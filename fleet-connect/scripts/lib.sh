#!/usr/bin/env bash
# lib.sh — shared config plumbing for the fleet-connect scripts. Sourced, not run.
#
# EVERY host-specific fact (which sessions exist, their labels, launch scripts, keep-alive
# jobs) comes from ONE json file — the fleet-connect config. Nothing here, and nothing in
# the sibling scripts, may hardcode a session name, label, path or launchd/systemd job.
# See reference/config.md for the schema.

# Hooks, launchd and systemd run with a minimal PATH — make tmux/jq resolve anyway.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

fc_config_path() {
  printf '%s' "${FLEET_CONNECT_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/fleet-connect/fleet.json}"
}

# Follow symlinks by hand: macOS has no `readlink -f` and the mini's config is expected to
# be a symlink INTO the fleet repo (so the fleet root is derivable from where it lands).
fc_resolve_link() {
  local p="$1" t
  while [ -L "$p" ]; do
    t="$(readlink "$p")"
    case "$t" in /*) p="$t" ;; *) p="$(dirname "$p")/$t" ;; esac
  done
  printf '%s' "$p"
}

fc_require_config() {
  FC_CONFIG="$(fc_config_path)"
  if [ ! -f "$FC_CONFIG" ]; then
    echo "fleet-connect: no config at $FC_CONFIG — run scripts/install.sh to create one." >&2
    return 1
  fi
  command -v jq >/dev/null 2>&1 || { echo "fleet-connect: jq not found." >&2; return 1; }
  jq -e . "$FC_CONFIG" >/dev/null 2>&1 || { echo "fleet-connect: $FC_CONFIG is not valid JSON." >&2; return 1; }
  FC_CONFIG_REAL="$(fc_resolve_link "$FC_CONFIG")"
  FC_CONFIG_DIR="$(cd "$(dirname "$FC_CONFIG_REAL")" && pwd -P)"
  export FC_CONFIG FC_CONFIG_REAL FC_CONFIG_DIR
}

fc_get() { jq -r "$1 // empty" "$FC_CONFIG"; }        # one scalar from the config
fc_fleet_root() {                                      # systemd hosts only
  local r; r="$(fc_get '.host.fleet_root')"
  [ -n "$r" ] || r="$FC_CONFIG_DIR"
  printf '%s' "${r/#\~/$HOME}"
}

# Host kind: explicit .host.keepalive wins; otherwise infer from the resolved config's
# neighbourhood — a config that lives inside a fleet repo (infra/channel-up.sh next to it)
# is a fleet host. That is what lets the mini symlink the fleet's own fleet.json verbatim.
fc_host_kind() {
  local k; k="$(fc_get '.host.keepalive')"
  if [ -z "$k" ]; then
    if [ -x "$(fc_fleet_root)/infra/channel-up.sh" ]; then k="systemd"; else k="launchd"; fi
  fi
  printf '%s' "$k"
}

fc_session_prefix() { fc_get '.host.session_prefix'; }

# The field separator fc_agents emits. NOT a tab: bash treats tab as IFS *whitespace*, so
# `IFS=$'\t' read` silently collapses a run of tabs into one delimiter and every empty field
# disappears, shifting the ones after it. US (0x1f) is not IFS whitespace, so empty fields
# survive. Readers must use `IFS="$FC_FS" read` (and cut/awk must be told the delimiter too).
FC_FS=$'\037'

# Emit one FC_FS-separated record per configured agent: name, session, label, launch script,
# keep-alive job, arm job, status. Defaults are merged in, so per-agent entries stay minimal.
# `status` is the registry's lifecycle field; a missing status means "live". Anything
# starting with `staged` is an agent that exists on paper but has no session yet — see
# fc_is_live. New columns go on the END: every reader reads by position.
fc_agents() {
  jq -j '
    (.defaults // {}) as $d
    | .agents[]?
    | ($d * .) as $a
    | [ ($a.name // ""), ($a.session // ""), ($a.channel.label // $a.name // ""),
        ($a.keepalive.script // ""), ($a.keepalive.job // ""), ($a.keepalive.arm_job // ""),
        ($a.status // "live") ]
    | join("\u001f") + "\n"' "$FC_CONFIG"
}

# True unless the status marks the agent as staged (not yet stood up on this host).
fc_is_live() {
  case "${1:-live}" in staged*) return 1 ;; *) return 0 ;; esac
}

fc_tmux_sessions() { tmux list-sessions -F '#{session_name}' 2>/dev/null || true; }

# The session this script is running inside, if any — never poke yourself.
# FC_SELF_SESSION overrides the tmux lookup; it exists so the self-skip can be exercised
# from a test harness that is not itself running inside the session under test.
fc_self_session() {
  if [ -n "${FC_SELF_SESSION:-}" ]; then printf '%s' "$FC_SELF_SESSION"; return 0; fi
  [ -n "${TMUX:-}" ] && tmux display-message -p '#{session_name}' 2>/dev/null || true
}

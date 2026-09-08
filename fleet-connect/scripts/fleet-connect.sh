#!/usr/bin/env bash
# fleet-connect.sh — the one entry point. Reads the host's fleet-connect config, works out
# which keep-alive mechanism this host uses, and dispatches. It contains no knowledge of any
# particular machine, agent, session or label: everything comes from the config.
#
#   ./fleet-connect.sh                 re-arm the channel on every agent of this host
#   ./fleet-connect.sh --dry-run       show the targets, send no keystrokes
#   ./fleet-connect.sh --restart       escalate: restart the runtime processes, then re-arm
#   ./fleet-connect.sh NAME...         limit to these agents (config name or session name)
#   ./fleet-connect.sh --where         print the config path, host kind and target list
#
# Config: $FLEET_CONNECT_CONFIG, else ${XDG_CONFIG_HOME:-~/.config}/fleet-connect/fleet.json
# (create it with scripts/install.sh). Schema: reference/config.md.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
fc_require_config || exit 1

MODE=connect; DRY=0; PASS=()
for a in "$@"; do
  case "$a" in
    --restart) MODE=restart ;;
    --where) MODE=where ;;
    --dry-run) DRY=1; PASS+=("$a") ;;
    *) PASS+=("$a") ;;
  esac
done

KIND="$(fc_host_kind)"
ROOT="$(fc_fleet_root)"

if [ "$MODE" = where ]; then
  echo "config:    $FC_CONFIG"
  [ "$FC_CONFIG_REAL" != "$FC_CONFIG" ] && echo "resolves:  $FC_CONFIG_REAL"
  echo "host kind: $KIND"
  [ "$KIND" = systemd ] && echo "fleet root: $ROOT"
  echo "agents:"
  fc_agents | awk -F"$FC_FS" '{printf "  %-24s session=%-26s label=%-20s status=%s\n", $1, $2, $3, $7}'
  self="$(fc_self_session)"
  [ -n "$self" ] && echo "self:      $self (excluded from a default run)"
  exit 0
fi

case "$KIND" in
  systemd)
    # A fleet host owns its own generic machinery — this skill only calls it.
    [ -x "$ROOT/infra/channel-up.sh" ] || { echo "fleet-connect: no infra/channel-up.sh under $ROOT" >&2; exit 1; }
    self="$(fc_self_session)"
    # An explicitly named agent may be given as its config name OR its session name, so the
    # self-check needs the name->session mapping, not just the session name.
    self_name=""
    if [ -n "$self" ]; then
      while IFS="$FC_FS" read -r n s _rest; do
        [ "$s" = "$self" ] && { self_name="$n"; break; }
      done < <(fc_agents)
    fi
    is_self() {  # $1 = a name the user typed; true if it resolves to the calling session
      [ -n "$self" ] || return 1
      [ "$1" = "$self" ] && return 0
      [ -n "$self_name" ] && [ "$1" = "$self_name" ]
    }

    names=(); asked=()
    if [ "${#PASS[@]}" -gt 0 ]; then
      for p in "${PASS[@]}"; do [ -n "$p" ] && [ "${p#-}" = "$p" ] && asked+=("$p"); done
    fi
    if [ "${#asked[@]}" -gt 0 ]; then
      # Explicit names bypass the status filter — the user asked for them by name — but
      # never the self-skip: typing /rc into your own composer is what we are avoiding.
      for p in "${asked[@]}"; do
        if is_self "$p"; then
          echo "fleet-connect: skipping '$p' — it is the session this script is running in." >&2
        else
          names+=("$p")
        fi
      done
    else
      # Default run: every live agent except this session. Staged agents have no session
      # yet, so channel-up would fail on them.
      while IFS="$FC_FS" read -r n s _label _script _job _arm status; do
        [ -n "$n" ] || continue
        fc_is_live "$status" || continue
        [ -n "$self" ] && [ "$s" = "$self" ] && continue
        names+=("$n")
      done < <(fc_agents)
    fi
    if [ "${#names[@]}" -eq 0 ]; then
      echo "fleet-connect: no target agents (all filtered out as staged or self)." >&2
      exit 0
    fi
    if [ "$DRY" -eq 1 ]; then
      printf 'would run: %s %s\n' "$ROOT/infra/channel-up.sh" "${names[*]}"
      [ "$MODE" = restart ] && printf 'preceded by: %s <name> for each\n' "$ROOT/infra/agent-restart.sh"
      exit 0
    fi
    rc=0
    if [ "$MODE" = restart ]; then
      for n in "${names[@]}"; do "$ROOT/infra/agent-restart.sh" "$n" || rc=1; done
    fi
    "$ROOT/infra/channel-up.sh" "${names[@]}" || rc=1
    exit $rc
    ;;
  launchd)
    script="$HERE/launchd-connect.sh"
    [ "$MODE" = restart ] && script="$HERE/launchd-restart.sh"
    if [ "${#PASS[@]}" -gt 0 ]; then exec "$script" "${PASS[@]}"; else exec "$script"; fi
    ;;
  *)
    echo "fleet-connect: unknown host.keepalive '$KIND' in $FC_CONFIG (expected launchd or systemd)." >&2
    exit 1
    ;;
esac

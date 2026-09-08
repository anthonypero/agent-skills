#!/usr/bin/env bash
# launchd-restart.sh — restart the persistent sessions of a launchd-managed host with
# `--continue`, so each runtime process RE-BINDS to the CURRENT machine login, then re-arm
# the keep-alive's arm jobs and verify the channel. Port of the old rc-restart.sh, with the
# session inventory moved OUT of the script and into the fleet-connect config.
#
# WHY THIS EXISTS (the account-switch failure mode): Remote Control binds to whatever
# account is active at ONE moment — process launch — and never re-initialises when the login
# changes underneath a live process. After switching the machine's `claude` login away and
# back, long-running sessions stay stranded on the old identity: the phone shows them
# "connected" but they don't respond, and an in-session `/rc` reports the WRONG account's
# policy. launchd-connect.sh (which only types `/rc`) CANNOT fix this — re-arming can't
# cross an account boundary. Only a process restart re-binds it.
#
#   ./launchd-restart.sh              restart every configured session + re-arm + verify
#   ./launchd-restart.sh --dry-run    show the resolved launch command per session, no keystrokes
#   ./launchd-restart.sh --full-resume  resume big sessions in full instead of from summary
#   ./launchd-restart.sh NAME...      only these agents (by config name or session name)
#
# Run it from OUTSIDE tmux so it never restarts itself. Targets must be idle at their prompt.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
fc_require_config || exit 1

DRY=0; FULL=0; ONLY=()
for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --full-resume) FULL=1 ;;
    -*) echo "unknown arg: $a" >&2; exit 2 ;;
    *) ONLY+=("$a") ;;
  esac
done
command -v tmux >/dev/null 2>&1 || { echo "tmux not found." >&2; exit 1; }
self="$(fc_self_session)"

wanted() {
  [ "${#ONLY[@]}" -eq 0 ] && return 0
  local x; for x in "${ONLY[@]}"; do [ "$x" = "$1" ] || [ "$x" = "$2" ] && return 0; done
  return 1
}

# Fallback for a session the config does not name: the `*-resume` alias in the shell rc file
# encodes both the session-up script and the `tmux attach -t <session>`.
upscript_from_alias() {
  local sess="$1" rc="${FLEET_CONNECT_ALIAS_FILE:-$HOME/.zshrc}"
  grep -hE "resume=.*tmux attach -t ${sess}\"" "$rc" 2>/dev/null | head -1 \
    | sed -nE 's/^[^"]*"[[:space:]]*([^"]*[^"[:space:]])[[:space:]]*&&.*/\1/p'
}

# The single source of truth for a session's launch command is its session-up script's
# LAUNCH= line — NOT the running process's args (a process can be running with wrong or
# partial flags; `ps` would faithfully relaunch the mistake — that is exactly how a session
# was found running without its --remote-control and 1M-context flags).
#
# Seed EVERY top-level assignment from the file, in file order — not just the interpreter
# path. A LAUNCH line may reference any sibling config var (e.g. "$CLAUDE_BIN …
# --remote-control $RC_LABEL"), and seeding one alone makes the eval die under `set -u`,
# returning an empty LAUNCH so the session is silently SKIPPED. Unindented matches only:
# config sits at the top of these scripts, so assignments inside functions are never picked
# up. `set +u` inside the subshell keeps a var referencing an unset env var from aborting.
launch_for() {
  local up="$1"
  [ -n "$up" ] && [ -f "$up" ] || return 1
  ( set +u
    eval "$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$up")" 2>/dev/null
    printf '%s' "${LAUNCH:-}" )
}

pane_child() {  # first child pid of a session's pane shell
  local pp; pp="$(tmux display-message -p -t "$1" '#{pane_pid}' 2>/dev/null || true)"
  [ -n "$pp" ] && pgrep -P "$pp" 2>/dev/null | head -1 || true
}

restart_one() {
  local s="$1" up="$2" launch cpid pane
  [ -n "$up" ] || up="$(upscript_from_alias "$s")"
  launch="$(launch_for "$up")" || true
  cpid="$(pane_child "$s")"

  if [ "$DRY" -eq 1 ]; then
    printf 'would restart %-26s up=%s\n' "$s" "${up:-<none>}"
    printf '   LAUNCH: %s\n' "${launch:-<COULD NOT RESOLVE — would skip>}"
    return 0
  fi
  if [ -z "$launch" ]; then echo "SKIP $s: could not resolve LAUNCH (keepalive.script in the config?)"; return 1; fi

  # --- quit: /exit, answer the "Exit anyway / Stay" confirm if tasks pend ---
  tmux send-keys -t "$s" -l "/exit"; sleep 1; tmux send-keys -t "$s" C-m; sleep 2
  pane="$(tmux capture-pane -t "$s" -p 2>/dev/null || true)"
  printf '%s' "$pane" | grep -q "Exit anyway" && tmux send-keys -t "$s" C-m
  local gone=0
  for _ in $(seq 1 15); do { [ -z "$cpid" ] || ! kill -0 "$cpid" 2>/dev/null; } && { gone=1; break; }; sleep 1; done
  [ "$gone" -eq 1 ] || { echo "SKIP $s: process ($cpid) still alive after /exit"; return 1; }

  # --- relaunch with --continue (noglob shields bracketed model ids like opus[1m]) ---
  tmux send-keys -t "$s" -l "noglob $launch --continue"; sleep 1; tmux send-keys -t "$s" C-m; sleep 2
  # Big sessions prompt: "Resume from summary / Resume full session". Default = summary.
  pane="$(tmux capture-pane -t "$s" -p 2>/dev/null || true)"
  if printf '%s' "$pane" | grep -q "Resume from summary"; then
    [ "$FULL" -eq 1 ] && tmux send-keys -t "$s" Down
    tmux send-keys -t "$s" C-m
  fi
  for _ in $(seq 1 25); do tmux capture-pane -t "$s" -p 2>/dev/null | grep -q "Context:" && break; sleep 1; done
  echo "restarted $s"
}

live="$(fc_tmux_sessions)"
PREFIX="${RC_PREFIX:-$(fc_session_prefix)}"
found=0; armjobs=""; seen=""

while IFS=$'\t' read -r name session _label script _job arm; do
  [ -n "$session" ] || continue
  seen="$seen $session"
  wanted "$name" "$session" || continue
  [ -n "$arm" ] && armjobs="$armjobs$arm"$'\n'
  printf '%s\n' "$live" | grep -qx "$session" || { echo "not running:   $session (config: $name)"; continue; }
  found=$((found + 1))
  [ "$session" = "$self" ] && { echo "skip (self): $session"; continue; }
  restart_one "$session" "${script/#\~/$HOME}"
done < <(fc_agents)

if [ -n "$PREFIX" ]; then
  while IFS= read -r s; do
    [ -n "$s" ] || continue
    case " $seen " in *" $s "*) continue ;; esac
    wanted "$s" "$s" || continue
    echo "unlisted:      $s   (add it to $FC_CONFIG)"
    found=$((found + 1))
    [ "$s" = "$self" ] && { echo "skip (self): $s"; continue; }
    restart_one "$s" ""
  done < <(printf '%s\n' "$live" | grep "^${PREFIX}" || true)
fi

[ "$found" -eq 0 ] && { echo "No live sessions to restart (config: $FC_CONFIG)."; exit 0; }
[ "$DRY" -eq 1 ] && exit 0

# Re-arm the in-session reminder timers: `--continue` restores the transcript but NOT live
# scheduled tasks (they are runtime state), so without this, reminders stay dead until the
# next daily arm job. Only agents that declare keepalive.arm_job are affected.
if [ -n "$armjobs" ]; then
  echo "--- re-arming reminder timers ---"
  uid="$(id -u)"
  printf '%s' "$armjobs" | sort -u | while read -r job; do
    [ -n "$job" ] || continue
    launchctl kickstart -k "gui/$uid/$job" >/dev/null 2>&1 && echo "re-armed: $job" || echo "arm FAILED: $job"
  done
fi

echo "--- verifying the channel ---"
if [ "${#ONLY[@]}" -gt 0 ]; then "$HERE/launchd-connect.sh" "${ONLY[@]}"; else "$HERE/launchd-connect.sh"; fi

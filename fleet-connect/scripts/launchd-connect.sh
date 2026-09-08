#!/usr/bin/env bash
# launchd-connect.sh — re-arm the Claude Remote Control channel on every persistent tmux
# session of a launchd-managed host (macOS). Port of the old rc-reconnect.sh, with the
# session inventory moved OUT of the script and into the fleet-connect config.
#
# WHY THIS EXISTS: the launchd keep-alive (*-session-up.sh) keeps each session + its claude
# process alive, and the arm jobs recreate the in-session check-in timers — but NOTHING
# re-establishes the Remote Control tunnel after a `/login` re-auth (every few weeks, when
# the token expires). RC goes dark for every running session and the phone can't see them.
# `/login` must be done by hand (browser auth — not automatable). This is the last step.
#
# Run it from a session that is NOT one of the managed ones — a fresh `claude` over SSH
# lives outside tmux and is the natural orchestrator. Targets must be idle at their prompt.
#
#   ./launchd-connect.sh              re-arm + verify every session in the config
#   ./launchd-connect.sh --dry-run    show what it WOULD arm (no keystrokes sent)
#   ./launchd-connect.sh NAME...      only these agents (by config name or session name)
set -uo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
fc_require_config || exit 1

DRY_RUN=0; ONLY=()
for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    -*) echo "unknown arg: $a" >&2; exit 2 ;;
    *) ONLY+=("$a") ;;
  esac
done
command -v tmux >/dev/null 2>&1 || { echo "tmux not found." >&2; exit 1; }

self="$(fc_self_session)"
PREFIX="${RC_PREFIX:-$(fc_session_prefix)}"

# Discover the RC label a session was LAUNCHED with (--remote-control <Name>), read straight
# from its claude process. Used only for sessions the config does not name — configured
# agents carry their label in the config.
label_from_process() {
  # All if-blocks (no trailing && chains): a short-circuited && list returns non-zero and,
  # under `set -e`, would abort the whole loop — that once silently dropped a session.
  local sess="$1" pane_pid="" pid="" cmd="" name=""
  pane_pid="$(tmux display-message -p -t "$sess" '#{pane_pid}' 2>/dev/null || true)"
  if [ -n "$pane_pid" ]; then
    pid="$(pgrep -P "$pane_pid" -f 'remote-control' 2>/dev/null | head -1 || true)"
    if [ -n "$pid" ]; then
      cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
      name="$(printf '%s' "$cmd" | sed -nE 's/.*--remote-control[ =]+([^ ]+).*/\1/p')"
    fi
  fi
  printf '%s' "$name"
}

wanted() {  # no name filter => everything
  [ "${#ONLY[@]}" -eq 0 ] && return 0
  local x; for x in "${ONLY[@]}"; do [ "$x" = "$1" ] || [ "$x" = "$2" ] && return 0; done
  return 1
}

# --- build the target list: configured agents first, then prefix-discovered strays -------
live="$(fc_tmux_sessions)"
targets=""; seen=""
while IFS="$FC_FS" read -r name session label _script _job _arm _status; do
  [ -n "$session" ] || continue
  seen="$seen $session"
  printf '%s\n' "$live" | grep -qx "$session" || { wanted "$name" "$session" && echo "not running:   $session (config: $name)"; continue; }
  wanted "$name" "$session" || continue
  targets="$targets$session	${label:-$name}"$'\n'
done < <(fc_agents)

if [ -n "$PREFIX" ]; then
  while IFS= read -r s; do
    [ -n "$s" ] || continue
    case " $seen " in *" $s "*) continue ;; esac
    wanted "$s" "$s" || continue
    l="$(label_from_process "$s")"; [ -n "$l" ] || l="${s#"$PREFIX"}"
    echo "unlisted:      $s -> /rc $l   (add it to $FC_CONFIG)"
    targets="$targets$s	$l"$'\n'
  done < <(printf '%s\n' "$live" | grep "^${PREFIX}" || true)
fi

armed=0; dark=0; found=0
while IFS=$'\t' read -r s name; do
  [ -n "$s" ] || continue
  found=$((found + 1))

  if [ "$DRY_RUN" -eq 1 ]; then
    if [ "$s" = "$self" ]; then echo "would skip (self): $s"; else echo "would arm:  $s  ->  /rc $name"; fi
    continue
  fi
  if [ "$s" = "$self" ]; then echo "skip (self):   $s"; continue; fi

  # Inject the command, then submit with a SEPARATE C-m — Claude's TUI treats a fast trailing
  # Enter as a newline (paste), so the carriage return must be sent on its own to actually
  # submit. (Same idiom the keep-alive *-session-up.sh scripts use.)
  tmux send-keys -t "$s" -l "/rc $name"
  sleep 1
  tmux send-keys -t "$s" C-m

  # Verify (wait up to ~30s — session creation can exceed 10s on slow/relayed networks).
  # Read the FOOTER STATUS LINE ONLY: a whole-pane grep also sees the transcript, so a
  # session that merely PRINTS a look-alike string self-reports as armed. Both the footer
  # format and the armed indicator are CLAUDE-VERSION-DEPENDENT (verified 2026-08-07 on two
  # live machines): new TUIs render "… | Context: 7% used | Effort: xhigh    /rc" (bare,
  # right-aligned "/rc"); older ones "Model: … | Context: 10% used  /rc active" with no
  # "Effort:" token. So locate the footer with the version-agnostic 'Context: (--|N% used)'
  # and accept all three indicator forms on THAT line.
  # BUT /rc on an ALREADY-armed session opens a management menu ("available in the Claude
  # mobile app") instead — that also means connected, so treat it as success and press Esc
  # to dismiss the menu (otherwise the session is left stuck on it).
  ok=0
  for _ in $(seq 1 30); do
    pane="$(tmux capture-pane -t "$s" -p 2>/dev/null || true)"
    if printf '%s' "$pane" | grep -q "available in the Claude mobile app"; then
      tmux send-keys -t "$s" Escape; ok=1; break
    fi
    footer="$(printf '%s' "$pane" | grep -E 'Context: (--|[0-9]+% used)' | tail -1 || true)"
    if printf '%s' "$footer" | grep -Eq '(^|[[:space:]])/rc[[:space:]]*$|Remote Control active|/rc active'; then ok=1; break; fi
    sleep 1
  done
  if [ "$ok" -eq 1 ]; then
    echo "re-armed OK:   $s  ->  /rc $name"
    armed=$((armed + 1))
  else
    echo "STILL DARK:    $s  ->  /rc $name   (wrong account? escalate: launchd-restart.sh)"
    dark=$((dark + 1))
  fi
done < <(printf '%s' "$targets")

if [ "$found" -eq 0 ]; then
  echo "No live sessions to arm (config: $FC_CONFIG${PREFIX:+, prefix '$PREFIX'})."
  exit 0
fi
[ "$DRY_RUN" -eq 1 ] && exit 0

echo
echo "Re-armed ${armed} session(s)${dark:+, ${dark} still dark}. Check the app on your phone."

#!/usr/bin/env bash
#
# ring.sh — play a loud attention sound ONCE, on demand, and show a popup that
# stays on screen until dismissed. The sound loops while the popup is up and
# stops the instant the user clicks Dismiss.
#
# The sound only starts looping once the popup is confirmed on screen. If no
# popup can be confirmed (Automation permission denied or still pending, no GUI
# session), the sound plays a bounded number of times instead and the script
# exits 3 — it never loops with nothing to dismiss it (2026-09-15, 2026-10-29).
#
# This is a one-off alert, not a hook. Nothing is installed and nothing persists;
# each run just plays the sound and raises the popup. Call it at the moment you
# hand control back with the thing the user was waiting for (see SKILL.md for when).
#
# Exit codes:
#   0    Dismiss clicked (or --no-popup / --dry-run finished)
#   1    the sound file or afplay is missing
#   2    usage error (unknown option, missing or non-numeric value)
#   3    no popup could be confirmed; the sound played --repeat times instead
#   4    the popup was up but ended without a Dismiss click (the dialog failed
#        or timed out, or osascript was killed); the sound was stopped
#   129, 130, 143  interrupted by SIGHUP, SIGINT, SIGTERM; sound and popup stopped
#
set -euo pipefail

usage() {
  cat <<'EOF'
ring.sh — loud one-off alert with a popup; the sound loops until Dismiss is clicked.

Usage: ring.sh [--message TEXT] [--volume N] [--max-seconds N]
               [--sound PATH] [--no-popup] [--repeat N] [--dry-run]

  --message TEXT    text shown in the popup (default: "Your turn — Claude Code needs you.")
  --volume N        afplay volume multiplier (1.0 = normal; default 4)
  --max-seconds N   safety cap: stop the sound after N seconds even if the popup
                    is still up (default 300; 0 = no cap). The popup stays up.
  --sound PATH      sound file to play (default: bundled comedy-horns.caf)
  --no-popup        sound only, no dialog: play --repeat times and exit
  --repeat N        number of plays with --no-popup, and in the fallback when no
                    popup can be confirmed (default 3). With a confirmed popup
                    the sound loops until Dismiss / --max-seconds.
  --dry-run         print what would happen, make no sound, show no dialog
  -h, --help        this help

Environment:
  RING_CONFIRM_SECONDS  seconds to wait for each popup route to be confirmed
                        (default 8; a value that is not a positive integer is
                        ignored with a warning)

Default behavior (popup on): the popup is raised first, through System Events so
it comes to the front; if System Events is not allowed (Automation permission
denied or still waiting on its consent prompt), a plain dialog is tried instead.
Only once a popup is confirmed up does the sound start looping; Dismiss stops it
and the script returns 0. If neither popup can be confirmed (up to about twice
RING_CONFIRM_SECONDS when both routes have to be tried), the sound plays --repeat
times, the reason is printed on stderr, and the script exits 3. No afplay, loop,
timer or dialog process is left behind.

Exit codes: 0 dismissed · 1 sound/afplay missing · 2 usage · 3 no popup, played
--repeat times · 4 popup ended without Dismiss (sound stopped) · 129/130/143
interrupted by SIGHUP/SIGINT/SIGTERM
EOF
}

die_usage() { echo "error: $1" >&2; exit 2; }

# Integers are normalized with 10#, so a leading zero ("08") is never read as octal.
is_uint() { case "${1:-}" in ''|*[!0-9]*) return 1 ;; esac; }
is_number() { [[ "${1:-}" =~ ^([0-9]+[.]?[0-9]*|[.][0-9]+)$ ]]; }

# Resolve own location so the bundled sound is found wherever this checkout lives.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SOUND="$SKILL_DIR/assets/comedy-horns.caf"
MESSAGE="Your turn — Claude Code needs you."
VOLUME="4"
REPEAT="3"
MAX_SECONDS="300"
POPUP="1"
DRY="0"
# Seconds to wait for a popup to be confirmed before giving up on that route.
# A pending Automation consent prompt blocks osascript indefinitely; this bounds it.
CONFIRM_DEFAULT=8
CONFIRM_SECONDS="${RING_CONFIRM_SECONDS:-$CONFIRM_DEFAULT}"
if is_uint "$CONFIRM_SECONDS" && [ "$((10#$CONFIRM_SECONDS))" -ge 1 ]; then
  CONFIRM_SECONDS=$((10#$CONFIRM_SECONDS))
else
  echo "ring: warning: RING_CONFIRM_SECONDS='$CONFIRM_SECONDS' is not a positive whole number of seconds; using $CONFIRM_DEFAULT." >&2
  CONFIRM_SECONDS=$CONFIRM_DEFAULT
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --message|--volume|--repeat|--max-seconds|--sound)
      [ $# -ge 2 ] || die_usage "$1 needs a value"
      case "$1" in
        --message)     MESSAGE="$2" ;;
        --volume)      VOLUME="$2" ;;
        --repeat)      REPEAT="$2" ;;
        --max-seconds) MAX_SECONDS="$2" ;;
        --sound)       SOUND="$2" ;;
      esac
      shift 2 ;;
    --no-popup)    POPUP="0"; shift ;;
    --dry-run)     DRY="1"; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) die_usage "unknown arg: $1" ;;
  esac
done

is_number "$VOLUME" || die_usage "--volume must be a number (e.g. 1.0 or 4), got '$VOLUME'"
{ is_uint "$REPEAT" && [ "$((10#$REPEAT))" -ge 1 ]; } || die_usage "--repeat must be a whole number of at least 1, got '$REPEAT'"
is_uint "$MAX_SECONDS" || die_usage "--max-seconds must be a whole number of seconds (0 = no cap), got '$MAX_SECONDS'"
REPEAT=$((10#$REPEAT))
MAX_SECONDS=$((10#$MAX_SECONDS))

# How long the dialog's Apple event may wait for Dismiss. Without a `with timeout`
# an Apple event gives up after 120 s: osascript would exit -1712, ending the
# sound well before --max-seconds, with the System Events dialog possibly left
# on screen. The cap plus a minute lets the sound run its full course first;
# with no cap, a week. (AppleScript integers stop at 536870911.)
DIALOG_TIMEOUT_MAX=604800
if [ "$MAX_SECONDS" -gt 0 ]; then DIALOG_TIMEOUT=$((MAX_SECONDS + 60)); else DIALOG_TIMEOUT=$DIALOG_TIMEOUT_MAX; fi
[ "$DIALOG_TIMEOUT" -le "$DIALOG_TIMEOUT_MAX" ] || DIALOG_TIMEOUT=$DIALOG_TIMEOUT_MAX

[ -f "$SOUND" ] || { echo "error: sound not found: $SOUND" >&2; exit 1; }

if [ "$DRY" = "1" ]; then
  if [ "$POPUP" = "1" ]; then
    echo "would show popup with a Dismiss button: $MESSAGE"
    if [ "$MAX_SECONDS" -gt 0 ]; then
      echo "once the popup is confirmed up, would loop sound (volume $VOLUME, stop on Dismiss or after ${MAX_SECONDS}s): $SOUND"
    else
      echo "once the popup is confirmed up, would loop sound (volume $VOLUME, stop on Dismiss; no time cap): $SOUND"
    fi
    echo "if no popup is confirmed within ${CONFIRM_SECONDS}s per route (System Events, then plain): would play x$REPEAT and exit 3"
  else
    echo "would play (x$REPEAT, volume $VOLUME): $SOUND"
    echo "would show no popup"
  fi
  exit 0
fi

command -v afplay >/dev/null 2>&1 || { echo "error: afplay not found (macOS only)" >&2; exit 1; }

# --- sound only -------------------------------------------------------------
if [ "$POPUP" = "0" ]; then
  i=0
  while [ "$i" -lt "$REPEAT" ]; do afplay -v "$VOLUME" "$SOUND" || true; i=$((i + 1)); done
  exit 0
fi
if ! command -v osascript >/dev/null 2>&1; then
  i=0
  while [ "$i" -lt "$REPEAT" ]; do afplay -v "$VOLUME" "$SOUND" || true; i=$((i + 1)); done
  echo "ring: osascript not found, so no popup was shown; played the sound $REPEAT times." >&2
  exit 3
fi

# --- popup first, then the looping sound ---------------------------------------
WORK="$(mktemp -d -t ring)"
READY="$WORK/ready"
PIDFILE="$WORK/afplay.pid"
DLG_ERR="$WORK/dialog.err"
LOOP_PID=""
DLG_PID=""
INTERRUPTED=""

stop_sound() {
  # Kill the loop first so it cannot spawn another afplay, then the live afplay.
  if [ -n "${LOOP_PID:-}" ]; then kill "$LOOP_PID" 2>/dev/null || true; fi
  local p
  p="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$p" ]; then kill "$p" 2>/dev/null || true; fi
}

cleanup() {
  local rc=$?
  # A second Ctrl-C must not cut the cleanup short and strand an afplay.
  trap '' INT TERM HUP
  # A dialog still up when the script is interrupted goes away with it.
  if [ -n "${DLG_PID:-}" ]; then kill "$DLG_PID" 2>/dev/null || true; fi
  stop_sound
  # Second sweep: covers the tiny window where the loop had just spawned a new
  # afplay between the kill of the loop and the read of the pid file.
  sleep 0.2
  stop_sound
  rm -f "$READY" "$PIDFILE" "$DLG_ERR" 2>/dev/null || true
  rmdir "$WORK" 2>/dev/null || true
  if [ -n "$INTERRUPTED" ]; then
    echo "ring: interrupted by SIG$INTERRUPTED; the sound and the popup were stopped." >&2
  fi
  exit "$rc"
}
# A signal ENDS the script (the old `trap cleanup INT` ran cleanup and then
# carried on, into reads of files cleanup had just deleted). The exit code is
# 128 + the signal number, so an interrupt never looks like a Dismiss.
on_signal() { INTERRUPTED="$1"; exit "$2"; }
trap cleanup EXIT
trap 'on_signal INT 130' INT
trap 'on_signal TERM 143' TERM
trap 'on_signal HUP 129' HUP

# The first lines of the last dialog's stderr, or nothing. Never fails, even
# when the file is gone.
dialog_error() { { head -c 300 "$DLG_ERR" 2>/dev/null || true; } | tr '\n' ' '; }

# Bounded plays for the no-popup fallback. Each afplay runs in the background and
# is waited for, so a signal is handled at once (bash defers a trap until a
# foreground child exits) and cleanup can kill the afplay through $PIDFILE.
play_bounded() {
  local i=0 af
  while [ "$i" -lt "$REPEAT" ]; do
    afplay -v "$VOLUME" "$SOUND" &
    af=$!
    printf '%s' "$af" > "$PIDFILE"
    wait "$af" || true
    i=$((i + 1))
  done
  rm -f "$PIDFILE"
}

# All scripts take the message, the ready-marker path and the dialog timeout as
# arguments, so the message needs no escaping. Each touches the marker only after
# the step that can block or be refused has passed, immediately before the
# dialog is drawn.
#
# Route 1: through System Events, activated first, so the dialog comes to the
# front instead of opening behind the window the user is looking at. The
# `count processes` call is a real Apple event: it is what trips the Automation
# check (-1743 when denied, an indefinite block while the consent prompt waits).
SE_SCRIPT='on run argv
  tell application "System Events" to count processes
  tell application "System Events" to activate
  do shell script "touch " & quoted form of (item 2 of argv)
  with timeout of ((item 3 of argv) as integer) seconds
    tell application "System Events" to display dialog (item 1 of argv) with title "Ring" buttons {"Dismiss"} default button 1 with icon caution
  end timeout
end run'
# Route 2: plain osascript dialog. Needs no Automation permission; `activate`
# brings osascript itself forward, though it can still land behind other windows.
PLAIN_SCRIPT='on run argv
  activate
  do shell script "touch " & quoted form of (item 2 of argv)
  with timeout of ((item 3 of argv) as integer) seconds
    display dialog (item 1 of argv) with title "Ring" buttons {"Dismiss"} default button 1 with icon caution
  end timeout
end run'

# Start a dialog in the background and wait until it is confirmed up.
# Returns 0 = up (marker written and osascript still alive a moment later),
#         1 = osascript exited (refused / failed; stderr is in $DLG_ERR),
#         2 = not confirmed within CONFIRM_SECONDS (blocked; it has been killed).
show_dialog() {
  rm -f "$READY"
  osascript -e "$1" "$MESSAGE" "$READY" "$DIALOG_TIMEOUT" >/dev/null 2>"$DLG_ERR" &
  DLG_PID=$!
  local ticks=$((CONFIRM_SECONDS * 5)) i=0
  while [ "$i" -lt "$ticks" ]; do
    if ! kill -0 "$DLG_PID" 2>/dev/null; then
      wait "$DLG_PID" 2>/dev/null || true; DLG_PID=""; return 1
    fi
    if [ -f "$READY" ]; then
      # Grace period: display dialog fails fast (e.g. -1713, no GUI session).
      sleep 0.6
      if kill -0 "$DLG_PID" 2>/dev/null; then return 0; fi
      wait "$DLG_PID" 2>/dev/null || true; DLG_PID=""; return 1
    fi
    sleep 0.2
    i=$((i + 1))
  done
  kill "$DLG_PID" 2>/dev/null || true
  wait "$DLG_PID" 2>/dev/null || true
  DLG_PID=""
  return 2
}

REASON=""
set +e
show_dialog "$SE_SCRIPT"; RC=$?
set -e
if [ "$RC" -ne 0 ]; then
  if [ "$RC" -eq 2 ]; then
    REASON="System Events did not answer within ${CONFIRM_SECONDS}s (likely an unanswered Automation prompt: allow it in System Settings > Privacy & Security > Automation)"
  else
    REASON="System Events dialog failed: $(dialog_error)"
  fi
  set +e
  show_dialog "$PLAIN_SCRIPT"; RC=$?
  set -e
  if [ "$RC" -ne 0 ]; then
    if [ "$RC" -eq 2 ]; then
      REASON="$REASON; plain dialog not confirmed within ${CONFIRM_SECONDS}s"
    else
      REASON="$REASON; plain dialog failed: $(dialog_error)"
    fi
    # No popup to dismiss, so never loop: a bounded number of plays, then report.
    play_bounded
    echo "ring: no popup could be shown, so the sound played $REPEAT times instead of looping. $REASON" >&2
    exit 3
  fi
  echo "ring: fell back to a plain dialog (it may open behind other windows). $REASON" >&2
fi

# The popup is up. Loop the sound in the background, recording each afplay's pid
# so it can be killed mid-play. `wait` returning non-zero means afplay was killed
# -> stop.
# The loop also guards itself, so it can't outlive this script (say ring.sh is
# SIGKILLed): before each play it stops if the parent is gone, and it keeps its
# own copy of the --max-seconds deadline.
RING_PARENT=$$
(
  trap - EXIT INT TERM HUP
  loop_deadline=""
  if [ "$MAX_SECONDS" -gt 0 ]; then loop_deadline=$((SECONDS + MAX_SECONDS)); fi
  while :; do
    kill -0 "$RING_PARENT" 2>/dev/null || break
    if [ -n "$loop_deadline" ] && [ "$SECONDS" -gt "$loop_deadline" ]; then break; fi
    afplay -v "$VOLUME" "$SOUND" &
    af=$!
    printf '%s' "$af" > "$PIDFILE"
    wait "$af" || break
  done
) 2>/dev/null &
LOOP_PID=$!
disown "$LOOP_PID" 2>/dev/null || true

# Wait for the dialog to end, enforcing the --max-seconds cap in this same loop
# (silence the sound, leave the dialog up). The cap used to be a background
# `sleep` timer that outlived every run; polling leaves nothing behind. The
# short foreground sleep also keeps a signal from waiting on anything long.
# $SECONDS counts whole seconds, so `-gt` makes the cap N to N+1 s, never short.
DEADLINE=""
if [ "$MAX_SECONDS" -gt 0 ]; then DEADLINE=$((SECONDS + MAX_SECONDS)); fi
while kill -0 "$DLG_PID" 2>/dev/null; do
  if [ -n "$DEADLINE" ] && [ "$SECONDS" -gt "$DEADLINE" ]; then
    stop_sound
    DEADLINE=""
  fi
  sleep 0.2
done
DLG_RC=0
wait "$DLG_PID" 2>/dev/null || DLG_RC=$?
DLG_PID=""

# osascript exits 0 only when the Dismiss button was clicked. Anything else (an
# Apple event timeout, the dialog process killed or crashing) is not a dismissal:
# say so, with a code of its own. cleanup stops the sound either way.
if [ "$DLG_RC" -ne 0 ]; then
  echo "ring: the popup ended without a Dismiss click (osascript exit $DLG_RC: $(dialog_error)); the sound was stopped. A System Events dialog may still be on screen." >&2
  exit 4
fi
exit 0

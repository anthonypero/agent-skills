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

Default behavior (popup on): the popup is raised first, through System Events so
it comes to the front; if System Events is not allowed (Automation permission
denied or still waiting on its consent prompt), a plain dialog is tried instead.
Only once a popup is confirmed up does the sound start looping; Dismiss stops it
and the script returns 0. If neither popup can be confirmed within a few
seconds, the sound plays --repeat times, the reason is printed on stderr, and
the script exits 3. No afplay, loop or dialog process is left behind.
EOF
}

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
CONFIRM_SECONDS="${RING_CONFIRM_SECONDS:-8}"

while [ $# -gt 0 ]; do
  case "$1" in
    --message)     MESSAGE="${2:-}"; shift 2 ;;
    --volume)      VOLUME="${2:-}"; shift 2 ;;
    --repeat)      REPEAT="${2:-}"; shift 2 ;;
    --max-seconds) MAX_SECONDS="${2:-}"; shift 2 ;;
    --sound)       SOUND="${2:-}"; shift 2 ;;
    --no-popup)    POPUP="0"; shift ;;
    --dry-run)     DRY="1"; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) echo "error: unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -f "$SOUND" ] || { echo "error: sound not found: $SOUND" >&2; exit 1; }

if [ "$DRY" = "1" ]; then
  if [ "$POPUP" = "1" ]; then
    echo "would show popup with a Dismiss button: $MESSAGE"
    if [ "$MAX_SECONDS" -gt 0 ] 2>/dev/null; then
      echo "once the popup is confirmed up, would loop sound (volume $VOLUME, stop on Dismiss or after ${MAX_SECONDS}s): $SOUND"
    else
      echo "once the popup is confirmed up, would loop sound (volume $VOLUME, stop on Dismiss; no time cap): $SOUND"
    fi
    echo "if no popup is confirmed within ${CONFIRM_SECONDS}s: would play x$REPEAT and exit 3"
  else
    echo "would play (x$REPEAT, volume $VOLUME): $SOUND"
    echo "would show no popup"
  fi
  exit 0
fi

command -v afplay >/dev/null 2>&1 || { echo "error: afplay not found (macOS only)" >&2; exit 1; }

play_bounded() {
  for _ in $(seq 1 "$REPEAT"); do afplay -v "$VOLUME" "$SOUND" || true; done
}

# --- sound only -------------------------------------------------------------
if [ "$POPUP" = "0" ]; then
  play_bounded
  exit 0
fi
if ! command -v osascript >/dev/null 2>&1; then
  play_bounded
  echo "ring: osascript not found, so no popup was shown; played the sound $REPEAT times." >&2
  exit 3
fi

# --- popup first, then the looping sound ---------------------------------------
WORK="$(mktemp -d -t ring)"
READY="$WORK/ready"
PIDFILE="$WORK/afplay.pid"
DLG_ERR="$WORK/dialog.err"
LOOP_PID=""
WATCH_PID=""
DLG_PID=""

stop_sound() {
  # Kill the loop first so it cannot spawn another afplay, then the live afplay.
  if [ -n "${LOOP_PID:-}" ]; then kill "$LOOP_PID" 2>/dev/null || true; fi
  local p
  p="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$p" ]; then kill "$p" 2>/dev/null || true; fi
}

cleanup() {
  if [ -n "${WATCH_PID:-}" ]; then kill "$WATCH_PID" 2>/dev/null || true; fi
  # A dialog still up when the script is interrupted goes away with it.
  if [ -n "${DLG_PID:-}" ]; then kill "$DLG_PID" 2>/dev/null || true; fi
  stop_sound
  # Second sweep: covers the tiny window where the loop had just spawned a new
  # afplay between the kill of the loop and the read of the pid file.
  sleep 0.2
  stop_sound
  rm -f "$READY" "$PIDFILE" "$DLG_ERR" 2>/dev/null || true
  rmdir "$WORK" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Both scripts take the message and the ready-marker path as arguments, so the
# message needs no escaping. Each touches the marker only after the step that can
# block or be refused has passed, immediately before the dialog is drawn.
#
# Route 1: through System Events, activated first, so the dialog comes to the
# front instead of opening behind the window the user is looking at. The
# `count processes` call is a real Apple event: it is what trips the Automation
# check (-1743 when denied, an indefinite block while the consent prompt waits).
SE_SCRIPT='on run argv
  tell application "System Events" to count processes
  tell application "System Events" to activate
  do shell script "touch " & quoted form of (item 2 of argv)
  tell application "System Events" to display dialog (item 1 of argv) with title "Ring" buttons {"Dismiss"} default button 1 with icon caution
end run'
# Route 2: plain osascript dialog. Needs no Automation permission; `activate`
# brings osascript itself forward, though it can still land behind other windows.
PLAIN_SCRIPT='on run argv
  activate
  do shell script "touch " & quoted form of (item 2 of argv)
  display dialog (item 1 of argv) with title "Ring" buttons {"Dismiss"} default button 1 with icon caution
end run'

# Start a dialog in the background and wait until it is confirmed up.
# Returns 0 = up (marker written and osascript still alive a moment later),
#         1 = osascript exited (refused / failed; stderr is in $DLG_ERR),
#         2 = not confirmed within CONFIRM_SECONDS (blocked; it has been killed).
show_dialog() {
  rm -f "$READY"
  osascript -e "$1" "$MESSAGE" "$READY" >/dev/null 2>"$DLG_ERR" &
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
    REASON="System Events dialog failed: $(head -c 300 "$DLG_ERR" 2>/dev/null | tr '\n' ' ')"
  fi
  set +e
  show_dialog "$PLAIN_SCRIPT"; RC=$?
  set -e
  if [ "$RC" -ne 0 ]; then
    if [ "$RC" -eq 2 ]; then
      REASON="$REASON; plain dialog not confirmed within ${CONFIRM_SECONDS}s"
    else
      REASON="$REASON; plain dialog failed: $(head -c 300 "$DLG_ERR" 2>/dev/null | tr '\n' ' ')"
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
(
  trap - EXIT INT TERM
  while :; do
    afplay -v "$VOLUME" "$SOUND" &
    af=$!
    printf '%s' "$af" > "$PIDFILE"
    wait "$af" || break
  done
) 2>/dev/null &
LOOP_PID=$!
disown "$LOOP_PID" 2>/dev/null || true

# Safety cap: silence after MAX_SECONDS, but leave the dialog up.
if [ "$MAX_SECONDS" -gt 0 ] 2>/dev/null; then
  ( trap - EXIT INT TERM; sleep "$MAX_SECONDS"; stop_sound ) &
  WATCH_PID=$!
  disown "$WATCH_PID" 2>/dev/null || true
fi

# Blocks until the user clicks Dismiss (or the dialog is closed / killed). If the
# dialog process dies for any reason, this returns and cleanup stops the sound.
wait "$DLG_PID" 2>/dev/null || true
DLG_PID=""

# trap cleanup stops the sound and reaps the background jobs.
exit 0

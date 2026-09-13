#!/usr/bin/env bash
#
# ring.sh — play a loud attention sound ONCE, on demand, and show a popup that
# stays on screen until dismissed. The sound loops while the popup is up and
# stops the instant the user clicks Dismiss.
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
  --repeat N        number of plays; ONLY meaningful with --no-popup (default 3).
                    With the popup, the sound loops until Dismiss / --max-seconds.
  --dry-run         print what would happen, make no sound, show no dialog
  -h, --help        this help

Default behavior (popup on): the sound loops in the background while a macOS
dialog with a single "Dismiss" button is up. Clicking Dismiss (or closing the
dialog) stops the sound immediately and the script returns. No afplay or loop
process is left behind.
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
    if [ "$MAX_SECONDS" -gt 0 ] 2>/dev/null; then
      echo "would loop sound (volume $VOLUME, stop on Dismiss or after ${MAX_SECONDS}s): $SOUND"
    else
      echo "would loop sound (volume $VOLUME, stop on Dismiss; no time cap): $SOUND"
    fi
    echo "would show popup with a Dismiss button: $MESSAGE"
  else
    echo "would play (x$REPEAT, volume $VOLUME): $SOUND"
    echo "would show no popup"
  fi
  exit 0
fi

command -v afplay >/dev/null 2>&1 || { echo "error: afplay not found (macOS only)" >&2; exit 1; }

# --- sound only -------------------------------------------------------------
if [ "$POPUP" = "0" ] || ! command -v osascript >/dev/null 2>&1; then
  for _ in $(seq 1 "$REPEAT"); do afplay -v "$VOLUME" "$SOUND"; done
  exit 0
fi

# --- popup + looping sound --------------------------------------------------
PIDFILE="$(mktemp -t ring.afplay)"
LOOP_PID=""
WATCH_PID=""

stop_sound() {
  # Kill the loop first so it cannot spawn another afplay, then the live afplay.
  if [ -n "${LOOP_PID:-}" ]; then kill "$LOOP_PID" 2>/dev/null || true; fi
  local p
  p="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [ -n "$p" ]; then kill "$p" 2>/dev/null || true; fi
}

cleanup() {
  if [ -n "${WATCH_PID:-}" ]; then kill "$WATCH_PID" 2>/dev/null || true; fi
  stop_sound
  # Second sweep: covers the tiny window where the loop had just spawned a new
  # afplay between the kill of the loop and the read of the pid file.
  sleep 0.2
  stop_sound
  rm -f "$PIDFILE" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Loop the sound in the background, recording each afplay's pid so it can be
# killed mid-play. `wait` returning non-zero means afplay was killed -> stop.
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

# Escape backslashes and double quotes for the AppleScript string literal.
ESCAPED="$(printf '%s' "$MESSAGE" | sed 's/\\/\\\\/g; s/"/\\"/g')"

DIALOG="display dialog \"$ESCAPED\" with title \"Ring\" buttons {\"Dismiss\"} default button 1 with icon caution"

# Blocks until the user clicks Dismiss (or the dialog is closed / killed).
# The dialog is raised *through System Events*, which is activated first: a plain
# `osascript ... display dialog` never becomes the frontmost app, so the dialog can
# end up behind whatever window the user is looking at — sound, but no popup.
set +e
DLG_ERR="$(osascript -e 'tell application "System Events" to activate' \
                     -e "tell application \"System Events\" to $DIALOG" 2>&1 >/dev/null)"
DLG_RC=$?
set -e

# If Automation permission for System Events is denied, fall back to a plain
# dialog — it may open behind other windows, but it is better than no popup.
if [ "$DLG_RC" -ne 0 ] && printf '%s' "$DLG_ERR" | grep -qiE '1743|not authorized|not allowed|assistive'; then
  osascript -e "$DIALOG" >/dev/null 2>&1 || true
fi

# trap cleanup stops the sound and reaps the background jobs.
exit 0

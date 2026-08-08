#!/usr/bin/env bash
#
# ring.sh — play a loud attention sound ONCE, on demand.
#
# This is a one-off alert, not a hook. Nothing is installed and nothing persists;
# each run just plays the sound. Call it at the moment you hand control back with
# the thing the user was waiting for (see SKILL.md for when).
#
# Usage: ring.sh [--volume N] [--repeat N] [--sound PATH] [--dry-run]
#   --volume N    afplay volume multiplier (1.0 = normal; default 4)
#   --repeat N    play N times in a row (default 3)
#   --sound PATH  sound file to play (default: bundled comedy-horns.caf)
#   --dry-run     print what would play, make no sound
#
set -euo pipefail

# Resolve own location so the bundled sound is found wherever this checkout lives.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SOUND="$SKILL_DIR/assets/comedy-horns.caf"
VOLUME="4"
REPEAT="3"
DRY="0"

while [ $# -gt 0 ]; do
  case "$1" in
    --volume)  VOLUME="${2:-}"; shift 2 ;;
    --repeat)  REPEAT="${2:-}"; shift 2 ;;
    --sound)   SOUND="${2:-}"; shift 2 ;;
    --dry-run) DRY="1"; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "error: unknown arg: $1" >&2; exit 2 ;;
  esac
done

[ -f "$SOUND" ] || { echo "error: sound not found: $SOUND" >&2; exit 1; }

if [ "$DRY" = "1" ]; then
  echo "would play (x$REPEAT, volume $VOLUME): $SOUND"
  exit 0
fi

command -v afplay >/dev/null 2>&1 || { echo "error: afplay not found (macOS only)" >&2; exit 1; }
for i in $(seq 1 "$REPEAT"); do afplay -v "$VOLUME" "$SOUND"; done

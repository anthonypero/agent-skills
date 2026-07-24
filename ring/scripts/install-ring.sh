#!/usr/bin/env bash
#
# install-ring.sh — install (or remove) the "ring" Stop hook.
#
# Plays a loud sound whenever Claude Code finishes a turn and hands control back,
# so you notice from across the room. Installs a Stop hook into a Claude Code
# settings file; the sound is copied to a stable per-machine location so the hook
# never depends on where this skill's checkout lives.
#
# Usage:
#   install-ring.sh [--scope global|project] [--volume N] [--repeat N]
#                   [--sound PATH] [--settings PATH] [--uninstall] [--print]
#
#   --scope global    Ring in every project on this machine  -> ~/.claude/settings.json  (default)
#   --scope project   Ring only in the current project        -> ./.claude/settings.local.json
#   --volume N        afplay volume multiplier (1.0 = normal; default 4)
#   --repeat N        Number of times to play in a row (default 3)
#   --sound PATH      Sound file to use (default: the bundled comedy-horns.caf)
#   --settings PATH   Write to this exact settings file (overrides --scope)
#   --uninstall       Remove the ring hook (keeps every other setting)
#   --print           Show what would be written, change nothing
#
set -euo pipefail

# --- resolve own location (portable across machines / through symlinks) ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SCOPE="global"
VOLUME="4"
REPEAT="3"
SOUND="$SKILL_DIR/assets/comedy-horns.caf"
SETTINGS=""
ACTION="install"
PRINT="0"

while [ $# -gt 0 ]; do
  case "$1" in
    --scope)     SCOPE="${2:-}"; shift 2 ;;
    --volume)    VOLUME="${2:-}"; shift 2 ;;
    --repeat)    REPEAT="${2:-}"; shift 2 ;;
    --sound)     SOUND="${2:-}"; shift 2 ;;
    --settings)  SETTINGS="${2:-}"; shift 2 ;;
    --uninstall) ACTION="uninstall"; shift ;;
    --print)     PRINT="1"; shift ;;
    -h|--help)   sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "error: unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- pick the settings file ---
if [ -z "$SETTINGS" ]; then
  case "$SCOPE" in
    global)  SETTINGS="$HOME/.claude/settings.json" ;;
    project) SETTINGS="$PWD/.claude/settings.local.json" ;;
    *) echo "error: --scope must be 'global' or 'project'" >&2; exit 2 ;;
  esac
fi

# --- stage the sound to a stable per-machine location (install only) ---
if [ "$ACTION" = "install" ]; then
  [ -f "$SOUND" ] || { echo "error: sound not found: $SOUND" >&2; exit 1; }
  SOUND_DIR="$HOME/.claude/sounds"
  mkdir -p "$SOUND_DIR"
  SOUND_DEST="$SOUND_DIR/$(basename "$SOUND")"
  # Copy only if different, so re-running is cheap and doesn't churn.
  if ! cmp -s "$SOUND" "$SOUND_DEST" 2>/dev/null; then cp "$SOUND" "$SOUND_DEST"; fi
else
  SOUND_DEST=""   # unused on uninstall
fi

# The marker comment lets us find & replace our own hook idempotently.
CMD="for i in \$(seq 1 $REPEAT); do afplay -v $VOLUME \"$SOUND_DEST\"; done 2>/dev/null || true # ring-skill"

mkdir -p "$(dirname "$SETTINGS")"

ACTION="$ACTION" PRINT="$PRINT" SETTINGS="$SETTINGS" CMD="$CMD" python3 - <<'PY'
import json, os, sys, shutil

path   = os.environ["SETTINGS"]
cmd    = os.environ["CMD"]
action = os.environ["ACTION"]
printk = os.environ["PRINT"] == "1"
MARK   = "# ring-skill"

try:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        print(f"error: {path} is not a JSON object", file=sys.stderr); sys.exit(1)
except FileNotFoundError:
    data = {}
except json.JSONDecodeError as e:
    print(f"error: {path} is not valid JSON ({e}); not touching it", file=sys.stderr); sys.exit(1)

hooks = data.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}
stop = hooks.get("Stop")
if not isinstance(stop, list):
    stop = []

# Strip any prior ring hook (identified by the marker), drop groups left empty.
new_stop = []
for group in stop:
    if not isinstance(group, dict):
        new_stop.append(group); continue
    inner = [h for h in group.get("hooks", [])
             if not (isinstance(h, dict) and isinstance(h.get("command"), str) and MARK in h["command"])]
    if inner:
        group = dict(group); group["hooks"] = inner
        new_stop.append(group)
stop = new_stop

if action == "install":
    stop.append({"hooks": [{"type": "command", "command": cmd, "async": True}]})

# Write hooks.Stop back, pruning empties so removal leaves a clean file.
if stop:
    hooks["Stop"] = stop
    data["hooks"] = hooks
else:
    hooks.pop("Stop", None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)

out = json.dumps(data, indent=2) + "\n"
if printk:
    print(out); sys.exit(0)

if os.path.exists(path):
    shutil.copy2(path, path + ".bak")   # safety net
with open(path, "w") as f:
    f.write(out)

print(f"{'installed' if action=='install' else 'removed'} ring hook -> {path}")
if os.path.exists(path + ".bak"):
    print(f"  (previous version backed up to {path}.bak)")
PY

if [ "$PRINT" = "0" ] && [ "$ACTION" = "install" ]; then
  echo "  sound: $SOUND_DEST  (volume $VOLUME, x$REPEAT)"
  echo "  Reload to activate: open /hooks once, or restart Claude Code."
fi

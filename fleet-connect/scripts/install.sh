#!/usr/bin/env bash
# install.sh — create this host's fleet-connect config. Run once per machine.
#
#   ./install.sh                 create the config from the template for this host's OS
#   ./install.sh --link <path>   symlink the config to an existing registry (a fleet repo's
#                                fleet.json), instead of writing a new file
#   ./install.sh --print         print the template that WOULD be written, to stdout
#
# The config is HOST-LOCAL state, not repo content: it names this machine's sessions, paths
# and keep-alive jobs. It never belongs in the skill's repo. See reference/config.md.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"

CONFIG="$(fc_config_path)"
LINK=""; PRINT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --link) LINK="${2:?--link needs a path}"; shift 2 ;;
    --print) PRINT=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

case "$(uname -s)" in
  Darwin) template="$HERE/../assets/fleet.launchd.example.json" ;;
  *)      template="$HERE/../assets/fleet.systemd.example.json" ;;
esac

if [ "$PRINT" -eq 1 ]; then cat "$template"; exit 0; fi

if [ -e "$CONFIG" ] || [ -L "$CONFIG" ]; then
  echo "fleet-connect: $CONFIG already exists — edit it, or move it aside first." >&2
  exit 1
fi

mkdir -p "$(dirname "$CONFIG")"
if [ -n "$LINK" ]; then
  [ -f "$LINK" ] || { echo "fleet-connect: no such registry: $LINK" >&2; exit 1; }
  ln -s "$LINK" "$CONFIG"
  echo "linked $CONFIG -> $LINK"
else
  cp "$template" "$CONFIG"
  echo "wrote $CONFIG from $(basename "$template")"
  echo "Edit it: one entry per persistent session on this host (see reference/config.md)."
fi
"$HERE/fleet-connect.sh" --where || true

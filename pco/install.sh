#!/bin/sh
# install.sh — put the `pco` CLI on PATH. Nothing is downloaded and nothing
# is installed beyond one symlink: the CLI is stdlib-only python3.
#
# Usage:  sh install.sh            symlinks bin/pco into ~/.local/bin
#         PCO_BIN_DIR=~/bin sh install.sh    (alternate target dir)
#
# Projects that consume the Python lib directly don't need this at all —
# they symlink the whole skill into .agents/skills/pco and import lib/.
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
BIN_DIR="${PCO_BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$BIN_DIR"
ln -sf "$HERE/bin/pco" "$BIN_DIR/pco"
echo "Linked $BIN_DIR/pco -> $HERE/bin/pco"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "NOTE: $BIN_DIR is not on your PATH — add it to your shell profile." ;;
esac

"$BIN_DIR/pco" --help >/dev/null && echo "pco CLI OK (try: pco whoami)"

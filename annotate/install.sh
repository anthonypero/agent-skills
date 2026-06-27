#!/bin/sh
# install.sh — annotate one-command bootstrap (tech-requirements §6.6, §8).
#
# Brings the whole stack up on this OS in ONE command, attempting everything
# automatically and degrading to a short, PROBED human checklist only for the few
# steps a given browser build / OS refuses (§6.6, §10 q4):
#
#   1. `npm install` the server/renderer/provisioner deps (the one shell step that must
#      precede the Node engine — markdown-it, highlight.js, @puppeteer/browsers, …)
#   2. delegate the rest to the Node setup engine (server/setup.js), which creates the
#      ~/.annotate tree (0700), locates/validates the extension, PROVISIONS a
#      --load-extension-honoring browser (a system Chromium if suitable, else downloads
#      Chrome for Testing), creates the dedicated profile, GENERATES runtime.json with
#      ABSOLUTE paths, starts the lazy-singleton server, and runs the LOAD PROBE
#      (launch with --load-extension; confirm the extension's /loaded heartbeat).
#
# The shell owns only the npm step (per §3: the shell delegates JSON-safe work to Node).
#
# Usage:   sh install.sh [--data-dir <dir>] [--port <n>] [--extension <dir>]
# Seams:   ANNOTATE_NODE (node binary), ANNOTATE_CFT / ANNOTATE_BROWSER_CACHE (reuse an
#          already-downloaded Chrome for Testing), ANNOTATE_HEADLESS, ANNOTATE_NO_DOWNLOAD,
#          ANNOTATE_PROBE_TIMEOUT — see server/setup.js.
#
# Exit:    0 = stack fully up (extension heartbeat confirmed); 2 = up but DEGRADED (a
#          probed manual checklist was printed); non-zero otherwise.
set -eu

PKG_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
NODE=${ANNOTATE_NODE:-node}

if ! command -v "$NODE" >/dev/null 2>&1; then
  echo "install.sh: Node.js is required but '$NODE' is not on PATH." >&2
  echo "install.sh: install Node (>=18), then re-run: sh install.sh" >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "install.sh: npm is required but not on PATH (it ships with Node)." >&2
  exit 1
fi

echo "install.sh: installing Node dependencies (npm install)…" >&2
( cd "$PKG_ROOT" && npm install --no-audit --no-fund )

echo "install.sh: provisioning the stack (browser, runtime.json, profile, load probe)…" >&2
exec "$NODE" "$PKG_ROOT/server/setup.js" install "$@"

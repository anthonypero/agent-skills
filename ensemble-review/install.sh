#!/bin/sh
# First-run setup for ensemble-review. Standard library only; nothing is installed.
#
#   ./install.sh                        # check python and the key, then seed the model registry
#   ./install.sh --workspace <project>  # seed that project's own registry instead of the package's
#   ./install.sh --dry-run              # show what the refresh would change, write nothing
#   ./install.sh --check-only           # python and the key; no catalogue call at all
#
# Three checks, in the order they can fail:
#
# 1. **python3, 3.10 or newer.** Every script here is standard-library Python 3 with no dependency
#    to install, so this is the whole runtime requirement.
# 2. **The OpenRouter key resolves**, through the same chain `dispatch.py` uses and no other: the
#    LastPass vault first (`skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY`), then
#    `$OPENROUTER_API_KEY` in the environment. This prints **which** path answered and never the
#    value — the skills repo is public, and a key echoed into a terminal is a key in a scrollback.
#    A connector whose config sets `api_key_secret: null` skips the vault leg, as it does at dispatch.
# 3. **The model registry is seeded.** `templates/models.json` holds, per concrete model id, what a
#    token costs, how much context it has, which effort strings it accepts and how many completion
#    tokens one seat spends on it. The cost pre-flight is unimplementable without it, and a resolved
#    seat the registry cannot price is a composition error that refuses the run.
#
# Idempotent: re-running changes nothing unless the catalogue has moved, and `refresh_models.py`
# says so and writes nothing when it has not. It never touches `output_token_prior`, which is
# measured from run manifests rather than from the catalogue.
#
# `$ENSEMBLE_REVIEW_CATALOGUE_URL` overrides the catalogue endpoint, which is how this script is
# tested without a network call.

set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=${ENSEMBLE_REVIEW_PYTHON:-python3}
WORKSPACE=""
DRY_RUN=""
CHECK_ONLY=""

fail() {
    printf 'install.sh: %s\n' "$1" >&2
    exit 1
}

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --workspace)
            [ $# -ge 2 ] || fail "--workspace needs a directory"
            WORKSPACE=$2
            shift 2
            ;;
        --workspace=*)
            WORKSPACE=${1#--workspace=}
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --check-only)
            CHECK_ONLY=1
            shift
            ;;
        -h | --help)
            usage
            ;;
        *)
            fail "unknown option $1 (try --help)"
            ;;
    esac
done

if [ -n "$WORKSPACE" ] && [ ! -d "$WORKSPACE" ]; then
    fail "no such workspace directory: $WORKSPACE"
fi

# --- 1. python3 ---------------------------------------------------------------------------------

command -v "$PY" >/dev/null 2>&1 || fail "no \`$PY\` on PATH. These scripts are standard-library Python 3; install Python 3.10 or newer."

VERSION=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null) ||
    fail "\`$PY\` is on PATH but would not report its version."

MAJOR=${VERSION%%.*}
MINOR=${VERSION#*.}
case "$MAJOR$MINOR" in
    *[!0-9]*) fail "\`$PY\` reported an unreadable version: $VERSION" ;;
esac
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]; }; then
    fail "python $VERSION is too old; ensemble-review needs 3.10 or newer."
fi
printf 'python:   %s (%s)\n' "$VERSION" "$(command -v "$PY")"

# --- 2. the OpenRouter key ------------------------------------------------------------------------

# Resolved by `dispatch.py` itself rather than by a copy of its chain here, so there is exactly one
# implementation of where a key comes from. Only the source is printed.
KEY_SOURCE=$(
    ENSEMBLE_REVIEW_SKILL_DIR="$SKILL_DIR" ENSEMBLE_REVIEW_WORKSPACE="$WORKSPACE" "$PY" - <<'PYTHON'
import os
import sys

sys.path.insert(0, os.path.join(os.environ["ENSEMBLE_REVIEW_SKILL_DIR"], "scripts"))

import dispatch
from lib import paths as paths_lib

workspace = os.environ.get("ENSEMBLE_REVIEW_WORKSPACE") or None
config, _path = dispatch.load_config(paths_lib.Paths(workspace))
_key, source = dispatch.resolve_api_key(config[dispatch.PROVIDER])
print(source)
PYTHON
) || fail "the OpenRouter key did not resolve. Both paths tried are named above; put the key in the vault (\`lp put global/OPENROUTER_API_KEY\`) or export \$OPENROUTER_API_KEY, then re-run."

printf 'key:      resolved from %s (value not shown)\n' "$KEY_SOURCE"

if [ -n "$CHECK_ONLY" ]; then
    printf 'registry: not touched (--check-only)\n'
    exit 0
fi

# --- 3. the model registry ------------------------------------------------------------------------

set -- "$SKILL_DIR/scripts/refresh_models.py"
[ -n "$WORKSPACE" ] && set -- "$@" --workspace "$WORKSPACE"
[ -n "$DRY_RUN" ] && set -- "$@" --dry-run
[ -n "${ENSEMBLE_REVIEW_CATALOGUE_URL:-}" ] && set -- "$@" --url "$ENSEMBLE_REVIEW_CATALOGUE_URL"

printf 'registry: refreshing from the catalogue\n'
"$PY" "$@" || fail "the model registry was not refreshed; the registry file was left untouched. The cost pre-flight cannot project a seat it has no price for, so a run would refuse before dispatch."

printf '\nready. Next: python3 %s/scripts/run_panel.py --artifact <doc> --ref <source> --out <run-dir>\n' "$SKILL_DIR"

#!/bin/sh
# First-run setup for panel-review. Standard library only; nothing is installed.
#
#   ./install.sh                        # check python and the key, install the judge, seed the registry
#   ./install.sh --workspace <project>  # seed that project's own registry instead of the package's
#   ./install.sh --dry-run              # show what the refresh would change, write nothing
#   ./install.sh --check-only           # python, the key and the judge's state; no catalogue call
#
# Five steps, in the order they can fail:
#
# 1. **python3, 3.10 or newer.** Every script here is standard-library Python 3 with no dependency
#    to install, so this is the whole runtime requirement.
# 2. **The OpenRouter key resolves**, through the same chain `dispatch.py` uses and no other: the
#    LastPass vault first (`skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY`), then
#    `$OPENROUTER_API_KEY` in the environment. This prints **which** path answered and never the
#    value — the skills repo is public, and a key echoed into a terminal is a key in a scrollback.
#    A connector file that sets `api_key_secret: null` skips the vault leg, as it does at dispatch.
#    Which endpoint is asked about comes from `config.json`'s `default_connector` and the connector
#    file it names, so a machine pointed at a private endpoint checks that endpoint's own key.
# 3. **The harness judge is installed.** `agents/judge.md` is copied to
#    `~/.claude/agents/panel-judge.md`, which is what makes it spawnable by name and what makes
#    `harness-judge` the default judgment supplier on an unattended run. It is a copy rather than a
#    symlink so the installed agent does not change under a running session when the package moves,
#    and it is idempotent: an identical file already there is left alone and the script says so.
#    `--check-only` and `--dry-run` both report whether it is there and install nothing.
#    `$PANEL_REVIEW_HARNESS_AGENTS_DIR` overrides the directory, which is how this is tested.
# 4. **The user tier is reported.** `~/.config/panel-review/` (or `$XDG_CONFIG_HOME/panel-review/`)
#    is the middle root of the resolution cascade: per-owner choices — a connector this machine
#    trusts, one model's effort rung, a key source — live there once instead of once per project.
#    It is optional, so this reports whether it exists and what it holds and creates nothing: a
#    directory this script made on its own would change which files a run resolves without anybody
#    asking for it.
# 5. **The model registry is seeded.** `templates/models/<slug>.json` holds, per concrete model id,
#    what a token costs, how much context it has, which effort strings it accepts, which abstract
#    effort level maps to which of them, and how many completion tokens one seat spends on it. The
#    cost pre-flight is unimplementable without it, and a resolved seat the registry cannot price is
#    a composition error that refuses the run.
#
# Idempotent: re-running changes nothing unless the catalogue has moved, and `refresh_models.py`
# says so and writes nothing when it has not. It never touches `output_token_prior`, which is
# measured from run manifests rather than from the catalogue.
#
# `$PANEL_REVIEW_CATALOGUE_URL` overrides the catalogue endpoint, which is how this script is
# tested without a network call.

set -eu

SKILL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY=${PANEL_REVIEW_PYTHON:-python3}
WORKSPACE=""
DRY_RUN=""
CHECK_ONLY=""

fail() {
    printf 'install.sh: %s\n' "$1" >&2
    exit 1
}

usage() {
    sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'
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
    fail "python $VERSION is too old; panel-review needs 3.10 or newer."
fi
printf 'python:   %s (%s)\n' "$VERSION" "$(command -v "$PY")"

# --- 2. the OpenRouter key ------------------------------------------------------------------------

# Resolved by `dispatch.py` itself rather than by a copy of its chain here, so there is exactly one
# implementation of where a key comes from. Only the source is printed.
KEY_SOURCE=$(
    PANEL_REVIEW_SKILL_DIR="$SKILL_DIR" PANEL_REVIEW_WORKSPACE="$WORKSPACE" "$PY" - <<'PYTHON'
import os
import sys

sys.path.insert(0, os.path.join(os.environ["PANEL_REVIEW_SKILL_DIR"], "scripts"))

import dispatch
from lib import paths as paths_lib

workspace = os.environ.get("PANEL_REVIEW_WORKSPACE") or None
paths = paths_lib.Paths(workspace)
config, config_path = dispatch.load_config(paths)
connector, _connector_path = dispatch.resolve_connector(paths, config, config_path)
_key, source = dispatch.resolve_api_key(connector)
print(source)
PYTHON
) || fail "the OpenRouter key did not resolve. Both paths tried are named above; put the key in the vault (\`lp put global/OPENROUTER_API_KEY\`) or export \$OPENROUTER_API_KEY, then re-run."

printf 'key:      resolved from %s (value not shown)\n' "$KEY_SOURCE"

# --- 3. the harness judge -------------------------------------------------------------------------

# The judgment supplier for an unattended run when a harness is present: a fixed, named agent on
# frontier Claude, on a family no seat holds. Owner ruling, 2026-09-19. Its presence at this exact
# path is also the signal `lib/judge.py` reads to decide whether a harness is there at all, which is
# why the install and the detection name one path between them.
JUDGE_SRC="$SKILL_DIR/agents/judge.md"
AGENTS_DIR=${PANEL_REVIEW_HARNESS_AGENTS_DIR:-${HOME:-}/.claude/agents}
JUDGE_DEST="$AGENTS_DIR/panel-judge.md"

[ -f "$JUDGE_SRC" ] || fail "the package is missing agents/judge.md; the harness judge cannot be installed."

# `--check-only` and `--dry-run` both report and write nothing. They mean different things
# elsewhere — one skips the catalogue call, the other makes it and discards the diff — and they
# mean the same thing here, because "write nothing" is what a dry run is for and installing an
# agent behind that flag is the surprise the flag exists to prevent.
if [ -n "$CHECK_ONLY" ] || [ -n "$DRY_RUN" ]; then
    WHY="--check-only"
    [ -n "$DRY_RUN" ] && WHY="--dry-run"
    if [ -f "$JUDGE_DEST" ] && cmp -s "$JUDGE_SRC" "$JUDGE_DEST"; then
        printf 'judge:    installed at %s (not touched, %s)\n' "$JUDGE_DEST" "$WHY"
    elif [ -f "$JUDGE_DEST" ]; then
        printf 'judge:    out of date at %s — would be replaced (nothing written, %s)\n' "$JUDGE_DEST" "$WHY"
    else
        printf 'judge:    NOT installed at %s — would be installed (nothing written, %s)\n' "$JUDGE_DEST" "$WHY"
    fi
fi

# The real install. Skipped under both report-only flags — `--check-only` reports the user tier and
# the registry's state below and must not write on the way there, which is the whole contract of
# the flag.
if [ -z "$DRY_RUN" ] && [ -z "$CHECK_ONLY" ]; then
    if [ -f "$JUDGE_DEST" ] && cmp -s "$JUDGE_SRC" "$JUDGE_DEST"; then
        printf 'judge:    already installed at %s (nothing moved)\n' "$JUDGE_DEST"
    else
        mkdir -p "$AGENTS_DIR" || fail "could not create the harness agents directory at $AGENTS_DIR"
        cp "$JUDGE_SRC" "$JUDGE_DEST" || fail "could not install the harness judge to $JUDGE_DEST"
        printf 'judge:    installed panel-judge at %s\n' "$JUDGE_DEST"
    fi
fi

# --- 4. the user tier -------------------------------------------------------------------------------

# Reported, never created. The cascade's middle root is optional, and a directory this script made
# on its own would change which files a run resolves without anybody having asked for it.
USER_ROOT=$(
    PANEL_REVIEW_SKILL_DIR="$SKILL_DIR" "$PY" - <<'PYTHON'
import os
import sys

sys.path.insert(0, os.path.join(os.environ["PANEL_REVIEW_SKILL_DIR"], "scripts"))

from lib import paths as paths_lib

root = paths_lib.user_root()
if not os.path.isdir(root):
    print("{0}\tabsent".format(root))
else:
    held = [name for name in ("config.json", "connectors", "models", "panels", "agents",
                              "references", "schemas", "backends")
            if os.path.exists(os.path.join(root, name))]
    print("{0}\t{1}".format(root, ", ".join(held) or "empty"))
PYTHON
) || fail "could not read the user tier's location."

USER_ROOT_PATH=${USER_ROOT%%	*}
USER_ROOT_STATE=${USER_ROOT#*	}
if [ "$USER_ROOT_STATE" = "absent" ]; then
    printf 'user:     no user tier at %s (optional; the cascade falls through to the package)\n' "$USER_ROOT_PATH"
else
    printf 'user:     %s holds %s\n' "$USER_ROOT_PATH" "$USER_ROOT_STATE"
fi

# --- 5. the model registry ------------------------------------------------------------------------

set -- "$SKILL_DIR/scripts/refresh_models.py"
[ -n "$WORKSPACE" ] && set -- "$@" --workspace "$WORKSPACE"
[ -n "$DRY_RUN" ] && set -- "$@" --dry-run
[ -n "${PANEL_REVIEW_CATALOGUE_URL:-}" ] && set -- "$@" --url "$PANEL_REVIEW_CATALOGUE_URL"

if [ -n "$CHECK_ONLY" ]; then
    printf 'registry: not touched (--check-only)\n'
    exit 0
fi

printf 'registry: refreshing from the catalogue\n'
"$PY" "$@" || fail "the model registry was not refreshed; the registry file was left untouched. The cost pre-flight cannot project a seat it has no price for, so a run would refuse before dispatch."

printf '\nready. Next: python3 %s/scripts/run_panel.py --artifact <doc> --ref <source> --out <run-dir>\n' "$SKILL_DIR"

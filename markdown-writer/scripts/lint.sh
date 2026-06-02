#!/bin/bash

# Resolve the skill directory relative to THIS script, so it works regardless of
# where the skill is checked out or symlinked from. (Previously hardcoded to a
# path that did not exist on every machine.)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
MD_CONFIG_SRC="$SKILL_DIR/assets/.markdownlint.json"
PRETTIER_CONFIG_SRC="$SKILL_DIR/assets/.prettierrc.json"

FILE_PATH="$1"
PROJECT_ROOT="$2"

# Validation
if [ -z "$PROJECT_ROOT" ]; then
    echo "Error: PROJECT_ROOT argument is required."
    echo "Usage: lint.sh <file_path> <project_root>"
    exit 1
fi

# 1. Ensure tooling is installed in the project root
if [ ! -f "$PROJECT_ROOT/node_modules/.bin/markdownlint-cli2" ]; then
    echo "Installing markdownlint-cli2 in $PROJECT_ROOT..."
    (cd "$PROJECT_ROOT" && npm install markdownlint-cli2 --save-dev --silent)
fi

if [ ! -f "$PROJECT_ROOT/node_modules/.bin/prettier" ]; then
    echo "Installing prettier in $PROJECT_ROOT..."
    (cd "$PROJECT_ROOT" && npm install prettier --save-dev --silent)
fi

MARKDOWNLINT="$PROJECT_ROOT/node_modules/.bin/markdownlint-cli2"
PRETTIER="$PROJECT_ROOT/node_modules/.bin/prettier"

# 2. Seed default configs into the project root ONLY if it has none of its own.
#    Never overwrite an existing config — check every recognized filename and
#    extension so we don't, e.g., drop a .json next to an author's .jsonc and
#    shadow it.
has_config() {
    # $1 = PROJECT_ROOT, remaining args = candidate config filenames
    local root="$1"; shift
    local name
    for name in "$@"; do
        [ -f "$root/$name" ] && return 0
    done
    return 1
}

if ! has_config "$PROJECT_ROOT" \
        .markdownlint.json .markdownlint.jsonc .markdownlint.yaml .markdownlint.yml \
        .markdownlint.cjs .markdownlint.mjs .markdownlint-cli2.jsonc \
        .markdownlint-cli2.yaml .markdownlint-cli2.cjs .markdownlint-cli2.mjs; then
    if [ -f "$MD_CONFIG_SRC" ]; then
        echo "Seeding default .markdownlint.json into project..."
        cp "$MD_CONFIG_SRC" "$PROJECT_ROOT/.markdownlint.json"
    else
        echo "Warning: default markdownlint config not found at $MD_CONFIG_SRC"
    fi
fi

if ! has_config "$PROJECT_ROOT" \
        .prettierrc .prettierrc.json .prettierrc.jsonc .prettierrc.json5 \
        .prettierrc.yaml .prettierrc.yml .prettierrc.toml .prettierrc.js \
        .prettierrc.cjs .prettierrc.mjs prettier.config.js prettier.config.cjs \
        prettier.config.mjs; then
    if [ -f "$PRETTIER_CONFIG_SRC" ]; then
        echo "Seeding default .prettierrc.json into project..."
        cp "$PRETTIER_CONFIG_SRC" "$PROJECT_ROOT/.prettierrc.json"
    else
        echo "Warning: default prettier config not found at $PRETTIER_CONFIG_SRC"
    fi
fi

# 3. Auto-format first, then lint.
#    Prettier handles what markdownlint --fix cannot (notably table column
#    alignment / MD060); markdownlint --fix then catches the remaining rules.
echo "Formatting with Prettier..."
"$PRETTIER" --write "$FILE_PATH"

echo "Linting with markdownlint-cli2..."
"$MARKDOWNLINT" --fix "$FILE_PATH"

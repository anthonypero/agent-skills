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

# 1. Resolve tooling WITHOUT installing anything. Projects may forbid adding
#    dependencies, so never `npm install` into them. Prefer a project-local
#    binary if the project already has one, else fall back to whatever is on
#    PATH (e.g. Homebrew). If neither exists, stop and tell the user.
resolve_tool() {
    # $1 = tool name
    if [ -x "$PROJECT_ROOT/node_modules/.bin/$1" ]; then
        echo "$PROJECT_ROOT/node_modules/.bin/$1"
    elif command -v "$1" >/dev/null 2>&1; then
        command -v "$1"
    fi
}

MARKDOWNLINT="$(resolve_tool markdownlint-cli2)"
PRETTIER="$(resolve_tool prettier)"

if [ -z "$MARKDOWNLINT" ] || [ -z "$PRETTIER" ]; then
    echo "Error: prettier and/or markdownlint-cli2 not found (project node_modules or PATH)."
    echo "This script does not install tools. Ask the user to install them, e.g.:"
    echo "  brew install prettier markdownlint-cli2"
    exit 1
fi

# 2. Use the project's own configs when it has them; otherwise pass the
#    skill's defaults explicitly. Never write config files into the project.
#    Check every recognized filename and extension.
has_config() {
    # $1 = PROJECT_ROOT, remaining args = candidate config filenames
    local root="$1"; shift
    local name
    for name in "$@"; do
        [ -f "$root/$name" ] && return 0
    done
    return 1
}

MD_ARGS=()
if ! has_config "$PROJECT_ROOT" \
        .markdownlint.json .markdownlint.jsonc .markdownlint.yaml .markdownlint.yml \
        .markdownlint.cjs .markdownlint.mjs .markdownlint-cli2.jsonc \
        .markdownlint-cli2.yaml .markdownlint-cli2.cjs .markdownlint-cli2.mjs; then
    MD_ARGS=(--config "$MD_CONFIG_SRC")
fi

PRETTIER_ARGS=()
if ! has_config "$PROJECT_ROOT" \
        .prettierrc .prettierrc.json .prettierrc.jsonc .prettierrc.json5 \
        .prettierrc.yaml .prettierrc.yml .prettierrc.toml .prettierrc.js \
        .prettierrc.cjs .prettierrc.mjs prettier.config.js prettier.config.cjs \
        prettier.config.mjs; then
    PRETTIER_ARGS=(--config "$PRETTIER_CONFIG_SRC")
fi

# 3. Auto-format first, then lint.
#    Prettier handles what markdownlint --fix cannot (notably table column
#    alignment / MD060); markdownlint --fix then catches the remaining rules.
echo "Formatting with Prettier..."
"$PRETTIER" "${PRETTIER_ARGS[@]}" --write "$FILE_PATH"

echo "Linting with markdownlint-cli2..."
"$MARKDOWNLINT" "${MD_ARGS[@]}" --fix "$FILE_PATH"

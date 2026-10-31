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
if [ ! -d "$PROJECT_ROOT" ]; then
    echo "Error: PROJECT_ROOT is not a directory: $PROJECT_ROOT"
    exit 1
fi
if [ ! -f "$FILE_PATH" ]; then
    echo "Error: file not found: $FILE_PATH"
    exit 1
fi
# Absolute paths, because the tools run from inside PROJECT_ROOT (step 3).
FILE_PATH="$(cd "$(dirname "$FILE_PATH")" && pwd)/$(basename "$FILE_PATH")"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"

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
#
#    Run from PROJECT_ROOT, and give Prettier the project's .prettierignore
#    explicitly. By default Prettier 3 reads .gitignore and .prettierignore from
#    the *cwd* and silently skips a matching file with exit 0. On 2026-10-06 a
#    session in ~/Projects/draftmark linted a note under ~/Projects/imperion/universe/;
#    draftmark's `.gitignore` line `universe/` matched it, so Prettier did nothing
#    and markdownlint raised 9 MD060 table errors. .gitignore is deliberately not
#    consulted: a gitignored note still deserves formatting when it is asked for.
cd "$PROJECT_ROOT" || exit 1
PRETTIER_ARGS+=(--ignore-path "$PROJECT_ROOT/.prettierignore")
FAILED=0

if "$PRETTIER" "${PRETTIER_ARGS[@]}" --file-info "$FILE_PATH" | grep -q '"ignored": true'; then
    echo "Error: Prettier will NOT format $FILE_PATH: it matches $PROJECT_ROOT/.prettierignore."
    echo "Nothing was reformatted (tables are not aligned). Format it by hand, or ask the user about the ignore entry."
    FAILED=1
else
    echo "Formatting with Prettier..."
    "$PRETTIER" "${PRETTIER_ARGS[@]}" --write "$FILE_PATH" || FAILED=1
    if [ "$FAILED" = 0 ] && ! "$PRETTIER" "${PRETTIER_ARGS[@]}" --check "$FILE_PATH" >/dev/null 2>&1; then
        echo "Error: Prettier ran but $FILE_PATH still does not pass prettier --check."
        FAILED=1
    fi
fi

#    markdownlint-cli2 reads every argument as a glob, so a filename holding glob
#    syntax — `notes (v2).md`, `[draft].md` — matched nothing and linted 0 files.
#    A leading `:` marks a literal file path (`markdownlint-cli2 --help`); it skips
#    glob matching but still honors the config's `ignores` and negated `globs`, so
#    with it, "0 files" can only mean the config excluded this file.
echo "Linting with markdownlint-cli2..."
MD_OUT="$("$MARKDOWNLINT" "${MD_ARGS[@]}" --fix ":$FILE_PATH" 2>&1)" || FAILED=1
printf '%s\n' "$MD_OUT"
if printf '%s' "$MD_OUT" | grep -q 'Linting: 0 file(s)'; then
    # Name the config that did it: a markdownlint-cli2 options file in
    # PROJECT_ROOT or in a directory between it and the file. Claim exclusion
    # only when one exists; otherwise say plainly that the cause is unknown.
    MD_CLI2_CONFIGS=()
    dir="$(dirname "$FILE_PATH")"
    case "$dir" in "$PROJECT_ROOT"|"$PROJECT_ROOT"/*) ;; *) dir="$PROJECT_ROOT" ;; esac
    while :; do
        for name in .markdownlint-cli2.jsonc .markdownlint-cli2.yaml .markdownlint-cli2.cjs .markdownlint-cli2.mjs; do
            if [ -f "$dir/$name" ]; then MD_CLI2_CONFIGS+=("$dir/$name"); fi
        done
        if [ "$dir" = "$PROJECT_ROOT" ]; then break; fi
        dir="$(dirname "$dir")"
    done
    if [ "${#MD_CLI2_CONFIGS[@]}" -gt 0 ]; then
        echo "Error: markdownlint-cli2 linted nothing: $FILE_PATH is excluded by the \`ignores\` or a negated \`globs\` entry in ${MD_CLI2_CONFIGS[*]}."
    else
        echo "Error: markdownlint-cli2 linted nothing for $FILE_PATH, and no .markdownlint-cli2.* config was found that would exclude it. Run markdownlint-cli2 on it by hand to see why."
    fi
    FAILED=1
fi

exit "$FAILED"

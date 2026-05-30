#!/bin/bash

# Define paths
SKILL_DIR="$HOME/Projects/custom-skills/skills/markdown-writer"
CONFIG_SRC="$SKILL_DIR/assets/.markdownlint.json"
CONFIG_DEST=".markdownlint.json"
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

# 2. Check for a markdownlint config (CWD first); seed the default if absent
if [ ! -f "$CONFIG_DEST" ]; then
    echo "Initializing .markdownlint.json..."
    if [ -f "$CONFIG_SRC" ]; then
        cp "$CONFIG_SRC" "$CONFIG_DEST"
    else
        echo "Warning: Standard config not found at $CONFIG_SRC"
    fi
fi

# 3. Auto-format first, then lint.
#    Prettier handles what markdownlint --fix cannot (notably table column
#    alignment / MD060); markdownlint --fix then catches the remaining rules.
echo "Formatting with Prettier..."
"$PRETTIER" --write "$FILE_PATH"

echo "Linting with markdownlint-cli2..."
"$MARKDOWNLINT" --fix "$FILE_PATH"

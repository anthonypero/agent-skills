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

# 1. Check for tool installation in project root
LOCAL_BIN="$PROJECT_ROOT/node_modules/.bin/markdownlint-cli2"

if [ ! -f "$LOCAL_BIN" ]; then
    echo "Installing markdownlint-cli2 in $PROJECT_ROOT..."
    (cd "$PROJECT_ROOT" && npm install markdownlint-cli2 --save-dev --silent)
fi

# 2. Check for configuration file
# We check CWD first.
if [ ! -f "$CONFIG_DEST" ]; then
    echo "Initializing .markdownlint.json..."
    if [ -f "$CONFIG_SRC" ]; then
        cp "$CONFIG_SRC" "$CONFIG_DEST"
    else
        echo "Warning: Standard config not found at $CONFIG_SRC"
    fi
fi

# 3. Run linter
"$LOCAL_BIN" --fix "$FILE_PATH"

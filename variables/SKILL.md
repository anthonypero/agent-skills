---
name: variables
description: Guide for resolving `${VARIABLE_NAME}` tokens. Use this skill when you encounter variable tokens in text, file paths, or instructions and need to substitute them with their actual values.
---

# Variables

Resolve `${VARIABLE_NAME}` tokens by substituting their defined values before executing any command or writing any path.

## Syntax

- **Defining**: `- **NAME** = value` in a memory file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, etc.)
- **Referencing**: `${NAME}` in text, scripts, or commands

## Resolution Order

Search these sources in order. Use the first match found:

1. **Project memory** — `CLAUDE.md`, `AGENTS.md`, or equivalent in the project root
2. **Secrets** — `.agents/PROJECT_SECRETS.md` or similar non-committed files
3. **LastPass vault** — for a secret-shaped name (a key, token, password, or credential) that the
   files above do not define, ask the `lastpass` skill: run `lp get NAME` from the project
   directory, which tries `<project>/NAME` in the shared vault and then `global/NAME`. The
   vault is the source of truth for secrets; `PROJECT_SECRETS.md` is a fallback copy. Use the
   value in place, never paste it into the conversation or a file.
4. **Local context** — conversation history, `.env`, or other active files

If a variable is undefined in all sources, ask the user.

## Common Variables

| Variable            | Purpose                                                                              |
|:--------------------|:-------------------------------------------------------------------------------------|
| `${PROJECT_DIR}`    | Absolute path to the project workspace root                                          |
| `${REPO_DIR}`       | Git repository root (may differ from `PROJECT_DIR`)                                  |
| `${WEB_ROOT}`       | Web asset serving directory (`src/`, `public/`, etc.)                                |
| `${ENV}`            | Environment descriptor (`production`, `development`)                                 |
| `${SUBPROJECT_DIR}` | Metaprojects only: the active subproject's work folder (see `variables-metaproject`) |

---
name: variables
description: Guide for resolving `${VARIABLE_NAME}` tokens. Use this skill when you encounter variable tokens in text, file paths, or instructions and need to substitute them with their actual values.
---

# Variables Skill

This skill guides the resolution of configuration variables (tokens formatted as `${VARIABLE_NAME}`).

## Resolution Strategy

When you encounter a token that needs resolution (e.g., "Deploy to `${WEB_ROOT}`"):

1.  **Identify the Source**:
    *   **Primary**: Check `AGENTS.md` (or `GEMINI.md`) in the project root.
    *   **Secrets**: Check `.agents/PROJECT_SECRETS.md`.
    *   **Context**: Check for definitions in the current conversation or other active files (e.g., `.env`, `README.md`).

2.  **Resolve**:
    *   Locate the definition (e.g., `- **WEB_ROOT** = `...``).
    *   Substitute the token with the exact string value.

3.  **verify**:
    *   Ensure the resolved value makes sense in context (e.g., a valid file path).
    *   If a variable is undefined, ask the user for clarification.

## Variable Standards

This project follows a standard convention for configuration and path portability.

### Syntax
- **Setting**: Variables are defined as `NAME = value` or `- **NAME** = value` in a memory file (e.g., `AGENTS.md`, `GEMINI.md`).
- **Referencing**: Variables are referenced in text, scripts, or commands using the binary `${NAME}` syntax.

### Common Variable Types
In most projects, you should expect to find and resolve the following:

| Variable | General Purpose |
| :--- | :--- |
| `${PROJECT_DIR}` | The absolute path to the root of the current project workspace. |
| `${WEB_ROOT}` | The directory from which web assets are served (often `src/`, `public/`, or `NULL`). |
| `${REPO_DIR}` | The root directory of the git repository. Usually defaults to `${PROJECT_DIR}`, but may be a subdirectory (e.g., `skills/`) or a separate path. |
| `${ENV}` | The environment descriptor (e.g., `production`, `development`, `testing`). |

### Resolution Hierarchy
When resolving a `${TOKEN}`, search in this order:
1.  **Project Memory**: Memory-augmented files in the project root (e.g., `AGENTS.md`).
2.  **Secret Stores**: Non-committed files in `.agents/` or similar hidden directories.
3.  **Local Context**: Conversational history or current file definitions.

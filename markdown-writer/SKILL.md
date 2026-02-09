---
name: markdown-writer
description: "Standards and tools for creating and modifying Markdown files. Always use when editing or creating documentation, notes, or any .md files."
---
# Markdown Writer

This skill defines the standards for writing Markdown in this project.

## Standards

1.  **Format**: usage GitHub Flavored Markdown (GFM).
2.  **Linting**: Ensure file syntax is valid.
3.  **Headers**: Use ATX style headers (`# Header`).
4.  **Requirement**: Use the bundled wrapper command.

```bash
# Run the wrapper script (handles install + config + fix)
~/Projects/skills/markdown-writer/scripts/lint.sh "path/to/file.md" "${PROJECT_DIR}"
```

**Rule**: After editing any `.md` file, you MUST run this script on the file.

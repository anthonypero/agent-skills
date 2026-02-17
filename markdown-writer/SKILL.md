---
name: markdown-writer
description: "Standards and tools for creating and modifying Markdown files. Always use when editing or creating documentation, notes, or any .md files."
---
# Markdown Writer

## Standards

- Use GitHub Flavored Markdown (GFM)
- Use ATX-style headers (`# Header`)
- Ensure all files pass linting before finishing

## Linting

After editing any `.md` file, run the bundled lint script:

```bash
${REPO_DIR}/markdown-writer/scripts/lint.sh "<file>" "${PROJECT_DIR}"
```

The script handles tool installation, config setup, and auto-fixing.

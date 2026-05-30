---
name: markdown-writer
description: "Standards and tools for creating and modifying Markdown files. Always use when editing or creating documentation, notes, or any .md files."
---
# Markdown Writer

## Standards

- Use GitHub Flavored Markdown (GFM)
- Use ATX-style headers (`# Header`)
- Ensure all files pass linting before finishing

## Formatting & Linting

After editing any `.md` file, run the bundled format-and-lint script:

```bash
${REPO_DIR}/markdown-writer/scripts/lint.sh "<file>" "${PROJECT_DIR}"
```

The script auto-installs its tools (Prettier + markdownlint-cli2) and seeds a
default `.markdownlint.json` if the project lacks one, then **auto-formats with
Prettier** (which aligns tables and normalizes spacing) and **lints/auto-fixes**
with markdownlint-cli2. Prettier handles what markdownlint cannot fix on its own
— notably table column alignment (MD060) — so you should rarely need to
hand-edit a file just to satisfy the linter.

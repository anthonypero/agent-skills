---
name: markdown-writer
description: "Standards and tools for creating and modifying Markdown files. Always use when editing or creating documentation, notes, or any .md files."
---
# Markdown Writer

## Standards

- Use GitHub Flavored Markdown (GFM)
- Use ATX-style headers (`# Header`)
- Ensure all files pass linting before finishing

## House style

Author Markdown so the formatter only has to **verify**, not rewrite. When Prettier has to reflow content is exactly when it can mangle it, so write to the style up front — `prettier --check` should report no changes on a file you just authored.

- **One paragraph per line.** Do not hard-wrap prose; write each paragraph as a single line and let the editor soft-wrap. The seeded Prettier config sets `proseWrap: "never"`, so hard wraps get collapsed anyway.
- **Backtick identifiers that contain underscores** (filenames, paths, slugs, IDs — e.g. `` `4447.0101_illios-royal-palace_mak` ``). With `proseWrap: "never"`, an un-backticked underscore identifier sharing a line with `_emphasis_` makes Prettier mispair the delimiters and corrupt both.
- **Use Prettier's normalized emphasis:** `_italic_` and `**bold**`.
- **Let Prettier own tables and spacing** — write a rough table and let the formatter align the columns (handles MD060).

## Formatting & Linting

After editing any `.md` file, run the bundled format-and-lint script:

```bash
${REPO_DIR}/markdown-writer/scripts/lint.sh "<file>" "${PROJECT_DIR}"
```

The script auto-installs its tools (Prettier + markdownlint-cli2) and seeds
default `.markdownlint.json` and `.prettierrc.json` configs if the project lacks
its own (it never overwrites an existing config of any extension), then
**auto-formats with Prettier** (which aligns tables and normalizes spacing) and
**lints/auto-fixes** with markdownlint-cli2. Prettier handles what markdownlint
cannot fix on its own — notably table column alignment (MD060) — so you should
rarely need to hand-edit a file just to satisfy the linter.

---
name: documentation-manager
description: "Manage documentation scraping using the `documentation` tool. Use when the user asks to scrape new docs, update existing docs, or clean up doc projects."
---
# Documentation Manager

This skill provides workflows for analyzing, scraping, and maintaining documentation from external websites using the `documentation` CLI tool.

## The `documentation` Tool

**Requirements**: The `documentation` command must be in your PATH.
(Source: `scripts/documentation` or globally installed).

## Workflow

### 1. Reconnaissance (Analyze Site)

Before scraping, analyze the site to determine the best strategy (Sidebar vs Sitemap).

```bash
documentation recon --url "https://example.com/docs"
```

- **Sidebar Mode**: Best for correct reading order. Requires a CSS selector for the sidebar.
- **Sitemap Mode**: Fallback for obfuscated/JS-heavy sidebars. Flattens structure (alphabetical).

### 2. New Project (Scrape)

**Standard (Sidebar)**:
```bash
documentation new --url "https://example.com/docs" \
  --title "MyProject" \
  --selector ".sidebar-nav" \
  --section "main"
```

**Sitemap Mode**:
```bash
documentation new --url "https://example.com/docs" \
  --title "MyProject" \
  --sitemap \
  --section "main"
```

*Note: `--section` is optional but recommended to isolate content from headers/footers.*

### 3. Update Project

Update an existing project to fetch the latest changes.

```bash
documentation update --title "MyProject"
```

### 4. Cleanup

Remove a documentation project.

```bash
documentation cleanup --title "MyProject"
```

---
name: documentation-manager
description: "Manage documentation scraping using the `documentation` tool. Use when the user asks to scrape new docs, update existing docs, or clean up doc projects."
---
# Documentation Manager

Scrape, compile, and maintain documentation from external websites using the `documentation` CLI.

## Prerequisites

Before running any command, verify the tool is available:

```bash
which documentation
```

If the command is not found, the tool must be installed from its source project:

- **Repository**: `https://github.com/anthonypero/documentation-builder.git`
- **Requirements**: Python 3.8+, Google Chrome, Node.js

```bash
gh repo clone anthonypero/documentation-builder
cd documentation-builder
python3 -m venv .venv && source .venv/bin/activate
pip install selenium webdriver-manager beautifulsoup4 markdownify
npm install -g markdownlint-cli2
```

After installation, ensure `scripts/documentation` is in PATH or symlinked.

## Workflow

### 1. Recon

Analyze the site to determine scraping strategy before creating a project.

```bash
documentation recon --url "https://example.com/docs"
```

- **Sidebar**: Preserves reading order. Requires a CSS selector for the nav element.
- **Sitemap**: Fallback for JS-heavy or collapsed sidebars. Returns URLs in alphabetical order.

### 2. New Project

**Sidebar mode** (preferred):

```bash
documentation new --url "https://example.com/docs" \
  --title "MyProject" \
  --selector ".sidebar-nav" \
  --section "main"
```

**Sitemap mode**:

```bash
documentation new --url "https://example.com/docs" \
  --title "MyProject" \
  --sitemap \
  --section "main"
```

`--section` is optional but recommended to isolate content from headers/footers.

### 3. Update

Re-scrape and rebuild an existing project:

```bash
documentation update --title "MyProject"
```

### 4. Cleanup

Remove a documentation project:

```bash
documentation cleanup --title "MyProject"
```

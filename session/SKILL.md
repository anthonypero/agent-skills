---
name: session
description: "Session lifecycle for a project. Use when the user says 'let's restart' or 'pick up where we left off' (start), asks to record decisions or 'take notes' (notes), or says 'let's wrap' / 'end this session' (wrap)."
---

# Session Lifecycle

This skill manages a project's start → notes → wrap loop. Read **only** the sub-file for the active subflow; the others must not enter context.

The requested action is `$ARGUMENTS` (empty when triggered by natural language — infer the subflow from the user's phrasing).

| Intent | Action | Read and follow |
| --- | --- | --- |
| "let's restart" · "pick up where we left off" · start of a session | `restart` | [start.md](start.md) |
| record a decision · "take notes" | `notes` | [notes.md](notes.md) |
| "let's wrap" · "end this session" | `wrap` | [wrap.md](wrap.md) |

## Where the session files live

Two layouts. Resolve which one applies **before** reading or writing anything; the sub-files say "restart file" and "notes dir" and mean whichever this resolves to.

- **Per-host** — the project has a `.agents/hosts/` directory. Restart file: `.agents/hosts/<host>/restart.md`. Notes dir: `.agents/hosts/<host>/notes/`. A top-level `.agents/restart.md` is then only a router stub: read it if you want the map, never write it. Anything else at `.agents/notes/` is frozen history.
- **Flat** — no `.agents/hosts/` directory. Restart file: `${PROJECT_DIR}/.agents/restart.md`. Notes dir: `${PROJECT_DIR}/.agents/notes/`.

`<host>` is the **host slug**: the single line in `~/.config/agents/host`, else the `$AGENTS_HOST` environment variable. If neither exists, the host is unknown.

```bash
cat ~/.config/agents/host 2>/dev/null || printf '%s\n' "$AGENTS_HOST"
```

Per-host mode with an unknown host: **stop and ask the user which host this is**, offering the directories under `.agents/hosts/` as the options, and offer to write their answer to `~/.config/agents/host` so the next session resolves on its own. Never guess the host, never fall back to flat mode, and never write into another host's subtree.

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

## What goes where

**`.agents/` is the agent's memory of the work. Everything else is the work.** This holds in every project, not only in metaprojects. The test: does a person or a program use it to do the job, or does the agent use it to remember the job?

| `.agents/` (project) or `.agents/subprojects/<subproject>/` (subproject) | The work |
| --- | --- |
| `restart.md`, session notes | inputs and outputs: reports, images, exports |
| plans, briefs, decisions, and research the agent wrote | data the work reads or produces, caches included |
| `variables.md`, `PROJECT_SECRETS.md` | scripts and tooling that do the work |
| skills (instructions for the agent) | links to the work's external pieces, such as a theme repo |

`.agents/scripts/` and `.agents/assets/` hold only what the agent itself uses. `.agents/scripts/` holds the utilities the agent writes to manage the project; it is not the project's work product. `.agents/assets/` holds diagrams and reference files for the agent's own docs. Scripts that are part of the work, the things a person or program runs to do the job, go in `scripts/` at the project root or in `subprojects/<subproject>/scripts/`. Data the work reads or produces sits next to the work it serves.

For example, a project's build, publish, and provisioning scripts run the project itself, so they go in `scripts/`; a one-off utility the agent wrote to clean up some data stays in `.agents/scripts/`. The test is whether the project needs the script to do its job, or the agent wrote it to manage the project. A script bundled inside a skill (`.agents/skills/<skill>/scripts/`) is part of that skill and stays with it; what it produces is the work.

Where the work goes in a metaproject:

- **One subproject's work** goes in `subprojects/<subproject>/`, or wherever its `SUBPROJECT_DIR` points (see `variables-metaproject`).
- **Work shared by several subprojects** stays at the metaproject root. Examples are a `gdrive` link and shared tooling.
- **Scripts** go in a folder named `scripts/`, not `bin/`. A script used by one subproject goes in `subprojects/<subproject>/scripts/`. A shared script goes in `scripts/` at the root.

**Subprojects are listed by their folders only.** A metaproject's subprojects are the folder names under `.agents/subprojects/`. `agentic-project.json` does not list them.

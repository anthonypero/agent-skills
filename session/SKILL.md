---
name: session
description: "Session lifecycle for a project. Use when the user says 'let's restart' or 'pick up where we left off' (start), asks to record decisions or 'take notes' (notes), or says 'let's wrap' / 'end this session' (wrap)."
---
# Session Lifecycle

This skill manages a project's start → notes → wrap loop. Read **only** the sub-file for the
active subflow; the others must not enter context.

The requested action is `$ARGUMENTS` (empty when triggered by natural language — infer the
subflow from the user's phrasing).

| Intent | Action | Read and follow |
| --- | --- | --- |
| "let's restart" · "pick up where we left off" · start of a session | `restart` | [start.md](start.md) |
| record a decision · "take notes" | `notes` | [notes.md](notes.md) |
| "let's wrap" · "end this session" | `wrap` | [wrap.md](wrap.md) |

# Session Note

Record the note as Markdown in `${PROJECT_DIR}/.agents/notes/`, filename `YYYY-MM-DD-n.md`
(`n` = the session number for the day, starting at `1`). List today's files first to get the
next `n`.

A note is **lean** and **historical**: write-once, never edited later, never points forward.
Capture decisions and the *why* — the diff carries *what* changed. Don't reproduce spec/PRD
content; point at the source instead.

## Template

```markdown
# Session Note: [Date] — Session [N]

## Goals
[What this session set out to do, in a sentence or two]

## What changed (why, not what — the diff carries the detail)
[Per change: the reasoning, tradeoffs, and constraints behind it]

## Decisions of record
[Choices worth remembering, with the rationale that makes them stick]

## Deferred
[Threads consciously left for later so they aren't lost]
```

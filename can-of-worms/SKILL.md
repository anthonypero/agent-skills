---
name: can-of-worms
description: "Park a discovered-but-deferred task into an in-repo `can-of-worms.md` backlog instead of derailing the work in progress, then acknowledge receipt and continue. Use whenever the user flags something as a tangent to set aside — phrasings like \"that's a can of worms\", \"let's can-of-worms that\", \"can-of-worms this\", \"add that to the can of worms\", \"park that\", or \"noted, not now\" — or otherwise signals that an idea/discovery should be captured rather than pursued right now. Also use to create the backlog file, append an item, or review/triage existing can-of-worms items."
---

# can of worms

A "can of worms" is a real task you *discover* mid-work whose pursuit would open a whole new effort
and derail what's in front of you (a fishing turn of phrase — once the can is open the worms won't
go back in, so you don't open it until you're ready). This skill captures such items in an in-repo
`can-of-worms.md` backlog — **not** the harness task list — so nothing is lost and current work
still gets finished.

## When the user flags a can of worms

Do all four, in order:

1. **Resolve the backlog file** (see below).
2. **Append the item** as an entry with a short *why* (see format).
3. **Acknowledge receipt** — always reply with a brief one-line confirmation, e.g.
   `Logged to can-of-worms: <short label>`. Never silently swallow it.
4. **Continue the task in progress** — do *not* start working the parked item.

## Resolving the backlog file

The file is portable across repo types. Pick its location in this order:

1. **Existing file wins.** If a `can-of-worms.md` already exists in the active working context
   (the current tier, then the repo root), append to it.
2. **Documented convention wins over defaults.** If the repo's `AGENTS.md`/`CLAUDE.md` (or
   equivalent) says where the backlog lives, follow that.
3. **Otherwise choose by repo shape:**
   - **Metaproject with subproject tiers** (`.agents/subprojects/<name>/`): if the session is
     focused on one subproject, use `.agents/subprojects/<name>/can-of-worms.md`; for repo-wide
     work use `.agents/can-of-worms.md`. (Same tier rule the `session` flow uses for notes.)
   - **Plain repo with an `.agents/` dir:** use `.agents/can-of-worms.md`.
   - **Plain repo, no `.agents/`:** use `can-of-worms.md` at the repo root.
   - **No repo / loose directory:** use `can-of-worms.md` in the working directory.
4. **Create it if missing**, with the header template below.

Mark an item that belongs to a different tier than the current one as *cross-tier* in its *why*,
and prefer logging it to the tier it actually concerns.

## Entry format

- Newest entries on top, under `## Open`. Use `- [ ]`.
- One bold lead clause naming the item, then a sentence of *why* / context so future-you remembers
  why it was parked. Add a date when useful.
- When an item is handled, flip it to `- [x]` and move it under `## Done` (keep done items a while
  for history).

```markdown
## Open

- [ ] **Short item name.** Why it surfaced and why it's parked; cross-tier note if relevant. — YYYY-MM-DD

## Done

- [x] **Resolved item.** — YYYY-MM-DD
```

## Header template (new file)

```markdown
# can-of-worms.md — <tier or project> backlog

The deferred tail: things we've *discovered* need doing but are deliberately setting aside so the
work in front of us gets finished — pursuing each would open a "whole new can of worms." Noted, not
now. Tracked here, in the repo, rather than in the harness task list.
```

## Reviewing / triaging

When the user asks what's in the can of worms, or to triage it, read the resolved file and
summarize the `## Open` items. Promote an item to active work only when the user decides to — at
which point it leaves the backlog for the live thread (restart.md / notes / the task at hand).

---
name: session-metaproject
description: "Session lifecycle for a metaproject — a repo that holds several subprojects alongside repo-wide concerns. Use when the user says 'let's restart' or 'pick up where we left off' (start), asks to record decisions or 'take notes' (notes), or says 'let's wrap' / 'end this session' (wrap). METAPROJECT AUGMENTATION: this skill EXTENDS the global `session` skill rather than replacing it — whenever `session` would trigger, load BOTH `session` and `session-metaproject` together and apply this on top. It routes notes and restart to a per-subproject `.agents/subprojects/<subproject>/` folder when the session targets one specific subproject, and keeps session's normal `.agents/` location when the session is on the metaproject as a whole. It also accepts an explicit `/session <subproject> <action>` command form."
---
# Session — metaproject augmentation

This skill does **not** replace `session`; it rides on top of it. Whenever `session` triggers,
load both. If you reached here without `session`, load it now and follow it — then apply the one
override below.

It applies to any repo that is a **metaproject**: a repo that holds several **subprojects** (each
its own unit of work, with its own deliverable) alongside repo-wide, shared concerns. What counts
as a subproject and how it's named is defined by the consuming repo — see *Where the subproject
identity comes from* below.

## The only override: where notes and restart live

A metaproject is two things at once — the **metaproject itself** (its repo-wide, shared concerns:
cross-cutting plumbing, shared docs, the framework or machinery the whole repo exists around) and a
home for many **subprojects**, each effectively its own sub-effort. Route this session's note and
restart by which one it's working on:

| This session is working on… | Notes path | Restart path |
| --- | --- | --- |
| **The metaproject** — repo-wide / shared concerns; no single subproject is the deliverable | `.agents/notes/YYYY-MM-DD-n.md` *(session's normal path)* | `.agents/restart.md` *(session's normal path)* |
| **One subproject** — work whose deliverable is one subproject's contents | `.agents/subprojects/<subproject>/notes/YYYY-MM-DD-n.md` | `.agents/subprojects/<subproject>/restart.md` |

The boundary is "what's the deliverable?" — the repo's shared machinery (metaproject) vs. one
subproject's contents. When a session genuinely does both, treat it as metaproject.

- **`<subproject>`** is the subproject's identifier — its directory name under
  `.agents/subprojects/`. For a subproject that doesn't exist yet, use the kebab-case name it will
  have. Create the `.agents/subprojects/<subproject>/notes/` path on first write.
- **Next `n`** — list today's files in *that tier's* notes dir, not the other's.
- **No board needed.** Each subproject carries its own `restart.md`, so working two subprojects (or
  two machines) in parallel never clobbers a single shared handoff. Separation does the job.

### Where the subproject identity comes from

This skill is project-agnostic on purpose: it owns the *mechanism* (metaproject tier vs.
per-subproject tier, and the routing below), not the *taxonomy* (what a subproject is and how it's
named in this particular repo). The consuming repo supplies the taxonomy:

- **Explicit is best** — the `/session <subproject> <action>` command form (below) lets the user
  name the subproject directly, so most of the time the skill needs no taxonomy at all.
- **Otherwise infer, and let the repo guide the inference** — the repo's `AGENTS.md`/`CLAUDE.md`
  is the place to state what its subprojects are (e.g. "subprojects are the packages under
  `packages/`," or "the skills under `skills/`"). If neither a token nor a clear repo convention
  resolves it, ask which subproject — or treat the session as metaproject.

### Naming the subproject in the command

`session`'s `$ARGUMENTS` may be **augmented with a leading subproject token** in front of the
action, so the tier can be addressed explicitly instead of inferred:

```
/session <subproject> start     # e.g. /session payments start
/session <subproject> notes
/session <subproject> wrap
```

Parse `$ARGUMENTS` as an optional `<subproject>` followed by the normal `session` action: split a
leading subproject token off the front, then hand the remaining action (`start`/`restart`,
`notes`, `wrap`) to `session`'s usual handling.

- **`<subproject>`** is the subproject's directory name under `.agents/subprojects/` (kebab-case
  for one that doesn't exist yet). It selects that subproject's tier for both notes and restart.
- **An explicit token wins over inference.** `/session payments wrap` routes to the `payments`
  tier even if the conversation looked like it was about something else.
- **No token → fall back to inference** (the table's rule): a named or plainly-single subproject
  routes to its tier; otherwise the metaproject tier. Plain `/session start`, `/session wrap`, and
  natural-language triggers ("let's wrap") are unchanged. The metaproject has no token — absence
  *is* the metaproject.

### How each subflow adapts

- **start / restart** — if the user names a subproject, or the work is plainly one subproject, read
  that subproject's `.agents/subprojects/<subproject>/restart.md`. Otherwise read the metaproject
  `.agents/restart.md`. To see what's in flight across subprojects first, list
  `.agents/subprojects/*/restart.md`.
- **notes** — write the note in whichever tier this session falls under; session's note rules
  (lean, historical, write-once, the template) are unchanged.
- **wrap** — write the note and make restart true again **in the same tier**, then commit per
  session's wrap order. A subproject wrap touches only that subproject's `notes/` and `restart.md`;
  a metaproject wrap touches only `.agents/restart.md`.

Everything else in `session` applies unchanged.

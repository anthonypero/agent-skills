---
name: variables-metaproject
description: "Variable resolution for a metaproject — a repo that holds several subprojects alongside repo-wide concerns. METAPROJECT AUGMENTATION: this skill EXTENDS the global `variables` skill rather than replacing it — whenever `variables` would trigger (resolving a `${VARIABLE_NAME}` token), load BOTH `variables` and `variables-metaproject` together and apply this on top. The one override: a tiered lookup that resolves a token from the active subproject's `.agents/subprojects/<subproject>/variables.md` first, then falls back to the metaproject-root `AGENTS.md`/`CLAUDE.md` defaults (and secrets from the subproject's then the root `PROJECT_SECRETS.md`). Use whenever you resolve `${VARIABLE_NAME}` tokens in a metaproject."
---
# Variables — metaproject augmentation

This skill does **not** replace `variables`; it rides on top of it. Whenever `variables` triggers,
load both. If you reached here without `variables`, load it now and follow it — then apply the one
override below.

It applies to any repo that is a **metaproject**: a repo that holds several **subprojects** (each
its own unit of work) alongside repo-wide, shared concerns. What counts as a subproject and how it
is named is defined by the consuming repo's `AGENTS.md` — the same taxonomy `session-metaproject`
uses.

## The only override: where variable values come from

The base `variables` skill resolves a `${VARIABLE_NAME}` from the project's single
`AGENTS.md`/`CLAUDE.md` (and secrets from `PROJECT_SECRETS.md`). In a metaproject, resolve with a
**tiered lookup — nearest tier wins**:

| This session is working on… | Resolve `${VAR}` from, in order |
| --- | --- |
| **One subproject** | 1. `.agents/subprojects/<subproject>/variables.md` → 2. metaproject-root `AGENTS.md` defaults |
| **The metaproject itself** | metaproject-root `AGENTS.md` defaults only |

- A subproject's `variables.md` holds **only overrides** — the variables whose value differs from
  the root defaults. A token not defined there **falls through to the root**. So shared values
  (`GITHUB`, the default `WEB_ROOT`/`REPO_DIR`, …) live once at the root; a subproject lists only
  what it changes (e.g. its own `REPO_DIR`/`WEB_ROOT` when it has a separate checkout).
- **`<subproject>`** is the active subproject's directory name under `.agents/subprojects/`,
  determined exactly as in `session-metaproject`: an explicit `/session <subproject> …` token, the
  plainly-single subproject in play, or inference from the work — otherwise treat the session as
  the metaproject and use root defaults.
- **Secrets tier the same way.** A `${VAR}` not found as a variable is looked up in the
  subproject's `.agents/subprojects/<subproject>/PROJECT_SECRETS.md`, then the metaproject-root
  `.agents/PROJECT_SECRETS.md`. Both are gitignored.

## `variables.md` format

A subproject's `variables.md` opens with the line:

> Resolved by the variables-metaproject skill; overrides metaproject-root values.

then lists overrides as `- **NAME** = ` followed by the value in backticks. Keep it to genuine
overrides; do not restate values that match the root defaults.

Everything else in `variables` applies unchanged.

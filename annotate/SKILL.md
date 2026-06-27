---
name: annotate
description: "Open any local artifact — a Markdown plan, a code file, a live localhost page, a generated image — in the browser for the human to mark up with comments and edits anchored to exact regions, then receive that anchored feedback back in-band and revise. Use when the human asks to review, mark up, annotate, or give anchored/visual feedback on a file or page, or when a plan/design/image needs a human in the loop before you act on it."
---

# Annotate

Annotate puts a human in the review loop for any local artifact. You open the artifact in a browser; the human marks it up with comments and edits anchored to exact regions (a Markdown line, a code line, a point or box on an image) and hits **send**; the anchored feedback returns to you in-band over the filesystem, in the same turn. You revise and re-present. The cycle repeats until the human **accepts**.

**The inversion.** This is an Agent Skill that dispatches no LLM agents. There is no sub-agent, no backend tier, no orchestration loop. The **human** reviews and **you** (the assistant already in this conversation) revise. The skill is a blocking shell command plus an on-disk round protocol — nothing more. Your job is to call `annotate`, read what it prints, edit the real file, and call it again.

**The loop cannot self-close.** Every action you take in response to feedback produces a fresh presentation the human must look at before they can accept. You cannot "fire and walk away": you cannot finalize a round the human has not seen, and accept is head-checked so a round you minted behind their back can never be the one they finalize (see Accept). Engagement is required by the loop's shape, not requested.

## First-run setup (HARD CONSENT GATE — read before installing)

Annotate needs a special browser to load its extension, and on a fresh machine that means a **one-time ~358 MB download**. First check whether the stack is already up — cheap and idempotent:

```sh
test -f "$HOME/.annotate/runtime.json" && echo up || echo first-run
```

If `runtime.json` exists the stack is set up — skip to **Running a review**.

**If it is first-run (no `runtime.json`) and the install decision is undecided (no `~/.annotate/config.json`), STOP. Do not run `install.sh`, `annotate setup`, or any download yet.** Present the following to the human in conversation and **wait for an explicit answer first — even if your tool calls are auto-approved and you are in accept-edits, bypass-permissions, or "YOLO" mode.** Auto-approved tool permissions are *not* the human's consent to download a browser; this gate must hold regardless.

> Annotate needs **Chrome for Testing** — a special Chrome build — because regular Chrome removed CLI `--load-extension` loading (Chrome 137/142; Chrome for Testing and Chromium are exempt). To enable it I would **download ~358 MB** and install it one-time under `~/.annotate/`. Your options:
> - **(a)** install Chrome for Testing (the ~358 MB download),
> - **(b)** decline — don't use annotate; it stays dormant and I won't ask again,
> - **(c)** use an existing Chrome/Chromium you already have, with a short manual setup (no download).

Only **after** the human answers, record the decision by writing `~/.annotate/config.json` — the durable consent file, kept **separate from the generated `runtime.json`** — and then install:

| Choice | Write `~/.annotate/config.json` | Then |
| --- | --- | --- |
| **(a)** install Chrome for Testing | `{ "browser": "cft", "consented": true }` | `sh install.sh` (the recorded consent lets the download proceed; equivalently `sh install.sh --download-cft`) |
| **(b)** decline / stay dormant | `{ "declined": true }` | nothing — do not install and do not re-prompt on later runs |
| **(c)** use my own Chrome/Chromium | `{ "browser": "system", "path": "<abs path to the Chrome/Chromium binary>" }` | `sh install.sh` — it **skips the download** and still builds the profile, `runtime.json`, and load probe |

`install.sh` (equivalently `annotate setup`) then brings up the rest in one command: the data dir, the generated `runtime.json`, the dedicated browser profile, the lazy-singleton server, and the loaded extension (heartbeat-verified). Re-running on a set-up workspace is a no-op. If setup degrades, it prints a short, near-mechanical checklist for the one or two steps a given browser/OS refused — relay those to the human.

The install also enforces this mechanically as a backstop: with no reusable browser and no recorded consent, a bare `install.sh` **refuses to download** and exits (code 3) telling you to confirm. But the gate above is *yours* to hold — never let the install reach for the download before the human has said yes.

## Running a review

The entrypoint is the launch script:

```sh
annotate <artifact-path|url> [--wait|--no-wait] [--timeout <dur>] \
         [--as-code|--as-frontend] [--session <id>] [--no-open]
annotate poll <session>/<artifact> [--timeout <dur>]
```

`annotate` blocks (the default), opens the artifact in the browser, and on the human's send prints **exactly one JSON object** on stdout — `{ "source", "snapshot", "feedback" }` — then exits. Diagnostics go to stderr; stdout carries only the bundle. Exit codes: `0` ok, `3` timeout (review still open — re-collect with `annotate poll`), `66` artifact not found.

### Render-mode inference

Render mode is **inferred from intent, never auto-detected from the file extension**. Pick the flag from what the artifact *is* and how the human wants it reviewed:

| Artifact | Flag | What the human anchors |
| --- | --- | --- |
| Markdown plan / doc | *(default, no flag)* | a rendered line (`data-src-line`) |
| Code file reviewed as code | `--as-code` | a source line — the PR-review bridge |
| HTML / a generated page reviewed as a running UI | `--as-frontend` | DOM elements / coordinates |
| A live localhost dev server (URL, not a file) | `--as-frontend` | live DOM elements + per-element context |
| Image (PNG/JPG/SVG/…) | *(default, no flag)* | a point or a box, normalized 0–1 |

The same `.html` can be reviewed either as source (`--as-code`) or as a running page (`--as-frontend`) — the flag is the human's intent, not the extension.

### Execution-mode inference

Default to **blocking with a short, self-correcting timeout** — a wrong guess simply times out and you re-run. Infer non-blocking only when the request says to do other work meanwhile:

| The human says… | Mode | How |
| --- | --- | --- |
| "let me review this" / nothing about other work | block (default) | `annotate <artifact>` and read the bundle it prints |
| "while I review this, go do X" | fire-and-forget, collect later | `annotate <artifact> --no-wait`, do X, then `annotate poll <session>/<artifact>` |
| "I might be a while" | block with a longer budget | `annotate <artifact> --timeout 30m` |

`annotate poll` blocks on the **current** round without minting a new one, so checking back never disrupts the open review.

## ID derivation

- **Session** — **always pass your harness session id** via `--session <id>` (e.g. `--session 015TQRyuiD4mPGKRkgHFtv1k`). It scopes the per-session auth token and keeps a run's reviews together — don't omit it. If you forget, the launch script falls back to the `CLAUDE_CODE_SESSION_ID` env var when the Claude Code harness exposes it; only if neither is present does it mint a distinct `sess-…` fallback id — deliberately **not** the round-GUID `<timestamp>-<8char>` shape, so a session is never mistaken for a version. The artifact tier already separates multiple files within one session, so reuse the *same* session id across a run; use a *fresh* `--session <id>` only to isolate a concurrent, independent review.
- **Artifact** — derived from the filename (`plan.md` → `plan`), so re-opening the same file accumulates rounds under one history automatically. You never compute this; just pass the same path.

## Consume the bundle and re-present

On the script's return, parse the single JSON bundle and act on it:

1. **Read `feedback`.** Each entry is one anchor: `type: "comment"` (intent — you decide the fix) or `type: "edit"` (a literal `original` → `replacement` the human wrote, which you apply and whose consequences you surface). The `anchor` names the exact region — a `source` line/keyPath/cell, a `text` quote, or a `spatial` point/box. A `source` anchor carries either `line: N` (one 1-based source line) or `lineRange: [start, end]` (an inclusive, 1-based span — a section or whole-document comment); for a `lineRange`, treat the feedback as applying to that entire span of source lines, not just the first.
2. **Edit `source`, never the snapshot.** `source` is the absolute path of the real project file. Apply the edits and address the comments there. The snapshot is a frozen copy the human marked up — read-only.
3. **Base your edits on the right basis via `snapshot`.** Normally `snapshot` resolves to the round's own copy and you can edit `source` directly. **On a revert**, `snapshot` is the `<guid>` of an *earlier* round — the human anchored their feedback against that older version. Read that older snapshot **off disk** (`~/.annotate/<session>/<artifact>/<snapshot-guid>/<snapshot-guid>-snapshot.<ext>`) and apply the feedback to it, then write the result to `source` (§5.4).
4. **Re-present — mint a new round.** Run `annotate <source>` again. This produces a fresh round the human must look at: your response to their feedback is itself presented, so they see what you did before they can accept. This persistent present → annotate → revise → re-present cycle is the review; it persists until the human accepts or exits, and it **cannot self-close**.

Repeat from step 1 each time the script returns.

## Accept

The human accepts in the browser (the Accept control in the Annotate chrome). Accept **finalizes the displayed version and does nothing else** — it does not execute the plan, ship the code, or use the image. Any downstream action is a separate, explicit instruction from the human.

Accept is **head-checked**: it carries the `<guid>` the human is looking at and is rejected (`409 stale-head`) if that is no longer the current head. So if you re-launched between the human's look and their click — minting a newer round they have not seen — their accept of the old round fails rather than silently finalizing a round nobody reviewed. This is the structural guarantee behind "the loop cannot self-close": you cannot finalize an unseen round, and a fire-and-forget accept is impossible by construction.

## Cold resume and history

History lives on disk, not in your context, so it survives a fresh session or a context compaction. To pick up an existing review or answer "what did this look like N rounds ago":

- Round folders sort chronologically: `~/.annotate/<session>/<artifact>/<guid>/`, with `<guid>` a fixed-width `<timestamp>-<8char>`. `ls` order is version order.
- Each round's `<guid>-round.json` is `{ source, snapshot, status, feedback }`; its `<guid>-snapshot.<ext>` is exactly what the artifact looked like that round. Read the Nth-from-last folder's snapshot to recover the Nth-ago version — authoritative, never reconstructed from memory.
- The latest round whose `<guid>-round.json` exists is the head. A `pending` head is an open review; `submitted` means the human sent feedback you have not yet consumed; `accepted` is final for that round (a fresh `annotate <source>` opens a new round and review resumes).

Never reconstruct review history from your own memory — read it off disk.

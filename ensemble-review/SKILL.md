---
name: ensemble-review
description: "Review a high-stakes document with a panel of independent reviewers instead of one. Use when a spec, plan, PRD, research report, design decision or technical-requirements document is about to gate a build or a commitment and a single reviewer's blind spot would be expensive; when the user asks for a panel, an ensemble, a multi-model review, a red-team read, or a second and third opinion on a document; or when a document has been revised enough that nobody trusts a single pass over it any more. Each seat carries a different lens (fidelity, buildability, consistency, adversarial) and a different model family, reviewers never see each other's work, every report lands on disk as validated JSON, and the host session reconciles them into one document. Not for code diffs — those go to /code-review."
---

# ensemble-review

One reviewer has one shape of blind spot. A panel of reviewers who carry different lenses **and** run on different model families has uncorrelated blind spots, so their agreement is evidence and their disagreement is a pointer at the thing nobody has decided yet.

The prototype that motivates this skill is on disk at `.agents/subprojects/annotate/pm/reviews/`. One round dispatched three reviewers with identical prompts on one model and paid three times the tokens for about one and a half times the coverage. The next round swapped redundancy for lens diversity — fidelity, buildability, consistency, adversarial — and beat it decisively: one blocker and two near-blockers the identical panel missed, and every unique high-severity catch came from exactly one lens. This skill makes that panel repeatable and adds the axis the prototype could not: reviewers from different model families.

The value is governed by **independence**, not by count. Everything here is machinery around that one sentence.

> **This is v0.** It runs a panel end to end and reconciles it with `reconcile.py`, which takes the half of the judgment no script can compute from you as a patch. See *What v0 does not have* at the bottom before you promise anyone anything.

## First-run setup

No installer. The scripts are standard-library Python 3 and the only external dependency is an OpenRouter key, which `dispatch.py` resolves itself: `skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY` first, then `$OPENROUTER_API_KEY` in the environment. If neither answers it exits with both paths it tried. Check once, cheaply:

```bash
python3 skills/ensemble-review/scripts/dispatch.py --help
```

## Dispatch model

Reviewers are **personas**, not harness subagents: a lens prompt, a rubric and an output schema in `agents/`, with no model baked in. A **seat** says which family runs a persona. Concrete model ids live in `templates/config.json` and are resolved at dispatch time, so a panel definition never names a vendor.

Prompt composition is fixed, and identical on both legs — that is what makes the comparison meaningful:

- **System message** = the persona body (everything after the frontmatter) + `references/finding-schema.md`.
- **User message** = the artifact and every reference **inlined verbatim**, delimited, each labelled with its repo-relative path.
- Personas have `tools: []`. They never read files. Two reviewers whose inputs differ by what they chose to open are not comparable.

**Tiers, chosen by stakes.** `templates/config.json` maps tier × family to a concrete model:

| Tier | Use it for | What it is |
| --- | --- | --- |
| `frontier` | Specs, plans and decisions that gate a build. The document someone will be held to. | Each family's newest flagship reasoning model. |
| `standard` | Routine documents, second passes, a review of a review. | Each family's prior flagship. |
| `fast` | A quick sanity pass, a dry run of the pipeline, a document you mostly trust. | Each family's mini/flash-class model. |

`--tier` defaults to `frontier`. A panel template may set `"tier"`, and a seat may set its own; `--tier` on the command line wins over both, so an operator can force a whole panel cheap. `--model` overrides everything and is how you pin one seat to a specific id.

**The default panel seats no Claude family.** The host session that authors and reconciles is a Claude model, so a Claude reviewer is the seat most correlated with the artifact; the `claude` family maps to Sonnet 5 at every tier and is seated only on request. The harness-subagent path (`run_panel.py --skip-claude`, an Opus subagent on the subscription) exists for that explicitly seated case.

## The interactive path

### 1. Decide whether it is worth a panel

A panel costs real money and real minutes. It earns them when the document is about to gate something expensive, when it has been revised enough that nobody holds the whole thing in their head, or when the cost of a missed defect is a rebuild. A document nobody will be held to gets one reviewer or none.

Then choose the artifact, the references and the panel:

- **Artifact** — one document. Panels do not review a folder.
- **References** — the source-of-truth documents to judge it against: PRD, seed, framework doc, style guide, acceptance criteria. Optional, but a panel with a `fidelity` seat and no references is a panel with a wasted seat, and `run_panel.py` warns about exactly that.
- **Panel** — `templates/panels/spec-review.json` is the measured four-lens panel. Copy and edit it for anything else; a panel template is a starting point, not a wall.

### 2. Create the run directory

Reviews land **inside the reviewed project's tree**, never in the session scratchpad — harness subagents cannot write there.

```text
<project>/.agents/reviews/<artifact-slug>/<run-id>/
```

and, when the artifact belongs to a subproject tier in a metaproject, that tier's own folder instead:

```text
<project>/.agents/subprojects/<subproject>/reviews/<artifact-slug>/<run-id>/
```

`<artifact-slug>` is the artifact's filename without its extension. `<run-id>` is `YYYY-MM-DD-<n>`, matching this repo's session-note naming. A repeat pass is a new folder, not a new filename convention.

### 3. Run the non-Claude seats

```bash
python3 skills/ensemble-review/scripts/run_panel.py \
  --panel spec-review \
  --artifact <artifact> \
  --ref <reference> --ref <reference> \
  --tier frontier \
  --out <run-dir>
```

Every seat runs in parallel as its own `dispatch.py` subprocess, so a seat that fails takes down only its own seat. Each seat writes `<run-dir>/<lens>-<family>.json` and `.md`, and prints a digest under 2000 characters. `run_panel.py` writes `<run-dir>/manifest.json` and prints every digest.

Add `--skip-claude` **only** when you intend to run the claude seats as harness subagents — which means the panel is at `standard` tier. Skipped seats are recorded in the manifest as `leg: harness, status: pending`.

### 4. Spawn the Claude seats as harness subagents (only with `--skip-claude`)

One subagent per skipped seat, spawned in a single message so they run concurrently. Each brief must carry:

- **The persona body verbatim** — everything after the frontmatter in `agents/lens-<lens>.md`, plus `references/finding-schema.md`. Byte-identical to what the OpenRouter leg sends. If the legs drift, the comparison this skill exists to make is meaningless.
- **The artifact and the references by path**, with the instruction to read *only* those paths and nothing else in the repo.
- **The instruction to write** its report as JSON to `<run-dir>/<lens>-claude.json` and to write nothing else.
- **The instruction to return a digest under 2000 characters** — reviewer id, verdict, counts by severity, the claim lines of its top three findings, and the report path. Nothing more: inter-agent messages truncate near 5500 characters, so disk is the channel and the message is only a pointer.
- **The instruction not to look for, read, or ask about any other reviewer's output.** Blindness is the invariant.

Per the global subagent rule, pin the model explicitly. Never `subagent_type: "fork"`.

### 5. Render the harness reports

```bash
python3 skills/ensemble-review/scripts/render_harness_report.py <run-dir>/<lens>-claude.json --model "<model the subagent ran on>"
```

It validates against the same schema the OpenRouter leg is held to and writes the `.md` beside the JSON, so both legs leave the same artifacts. If it reports validation errors, send them back to that subagent as one repair re-ask; if the second attempt also fails, retire the seat and record it as a **missing seat** in the manifest and in the reconciliation. Never silently drop a reviewer — a four-seat panel that reconciled three reports has to say so, because agreement counts are meaningless otherwise.

### 6. Reconcile

This is the product. The reports are inputs. **`reconcile.py` is the only writer of `reconciliation.json` and `reconciliation.md`** — you never write either by hand. It computes what a script can compute and takes the rest from you as a **judgment patch**.

```bash
python3 skills/ensemble-review/scripts/reconcile.py --run-dir <run-dir>
```

The first run calls no models and writes nothing but `<run-dir>/judgment-request.json`, then exits 3 saying a patch is required. That file is your worksheet: one entry per **provisional cluster**, with its members, the key that joined them, its provisional tier and the fields you owe it.

**What the script did on its own.** It collapsed each reviewer's duplicates, then grouped findings across reviewers on the two keys it can compute, in order — **normalized location equality** (NFKC, case-folded, markup and a leading `§` stripped, every run of non-alphanumerics collapsed to a space; equal, or one a prefix of the other at a space boundary) and then **quote overlap** (normalized the same way; containment either direction, or a longest common substring of at least 60 characters). Each group is a provisional cluster `P-n`. It tiered them, provisionally.

**What you owe it**, written to `<run-dir>/judgment.json` against `schemas/judgment-patch.schema.json`:

- **`claim_joins`** — provisional clusters that name the same defect from different anchors. Claim equivalence is a semantic call and is never computed; every cluster it forms is marked `match_key: "claim"` and `judgment: true`, so a reader auditing the document knows which clusters rest on a mind.
- **`splits`** — a mechanically joined group whose members turn out to name different defects. Two reviewers quoting one sentence to make two arguments is the common case. Splits apply first, so a join can name a split's product (`P-4.1`).
- **`singleton_labels`** — every **post-merge** `singleton` or `corroborated-same-family` cluster is either a **blind-spot catch** or a **family-specific false positive**, with the reason. An unlabelled one is not an allowed output, and the script refuses the patch.
- **`severities`** — the arbitrated severity and why it survived, for any cluster whose members disagree. A severity argued with a `citation` beats one argued without; downgrade one step when a finding is a singleton, below `high` confidence, and uncited. Where the members agree the script takes their severity and records the spread.
- **`dispositions`** — `fix-now`, `flag-for-human` or `defer`, for **every** cluster, with the reason and the reviewers on each side. A `judgment-call` change kind is `flag-for-human` unless you state a ruling on it in `rulings`, which is a thing an interactive host may do and the `synthesis` persona may not.
- **`contradictions`**, **`canonical_edits`**, **`altitude_splits`** and the **`method_caveat`** — who flatly disagreed with whom, which literal edit you accept for application and why, which lens is rating a document's seams at a different altitude than the rest (not a conflict, and never reported as one), and how many families actually ran.

Then run it again. It merges your patch, **recomputes every tier from the post-merge membership** — a tier computed before your joins is a tier computed against the wrong member set — validates, mints the final `BK-n` / `SF-n` / `NH-n` ids, computes the run-level verdict from arbitrated severity and disposition together, and writes both files atomically:

```bash
python3 skills/ensemble-review/scripts/reconcile.py --run-dir <run-dir> --judgment <run-dir>/judgment.json
```

There is no verdict field in the patch. If you disagree with the verdict, change a **disposition** — a claim about what should happen to a cluster, which is reviewable — rather than the number a rule produced. If the patch does not hold against the post-merge state, nothing is written and the failing entries are named; fix them and re-run. `--render-only` re-renders the Markdown from the JSON, and `--artifact <path>` checks that every quoted anchor is real text in the pinned document.

## Independence invariants

A v0 that violates these is not this skill.

- **Blind and parallel.** Reviewers never see each other's prompts, outputs, digests or file paths. Sequencing is fine; leakage is not.
- **Write full, return a digest.** Every report lands on disk as JSON inside the project tree. The orchestrator receives at most 2000 characters back.
- **Differentiated by default.** Seats carry different lenses. Identical briefs are for corroboration-voting on a single yes/no question, and v0 does not implement that mode.
- **Structured output, validated.** Prose is not an accepted reviewer output. One repair re-ask, then flag-and-advance as a missing seat.
- **One persona body, both legs.** The prompt a harness subagent adopts and the system message sent to OpenRouter are byte-identical.

## Topic index

| Request | Where to look |
| --- | --- |
| "How do I fill a finding?" | `references/finding-schema.md` |
| "What exactly is a valid report?" | `schemas/review-report.schema.json` |
| "What do I owe the reconciler?" | `schemas/judgment-patch.schema.json`, and `<run-dir>/judgment-request.json` for this run |
| "What shape is the reconciliation?" | `schemas/reconciliation.schema.json` |
| "How do the mechanical match keys work?" | `scripts/lib/reconcile_core.py` — `normalize_location`, `location_match`, `quote_match` |
| "Does the clustering still work?" | `python3 scripts/tests/test_replay.py` — the deterministic replay of the 2026-09-18 panel |
| "Which models run which family?" | `templates/config.json` |
| "What does the measured panel seat?" | `templates/panels/spec-review.json` |
| "What does each lens actually ask?" | `agents/lens-*.md` |
| "What does a report of this quality read like?" | `.agents/subprojects/annotate/pm/reviews/fullspec-review-*.md` |
| "What should the reconciliation look like?" | `.agents/subprojects/annotate/pm/reviews/fullspec-reconciliation.md` |

## Reference file index

| Key | File | Domain | Used by |
| --- | --- | --- | --- |
| `finding-schema` | `references/finding-schema.md` | Domain knowledge — the output contract | every persona, both legs |
| `reconcile` | `scripts/reconcile.py` | The only writer of both reconciliation files | step 6, interactively and autonomously |
| `reconcile-core` | `scripts/lib/reconcile_core.py` | Match keys, tiers, patch merge, verdict — importable without the CLI | `reconcile.py`, the replay test |
| `judgment-patch` | `schemas/judgment-patch.schema.json` | The contract for the judgment you supply | step 6 |
| `reconciliation` | `schemas/reconciliation.schema.json` | The contract for the product | `reconcile.py`, later `apply_fixes.py` |
| `replay` | `scripts/tests/test_replay.py` | Acceptance (a): the clustering replayed against a hand reconciliation | run it after any change to the core |

## Dynamic context loading matrix

| Agent | Static context (frontmatter) | Dynamic context (injected at dispatch) |
| --- | --- | --- |
| `lens-fidelity` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-buildability` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-consistency` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-adversarial` | `finding-schema.md` | Artifact inlined, every reference inlined |

## What v0 does not have

Say this plainly to anyone who asks what the skill does. None of it is stubbed; it is absent.

- **`apply_fixes.py` and auto-apply** — nothing is ever written back to the artifact. `change_kind` and `literal_edit` are collected, and `reconciliation.json` now carries a `canonical_edit` and an `edit_conflict` flag per cluster so the gate can be built, but no code reads them yet.
- **`install.sh`** — there is no installer. The key check is `dispatch.py --help`.
- **The `synthesis` persona and autonomous mode** — `reconcile.py` reads a judgment patch from either author, but nothing here can *produce* one unattended: there is no reconciler persona and no unattended run. Every run has a human writing `judgment.json`.
- **Four of the eight lenses** — `completeness`, `security`, `alternatives` and `second-order` are specced and not written. `spec-review.json` carries a fifth seat under `optional_seats` (completeness on `xai`) that cannot be enabled until that persona exists.
- **Family constraint resolvers** — `non-claude` and `distinct` are not implemented. A seat's `family` must be a named family from the config tier map. `spec-review.json` therefore names four concrete families where the spec writes constraints.
- **Budget projection** — no pre-flight estimate and no `budget_usd` knob. Cost is reported after the fact, per seat in `_meta.cost_usd` and per run in the manifest.
- **The other three panel templates** — `research-report`, `design-decision` and the deferred `code-review` stub are not written.
- **Reference and artifact revisions** — the manifest records paths, not the git revision they were read at. A re-run against a changed file is not distinguishable from the manifest alone.
- **Code review** — out of lane. Code diffs go to `/code-review`.

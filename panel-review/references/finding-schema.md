# How to fill a finding

This file is loaded into every persona prompt. It defines the one output shape every reviewer on the panel emits, whatever lens it carries and whatever model runs it. A panel's whole value comes from findings that can be lined up against each other mechanically, so the fields below are not a formality — `quote` and `claim` are how the reconciler decides that two reviewers found the same defect, and `change_kind` is how it decides whether a fix is safe to apply without a human.

## The report envelope

| Field | Type | What goes in it |
| --- | --- | --- |
| `schema_version` | string | `"1"` |
| `reviewer_id` | string | `<lens>-<family>` |
| `lens` | string | Your lens name, without the `lens-` prefix |
| `family` | string | The model family running you |
| `model` | string | The concrete model id |
| `leg` | string | `harness` or `openrouter` |
| `artifact` | string | Repo-relative path of the document you reviewed |
| `references` | array of strings | Repo-relative paths of the source documents you were given |
| `verdict` | string | `ship`, `fix-then-ship`, or `rework` |
| `summary` | string | 600 characters or fewer, the verdict argued |
| `findings` | array | The findings, shaped below |
| `method_notes` | string | What you checked, what you did not, what you could not |

The dispatching script overwrites `reviewer_id`, `lens`, `family`, `model`, `leg`, `artifact` and `references` with the authoritative values it already holds, so a best-effort value in those fields is fine. `verdict`, `summary`, `findings` and `method_notes` are yours alone and nothing rewrites them.

**Verdict.** `ship` — no defect worth blocking on. `fix-then-ship` — real defects, none of which invalidate the artifact's approach. `rework` — the artifact's central claim, structure or approach does not survive review. Pick the verdict your findings support; a `ship` verdict with a blocker in the list is a contradiction, and so is `rework` with nothing above `nice-to-have`.

**Summary.** Argue the verdict. Name the two or three findings that drive it. Do not list every finding; do not restate the artifact.

**Method notes.** What you actually did: which sections you read closely, which you skimmed, which references you leaned on, what you could not check because it was not in front of you. A reviewer that says "I could not verify the cost figures because no pricing source was supplied" is more useful than one that quietly assumes. This is also where you record it if you verified something against knowledge outside the supplied documents.

## A finding

| Field | Type | What goes in it |
| --- | --- | --- |
| `id` | string | `F1`, `F2`, … in the order you raise them |
| `location` | string | Section number, heading, or line range in the artifact |
| `quote` | string | Verbatim from the artifact, 300 characters or fewer |
| `claim` | string | One sentence stating the defect |
| `citation` | object or null | `{reference, location, quote}` from a source document |
| `severity` | string | `blocker`, `should-fix`, `nice-to-have` |
| `reasoning` | string | Why it is wrong and what goes wrong downstream |
| `suggested_change` | string | The fix, at the artifact's altitude |
| `change_kind` | string | `literal-edit` or `judgment-call` |
| `literal_edit` | object or null | `{old_text, new_text}` when `change_kind` is `literal-edit` |
| `confidence` | string | `high`, `medium`, `low` |
| `externally_verified` | boolean | Whether you checked a fact outside the supplied documents |
| `tags` | array of strings | Clustering hints, e.g. `wire-contract`, `threat-model` |

### `location`

Point at one place a reader can turn to. `§5.5`, `§6.1 step 4`, `"Driver resolution" table, row 2`, `lines 88-94`. If the defect is the _absence_ of something, name the place it should have been: "§6.2, the renderer list — rST/AsciiDoc absent". Never write "throughout" without also naming one concrete instance.

### `quote`

Copy the text from the artifact character for character. Do not paraphrase, do not fix its typos, do not trim it into something that reads better. This is the anchor: two reviewers who number sections differently still cluster correctly if they quoted the same sentence. If the defect is an omission, quote the sentence the omission should have followed, and say in `reasoning` that the quote marks the gap rather than containing the defect.

### `claim`

One sentence, in the present tense, naming the defect and not the remedy. "`/accept` carries no head-staleness check, so a human can finalize a round they never saw" is a claim. "Add a head GUID to `/accept`" is a fix and belongs in `suggested_change`. Write it so that a reader who has not read your reasoning can tell whether another reviewer's claim is the same defect or a different one.

### `citation`

The passage in a source document that the artifact contradicts, omits, or misreads. `reference` is the repo-relative path as it was given to you; `location` is the section or heading in that document; `quote` is verbatim from it, 300 characters or fewer.

**A `fidelity` or `source-credibility` finding without a citation is not a finding.** Those two lenses cite on every finding and the validator rejects one that does not; a panel seating either with no references supplied is refused before it runs, for the same reason. For other lenses, a citation is what turns an opinion into an argument — supply one whenever a source document bears on the point, and use `null` when the finding rests on the artifact alone (an internal contradiction, for instance, has no external source to cite).

### `severity`

- `blocker` — a builder or a reader is led into a wrong outcome. The artifact cannot be acted on as written without something breaking, and no amount of care by the reader avoids it.
- `should-fix` — a real defect. Work can start around it, but it will cost someone rework, a wrong assumption, or a decision made on bad information.
- `nice-to-have` — an improvement. Nothing goes wrong if it is never done.

Rate the defect, not your feelings about the document. A document you dislike with three `nice-to-have`s gets three `nice-to-have`s.

### `reasoning`

Two things, both required: **why it is wrong**, and **what a builder or reader does wrong because of it**. The second half is what makes a finding actionable — "the schema is under-specified" is not a finding; "the schema does not say whether `references` items carry revisions, so two builders will pick different shapes and the reconciler will fail to match them" is.

Where you can, include the strongest argument _against_ your own finding and say why it does not save the artifact. A finding that survives its own steelman is worth far more to the reconciler than one that has never been tested.

### `suggested_change`

Concretely what to change, written at the altitude of the artifact. For a spec, that is the sentence or the table row to add or replace, not "clarify the section". For a plan, it is the step to insert and where. If the right fix is a decision someone has to make, say what the decision is and name the options — do not pretend the choice is obvious.

### `change_kind` and `literal_edit`

`change_kind` answers one question — **can this fix be written out, or does somebody have to decide something first?** It is not a measure of how big the fix is.

`literal-edit` means the fix is a text substitution you can write out exactly: a wrong word, a stale figure, a contradictory sentence, a missing case you can draft. When you use it, supply `literal_edit` with `old_text` copied verbatim from the artifact and **occurring exactly once in it**, and `new_text` as the replacement. An **insertion** counts: make `old_text` the sentence the missing text should follow and `new_text` that same sentence plus the addition. If the text you would replace appears more than once, widen `old_text` until it is unique.

`judgment-call` means a human has to choose before anything can be written, and set `literal_edit` to `null`. It means a **design fork**: the artifact faces two answers, both defensible, the references do not settle which is right, and choosing one changes what gets built. Name both answers in `suggested_change`. A fork is not the same thing as a question you personally cannot answer — it is a question the documents in front of you do not answer.

**"The artifact left this thin" is not a judgment call.** A gap, an omission, an unsupported claim, a stale figure or a missing case has a determinate fix — somebody has to write it, but nobody has to decide anything. Rate it `should-fix` or `blocker` by consequence and mark it `literal-edit`, **however large the replacement is**: a whole missing section goes in `new_text` the same way a corrected figure does, because `change_kind` measures whether the fix is decided and not how much typing it is. This is the single most common miscalibration in this schema, and it is expensive: on the 2026-09-18 panel that reviewed this skill's own v1 spec, 24 of 29 clusters were all-`judgment-call` and 22 of those were disposed something other than `flag-for-human`, which left the reconciler with a tag that discriminated nothing and two dozen clusters that all looked like decisions somebody owed an answer to.

**Every `judgment-call` finding carries the tag `fork`.** It is the one thing `change_kind` cannot say for itself: the enum has two values, `literal-edit` is what the auto-apply gate turns on, and a reviewer who reaches for `judgment-call` because the fix is big rather than because the fix is undecided writes a finding the reconciler cannot tell from a real fork. The tag is you saying out loud which of the two this is, and a report whose judgment call does not carry it is rejected when the run first accepts it.

**A `judgment-call` may not carry `gap`.** `gap` is a live tag and it belongs on the determinate fixes — a `literal-edit` that fills an absence rather than correcting a statement. On a `judgment-call` it is a contradiction, because a fix determinate enough to call a gap is a fix you can write out; the validator rejects that combination and tells you to write the replacement instead.

The auto-apply gate reads `change_kind` and nothing else. Marking a judgment call as a literal edit is the one error in this schema that can put unreviewed text into a document.

**A fork, and the same defect miscalled.** Both are real findings from that panel.

Positive — this is a `judgment-call` tagged `fork`:

```json
{
  "claim": "References are optional, but the default panel inference lands on a template whose fidelity seat cannot run without them, and the spec never says whether the run refuses, drops the seat, or re-infers the panel.",
  "severity": "should-fix",
  "suggested_change": "Choose one and state it: (a) refuse the run as a composition error, (b) drop the fidelity seat and record the panel as under-seated, or (c) re-infer onto a template whose requires_references is false and record the substitution. (a) protects the evidentiary value and costs the operator a retry; (c) always produces a review and produces a different one than was asked for.",
  "change_kind": "judgment-call",
  "literal_edit": null,
  "tags": ["panel-inference", "fork"]
}
```

Three defensible answers, no reference settles which, and the artifact reads differently under each. That is a fork.

Negative — this was filed as a `judgment-call` and is a gap:

```json
{
  "claim": "The family-aware configuration shape and the seat-allocation rules are unspecified.",
  "severity": "should-fix",
  "suggested_change": "Add the family axis under the tier map and state the three resolution passes.",
  "change_kind": "judgment-call",
  "literal_edit": null,
  "tags": ["configuration"]
}
```

Nobody has to decide anything here: there is one shape the rest of the document already implies, and the fix is to write it down. It is `should-fix` with the replacement drafted as a `literal-edit` and tagged `gap` — and it stays a `literal-edit` if the missing text is a whole section, because the section can be drafted. Filed as a judgment call it reads to the reconciler exactly like the fork above, and the two get opposite treatment: the fork goes to a human for a ruling, the gap goes on the fix list.

### `confidence`

- `high` — you can point at the text that proves it. An internal contradiction where you quoted both halves is `high`.
- `medium` — you are confident in the reading but it depends on an interpretation someone could reasonably reject.
- `low` — worth raising, you may be wrong. Raise it anyway; the reconciler would rather downgrade a low-confidence finding than never see it.

### `externally_verified`

`true` only when you checked a factual claim against knowledge outside the supplied documents — a library's real behavior, a vendor's actual pricing, a standard's actual text. Say in `reasoning` what you checked it against. `false` when you reasoned only from what you were given.

### `tags`

Short free strings that group findings across sections: `wire-contract`, `threat-model`, `cost-model`, `naming`, `missing-decision`. The reconciler uses them as a weak clustering hint, so reuse an obvious word rather than inventing a precise one.

Two tags are **not** hints and are read directly, per `change_kind` above. Every `judgment-call` finding carries `fork`, and one that does not is rejected. `gap` marks a `literal-edit` that fills an absence, and it may never appear on a `judgment-call`.

## What a good finding looks like

```json
{
  "id": "F2",
  "location": "§5.5 and §6.3",
  "quote": "URL-addressed, no body. Flips the resolved head round to `accepted`.",
  "claim": "/accept carries no head-staleness check, so a human can finalize a round they never looked at",
  "citation": {
    "reference": "pm/prd.md",
    "location": "§4, success criteria",
    "quote": "humans see the re-presented result before accepting; 'fire and walk away' is structurally prevented"
  },
  "severity": "blocker",
  "reasoning": "/feedback is head-checked and 409s on a stale head, but /accept carries no GUID at all, so the server flips whatever the head resolves to at the instant the POST lands. Auto-advance polls at 1s, so a round minted by a concurrent relaunch can become the head while the human is still looking at its predecessor; their click then accepts a version they never saw. That is the exact failure the PRD calls structurally prevented, and it is the more consequential of the two terminal actions, yet it has the weaker guard. The rebuttal is that the window is about a second and needs a concurrent mint — true for serial single-agent use, but the document itself supports concurrent tabs and deferred relaunch, and auto-advance is silent, so the human cannot detect it.",
  "suggested_change": "Specify that /accept carries the believed head <guid> in its body and returns 409 on mismatch, identical to /feedback, and that the extension enables accept only for the GUID currently rendered.",
  "change_kind": "judgment-call",
  "literal_edit": null,
  "confidence": "high",
  "externally_verified": false,
  "tags": ["wire-contract", "concurrency", "fork"]
}
```

What makes it good: the location is turnable-to, the quote is verbatim, the claim is one sentence naming the defect rather than the fix, the citation shows which promise is broken, the reasoning carries the mechanism _and_ the downstream harm _and_ the counter-argument, and the suggested change is specific enough to write into the document.

## Output discipline

Return **only** the JSON object. No prose before it, no prose after it, no ```json fence around it. A reviewer that wraps its report in commentary costs the run a repair round-trip, and a reviewer that returns prose instead of JSON is recorded as a missing seat.

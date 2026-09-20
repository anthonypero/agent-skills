# Auto-apply — the gate, and its audit trail

`apply_fixes.py` writes a reconciliation's accepted literal edits back into the artifact. It is the only thing in this skill that modifies the document under review, and it is **off by default**.

```bash
python3 scripts/apply_fixes.py --run-dir <run-dir> --i-authored-this
python3 scripts/apply_fixes.py --run-dir <run-dir> --dry-run      # resolve and report, write nothing
```

It reads `reconciliation.json` and `manifest.json` and nothing else — never a report, never a judgment patch, never re-derived prose. Everything the gate turns on was decided upstream by a mind and recorded in the product.

## The threat model this gate exists inside

**The artifact is untrusted input.** It is inlined verbatim into every seat's user message, and a document can contain instructions as easily as content. A hostile artifact can ask its reviewers to emit a consensus-shaped `literal-edit` whose `new_text` is the attacker's paragraph, and if two families comply, the five conditions below see a well-formed cluster and nothing is wrong with any of them.

So the gate is not the whole defence. Three rules are, and only the third is a gate:

1. **Never auto-apply against an artifact the operator did not author.** `--i-authored-this` is refused-by-default, with the reason printed: the operator is the only party who can tell a corroborated fix from a document that recruited its own reviewers. `run_panel.py --auto-apply on` requires the same assertion, and refuses as a composition error without it.
2. **Personas quote verbatim.** A `quote` and a `literal_edit.old_text` are spans of the artifact, never paraphrase, and the lens bodies say so.
3. **The reconciler checks that quoted text is real.** Every `quote` and every `old_text` is verified against the pinned artifact bytes before anything is clustered, and any member whose anchor is not real text is dropped and recorded in `anchor_drops`.

None of this makes the skill safe against a hostile document. It makes the write path refuse the obvious gadget.

## The five conditions

A cluster is applied only if **all five** hold.

1. **`disposition` is `fix-now`.** Somebody decided this should happen. `flag-for-human` and `defer` both mean nobody has agreed to fix it.
2. **`contradicted_by` is empty and `edit_conflict` is false.** No reviewer explicitly approved what this cluster flags, and the members did not propose competing replacements the judgment supplier declined to choose between.
3. **`canonical_edit` is present and was explicitly accepted in the judgment patch** — exactly one literal edit, carrying an `accepted_by`, whose `old_text` occurs **exactly once** in the artifact. `accepted: false` in the patch, or no `canonical_edits` entry at all, leaves `canonical_edit` null and the cluster out.
4. **`tier` is `consensus`, or `unanimous` with `n_families >= 2`.** Stated as evidence rather than as an enum, the same rule reads: `n_families >= 2` **and** the tier is not a same-family tier (`same-family`, `corroborated-same-family`, `singleton`). Both wordings are checked on every cluster, so the gate cannot silently change meaning if the tier table does; a tier neither wording knows is refused rather than guessed at.
5. **The artifact's current content hash equals the `artifact_revision` the manifest recorded.** A run with no recorded revision is not auto-appliable at all: there is nothing to check the document against.

**Condition 4 corroborates the defect, not the replacement.** Requiring two families to write byte-identical replacement text would make the gate fire almost never. What stands in for it is condition 3's acceptance — a mind looked at the wording and said so in the patch — and the canonical edit's source reviewer is named in the log, so a single-sourced replacement is traceable to the one seat that wrote it. That was a fork the adversarial seat raised and the owner settled on 2026-09-18: single-sourced canonical edits stay eligible, gated on explicit acceptance.

## All-or-nothing

Every candidate anchor is resolved against the current file **first**, no two edits may overlap, and only then is the file written **once, atomically**, with the original file's mode carried across.

A failure at any anchor aborts the whole set and applies nothing. An `old_text` that has stopped occurring, one that now occurs twice, and two edits whose spans overlap are all the same condition: the document is not what the reconciliation describes, and a half-applied set of corroborated fixes leaves it in a state no reviewer read and no reconciliation describes.

**Failing the gate is not failing an anchor.** A cluster that does not pass conditions 1 to 4 is simply not a candidate: it is reported as unapplied with its reason, and the candidates that do pass are still applied. Only an anchor that will not resolve, an overlap, or a revision mismatch aborts everything.

## The single-family no-op

A panel that ran single-family cannot satisfy condition 4 for any cluster. Auto-apply on such a run is a **no-op that says so** — exit 0, nothing written, no audit log — rather than an error or a quietly relaxed gate. The distinction matters: silence would read as "the gate weighed the clusters and found nothing", and the truth is that the panel never produced the kind of evidence the gate turns on.

## The audit trail

`applied.md` lands in the run directory whenever the gate actually ran, and it is written whether or not anything was applied. Its job is that an applied fix can be audited **backwards** — from the bytes in the document, to the cluster, to the reviewers behind it, to the seat whose wording was used and the mind that accepted it.

Per applied edit: the cluster id and its claim, the tier and the families behind it, every contributing reviewer with its finding id, the canonical edit's source seat, who accepted it and why, and the before/after text as a diff.

Then every candidate that was **not** applied, with the condition it failed. That half is as load-bearing as the first: a reader has to be able to tell "the gate found nothing to apply" from "the gate found four things and refused all of them".

**An applied run's reconciliation no longer pins the artifact.** Writing changes its content hash, so condition 5 will fail on any second run of `apply_fixes.py` against the same reconciliation. That is correct and is why it is not worked around: re-applying a review of bytes that no longer exist is exactly what condition 5 is there to stop.

## Exit codes

`0` applied, or nothing was eligible, or a single-family no-op · `1` usage — no authorship assertion, no run directory, no reconciliation, an artifact that does not resolve, or an `--artifact` pointing inside the run's own read-only `inputs/` copy · `3` the set was aborted — an anchor did not resolve, two edits overlapped, or the artifact has moved since the panel read it.

`0` covers two different outcomes on purpose: "there was nothing to do" is not a failure, and the audit log distinguishes them in a form a human reads rather than in a code a script would have to guess the meaning of.

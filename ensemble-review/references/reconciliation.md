# Reconciliation — the algorithm, and how to write a judgment patch

The reviews are inputs. This is the product.

`reconcile.py` is the **only writer** of `reconciliation.json` and `reconciliation.md`, in every mode. It computes the half of the algorithm a script can compute and takes the other half as a **judgment patch** at `<run-dir>/judgment.json`, supplied by the host session interactively, by the `harness-judge` agent when one is installed, or by the `synthesis` persona when none is. Two minds each holding a copy of the same seven steps is two implementations of one contract; one script plus one validated patch is one.

This file is loaded into the `synthesis` persona's system prompt, so everything below is a description of code that exists rather than of behaviour somebody intends. Where you find it disagreeing with `scripts/lib/reconcile_core.py`, the code is right and this file is a bug.

## The division of labour

| Step | Whose | What |
| --- | --- | --- |
| 1. Collect and validate | script | Load every report the manifest expected; name every seat that did not report |
| 2. Cluster, mechanically | script | `location` equality, then `quote` overlap. Each group is a provisional cluster `P-n` |
| 2. Cluster, semantically | **patch** | `claim_joins` and `splits` — the joins and separations no string comparison reaches |
| 3. Tier by agreement | script | Twice: once over the provisional clusters, once over the post-merge ones. Only the second is recorded |
| 4. Arbitrate severity | **patch** | `severities`, where the members disagree |
| 5. Dispose | **patch** | `dispositions`, for every cluster; `contradictions` |
| 6. Surface disagreements | script, from the patch | Contradictions and two-step severity spreads are derived; `altitude_splits` come from the patch |
| 7. Record choices | **patch** | Every reason field, plus `singleton_labels`, `canonical_edits` and `method_caveat` |

## The order the script runs in, and why it is that order

collect and validate → compute provisional clusters → **drop unreal anchors** → compute provisional tiers → hand both to the judgment supplier → merge `splits`, then `claim_joins` → **recompute every tier from the post-merge membership** → apply the labels, severities, dispositions, contradictions and canonical edits → validate the patch against the post-merge state → mint the final ids → compute the verdict → write.

Two things in that sequence are load-bearing.

**Splits run before joins.** A mechanically joined pair the patch pulls apart usually exists precisely so one half can be joined to something else; running the joins first would leave that half unreachable.

**Tiers are recomputed after the merge.** A tier computed before the joins is a tier computed against the wrong member set: two provisional singletons the patch merges are a `consensus` cluster afterwards, and a mechanically joined pair the patch splits is two singletons. Two patch errors fall straight out of that recompute, and both re-ask the supplier — a post-merge cluster whose members are all one family (`singleton`, `same-family` or `corroborated-same-family`) with **no label**, and a **label that lands on a cluster that is no longer one of those three tiers**. Both mean the supplier judged a different member set than the one that survived.

## Step 1 — collect and validate

Every `<reviewer-id>.json` the manifest's `seats` array named. A report that is absent or that fails validation makes that seat a **missing seat**, recorded with its stage (`dispatch`, `validation`, `timeout`, `skipped`) and its reason, in the manifest and in the product. A reviewer is never silently dropped: a four-seat panel that reconciled three reports has to say so, because agreement counts are meaningless otherwise.

A report file for a reviewer the manifest never resolved is the opposite error and stops the run. `seats_expected` is exactly what the manifest resolved and nothing else, because it is the `unanimous` denominator, and a stray file would raise that bar with no seat behind it.

Stored reports are validated **without** the ingest-time rules. The `fork` tag on a `judgment-call` finding is required when a report first enters the system and never when one is read back, so a report written before that rule still reconciles.

## Step 2 (mechanical) — the two keys a script can compute

Each reviewer's own duplicates collapse first, so a reviewer contributes at most one member to a cluster; the ids that collapsed into it are kept in `collapsed_finding_ids`, so the audit path back to the report does not dead-end.

**Normalized location equality.** Normalize a `location` by NFKC-normalizing it, case-folding it, stripping backticks, asterisks and underscores, stripping a leading `§`, replacing every run of non-alphanumeric characters with a single space, and trimming. Two findings match when their normalized forms are equal, **or when one is a prefix of the other at a space boundary** — "Reconciliation step 3" matches "Reconciliation step 3 tier table" and does not match "Reconciliation step 30".

**Quote overlap.** Normalize a `quote` by NFKC-normalizing it, case-folding it, collapsing every run of whitespace to one space, and trimming. Two findings match when the longer normalized quote **contains** the shorter, or when their **longest common substring is at least 60 characters**. Below 60, containment is the only match. Sixty is a fifth of the 300-character quote cap, chosen so two reviewers anchoring on the same sentence match and two anchoring on adjacent boilerplate do not.

Location is tried first; a group joined on location records `match_key: "location"`, one joined only on quotes records `quote`, and a group of one records `none`. Each group gets a stable provisional id `P-n`, numbered by its lowest `(reviewer_id, finding_id)` pair in lexical order.

**Claim joins are not computed, and the script does not pretend otherwise.** Two findings naming one defect from different sections with different anchors are joined only by the patch.

## The anchor check, which is not optional

Before anything is tiered, every `quote` and every `literal_edit.old_text` is checked against the **pinned artifact** — the bytes in the run's own `inputs/` directory, which is what the seats read. A member whose anchor is not real text in that document is **dropped from its cluster**, and the drop is recorded in `anchor_drops` with the provisional id it was dropped from. A cluster left with no members disappears; one left with one member is a cluster of one and no longer rests on the key that joined it.

The check runs **after** the provisional ids are minted and never renumbers them: `P-n` is the patch's entire reference vocabulary, and a check that renumbered would invalidate every patch written against the previous run.

`reconcile.py` resolves the artifact in three steps, and this is the order `_resolve_artifact` takes: **`--artifact` first**, then the run's own `inputs/` copy, then the manifest's `artifact` path. Neither resolving is exit 1, not a quiet skip. Only `--render-only`, which re-renders the Markdown from an already-checked `reconciliation.json`, runs without it. A quote the validator truncated at its 300-character cap is matched as the prefix it is, by the truncation marker on the raw string.

## Step 3 — the tier table

Ordered and exhaustive; first match wins. `R` is the number of **reporting** seats.

| Order | Tier | Condition | Reading |
| --- | --- | --- | --- |
| 1 | `unanimous` | Every **expected** seat is a member, and `n_families >= 2` | The whole commissioned panel agrees, across families |
| 2 | `consensus` | `n_families >= 2` | Cross-family corroboration — the strongest evidence this skill produces |
| 3 | `same-family` | `n_reviewers >= 2`, `n_reviewers > R/2`, one family | Several lenses, one mind — labelled below |
| 4 | `corroborated-same-family` | `n_reviewers >= 2`, `n_reviewers <= R/2`, one family | Two of four seats from one family — labelled below |
| 5 | `singleton` | `n_reviewers = 1` | One seat, labelled below |

**The denominator differs by tier on purpose.** `unanimous` counts **expected** seats, so a panel with any missing seat cannot mint it. `same-family` and `corroborated-same-family` count **reporting** seats, because a retired seat cannot vote either way. The `n_reviewers >= 2` floor on both of them is what keeps `singleton` reachable at all: without it a one-seat panel would tier its clusters `same-family` and skip the labelling duty.

**Tiers 3, 4 and 5 all owe a label**, because all three are one family and one family agreeing with itself is one mind whatever the count. Tier 3 was called `majority` and was the one that did not: it is unreachable with two families in it — tier 2 catches those — so the name promised corroboration the tier cannot carry, and on a single-family panel every multi-lens agreement rendered unlabelled under a "Single-seat" heading. They render under their own heading, **Same-family should-fixes — one family, uncorroborated**, and never under the single-seat one.

Two cross-family agreements outrank three same-family agreements, and both counts are in the document so a reader can check the ruling.

## Writing the patch

One JSON object at `<run-dir>/judgment.json`, validated against `schemas/judgment-patch.schema.json`. Run `reconcile.py --run-dir <dir>` with no patch first: it writes `<run-dir>/judgment-request.json` — one entry per provisional cluster with its members, the key that joined them, its provisional tier and the fields it owes — and exits 3. That file is the worksheet.

Required at the top level: `schema_version` (`"1"`), `run_id` (must match the manifest), `author` (`host`, `synthesis` or `harness-judge`), `generated_at` (ISO 8601 with offset), `dispositions`, and `method_caveat`. Every other array may be omitted when empty. Unknown fields are rejected; a field whose name begins with an underscore is the one exception the validator makes, for a working note nothing may rely on.

### Cluster references

Provisional ids — `P-n` as the mechanical pass assigned them — everywhere except inside `splits`, which addresses findings directly by `{reviewer_id, finding_id}`.

A split's own products are addressable too. The groups of a `splits` entry on `P-4` produce `P-4.1`, `P-4.2`, … **in the order the `groups` array lists them**, and any member of `P-4` the entry did not place lands together in one final leftover group with the next number. Those ids are derivable from the patch alone, which is what lets a later `claim_joins` entry name one half of a split. **A bare `P-4` that the patch itself split no longer names one cluster**: naming it is a patch error, not a silent bind to the first product.

A cluster the patch merged is addressable by any of the ids that went into it.

### `splits`

`{from, groups: [[{reviewer_id, finding_id}, …], …], reason}`. Every finding named must be a member of `from`, and no finding may appear in two groups. One entry per `from`.

### `claim_joins`

`{merge: [provisional ids], reason}`, at least two ids, every one of which must still exist after the splits. The merged cluster is recorded `match_key: "claim"` and `judgment: true`, so a reader auditing the document knows which clusters rest on a semantic call.

### `singleton_labels`

`{cluster, label, reason}`. `label` is `blind-spot-catch` or `family-specific-false-positive`; `reason` is required and must be non-empty. Required for every **post-merge** cluster whose members are all one family — `singleton`, `same-family` and `corroborated-same-family` — and forbidden on any other. An unlabelled singleton is not an allowed output — one family agreeing with itself is one mind, and so is one seat.

### `severities`

`{cluster, severity, arbitration_reason}`. Supply one only where the members **disagree**. Where they agree, the script takes the agreed severity and fills `arbitration_reason` with the fixed string `members agree`; where they disagree and the patch is silent, that is a patch error and the supplier is re-asked, because arbitrating a disagreement is step 4 and step 4 is not the script's.

The rule: the cluster takes the highest severity whose reasoning survives scrutiny. A severity argued with a `citation` beats one argued without. Downgrade one step when a finding is a singleton, below `high` confidence, and uncited.

### `dispositions`

`{cluster, disposition, disposition_reason}` — `fix-now`, `flag-for-human` or `defer` — for **every** cluster. Name the reviewers on each side where they differ.

**A `judgment-call` change kind is `flag-for-human`, and the rule binds the authors differently.** An interactive **host** may dispose such a cluster some other way and say why in `disposition_reason`: it has an owner to answer to, and that reason is trace enough. The two **unattended** authors — `synthesis` and `harness-judge` — have no such latitude and `reconcile.py` enforces it: a cluster whose members are all `judgment-call`, in a patch from either of them, disposed anything other than `flag-for-human`, is a patch error and nothing is written. An unattended mind disposing a design fork is a human decision recorded as settled by nobody, and the harness judge is held to it exactly as the persona is — it is a frontier model with file tools, which makes it more capable and no more entitled, because capability is not an owner.

The one exception is the tag. A cluster whose judgment calls are **every one of them** tagged `gap` is a determinate fix filed under the wrong `change_kind`, and it goes on the fix list like anything else. Untagged says nothing either way, and the safe reading of silence is the one that asks.

### `contradictions`

`{cluster, contradicted_by: [{reviewer_id, finding_id}]}` — a reviewer that explicitly approved what this cluster flags. Non-empty **forces** `flag-for-human`, whatever the `dispositions` entry said.

### `canonical_edits`

`{cluster, source: {reviewer_id, finding_id}, accepted, reason}`, with an optional `accepted_by`. It names which literal edit in the cluster is the canonical one and whether the supplier accepts it for application.

`accepted: false`, or no entry at all, leaves `canonical_edit` null. So does a `source` that names a member with no `literal_edit`. Competing replacements do **not** by themselves null it — choosing among them is exactly what this entry is for — but `edit_conflict` still records that the members disagreed. `edit_conflict` is a claim about **members**: one reviewer's own collapsed duplicates offering two wordings is that reviewer restating itself, not a disagreement between seats.

This is the only field that can put reviewer-written text into a document unattended. See `auto-apply.md` for the five conditions it is one of.

### `rulings`

`{cluster, ruling, owner_to_confirm}` — a stated ruling on a judgment-call cluster, so an owner has something concrete to confirm or overrule. **The host's alone.** A patch from either unattended author carrying a non-empty `rulings` is a hard error: nothing is written and nobody is re-asked. `rulings` is reserved for a real decision of record — where the host overrides the direction the reviewers proposed, or the owner has settled the fork — and is never required merely because a cluster is a judgment call.

### `altitude_splits`

`{reviewer_id, clusters: [provisional ids], note}` — one lens rating a document's seams at a different altitude than the rest. Copied into `disagreements.altitude_splits` with the provisional ids rewritten to final ids. **This is not a conflict and is never reported as one.** Step 6 is the supplier's, and this is the only field that can carry an altitude split into the product; contradictions and two-step severity spreads are derived by the script from what it already holds.

### `method_caveat`

Required and non-empty. How many families actually ran, what was not supplied to the seats, anything that could not be checked, and any salvage or deviation from the runbook.

The script **appends** to it what the supplier could not know, each drawn from the manifest: seat substitutions the dispatcher made after the reports were written, the family target and both counts against it — one reporting family reads as **lens-diverse only** — and a panel substitution when a run with no references landed on a different template than the artifact's name suggested. Do not guess at any of those.

## There is no verdict field

The run-level verdict is computed, and a supplier who disagrees with it changes a **disposition** — a claim about what should happen to a cluster, which is reviewable — rather than overriding the one machine-readable field downstream scripts branch on.

The rule, in order:

1. Two or more families returning `rework` as their seat verdict yields `rework`.
2. Any cluster at `blocker` severity forbids `ship`. A `blocker` disposed `flag-for-human` or `defer`, or carrying a non-empty `contradicted_by`, yields `rework`; otherwise a blocker yields `fix-then-ship`. `defer` sits with `flag-for-human` because both mean the same thing about the artifact: nobody has agreed to fix it.
3. With no blocker, any cluster at `should-fix` severity disposed `fix-now` or `flag-for-human` yields `fix-then-ship`. A `should-fix` disposed `defer` does not by itself move the verdict off `ship`, because deferral is a decision taken.
4. Everything remaining is `ship`.

## What the patch is checked against

Nothing is written when any of these fails. The failing entries are named; interactively the host fixes them and re-runs, and autonomously the persona gets one repair re-ask and then exit 3.

- The schema: required fields, enums, `additionalProperties: false`.
- `schema_version` is `"1"` and `run_id` matches the manifest.
- `author` is `host`, `synthesis` or `harness-judge`, it matches the run's own recorded reconciler when that reconciler is unattended, and neither unattended author's patch carries `rulings`.
- Every `{reviewer_id, finding_id}` pair names a finding in a validated report.
- At most one entry per cluster in each array — two entries for one cluster would silently last-wins, and the loser is a judgment somebody wrote down that the product never carried.
- Every `singleton_labels` entry carries a label from the enum and a non-empty reason; every disposition and severity is in its enum.
- `method_caveat` is non-empty.
- After the merge: every post-merge `singleton`, `same-family` and `corroborated-same-family` cluster is labelled, no label lands on a cluster that is not one of those, every cluster has a disposition, every cluster whose members disagree on severity has an arbitration, no finding lands in two final clusters, and no reference names a cluster the patch itself split.

## Running it

```bash
python3 scripts/reconcile.py --run-dir <run-dir>                              # worksheet, exit 3
python3 scripts/reconcile.py --run-dir <run-dir> --judgment <run-dir>/judgment.json
python3 scripts/reconcile.py --run-dir <run-dir> --reconciler synthesis        # the persona judges
python3 scripts/reconcile.py --run-dir <run-dir> --judgment <staging>/judgment.json   # ingest the harness judge
python3 scripts/reconcile.py --run-dir <run-dir> --render-only                 # re-render the Markdown
```

`--judgment` defaults to `<run-dir>/judgment.json` when that file exists and then to the harness judge's staging path, so both of the middle forms are usually just the first form run again.

`--reconciler` is `host`, `synthesis`, `harness-judge` or `default`; `default` is what the run recorded in its manifest, and it means host when a human is attached and, when nobody is, the harness judge where `ensemble-judge` is installed and `synthesis` where it is not. In `harness-judge` mode this script makes **no call at all**: with no patch on disk it writes the worksheet, prints the spawn instruction over it and exits 3, and the agent's patch — written to a seat-private staging path outside the run directory — is then ingested and held to its author. `run_panel.py` reaches the same two things by running this pass, so the worksheet always exists before the instruction that names it. In `synthesis` mode with no patch on disk, the persona is dispatched through `dispatch.py`'s own machinery — the same driver, the same doubled-cap length retry on a truncation, the same registry gate, the same per-call cost record — and its patch is validated in full **before** `judgment.json` is written, so an invalid patch never reaches disk. The call is recorded in the manifest as `judge`, with its model, its attempts and its cost, and its cost is added to `cost_usd_total`.

**Exit codes:** `0` both files written and every expected seat validated; `1` usage, including no resolvable artifact and an artifact whose hash does not match the manifest's `artifact_revision`; `2` zero reporting seats, or a provider auth failure on the judgment call; `3` a patch is required, or did not validate after its repair, or the composed judgment prompt does not fit the judge model's context window, or the run reconciled with a missing seat — in which case both files **are** written first, with the missing seat named in them, and the code is a CI signal that the run was under-seated rather than an abort; `4` a direct judgment call this run's manifest never priced, which `--approve-budget` overrides.

---
name: ensemble-review
description: "Review a high-stakes document with a panel of independent reviewers instead of one. Use when a spec, plan, PRD, research report, design decision or technical-requirements document is about to gate a build or a commitment and a single reviewer's blind spot would be expensive; when the user asks for a panel, an ensemble, a multi-model review, a red-team read, or a second and third opinion on a document; or when a document has been revised enough that nobody trusts a single pass over it any more. Each seat carries a different lens — fidelity, buildability, consistency, adversarial, completeness, source credibility, security, alternatives, second-order consequences — and a different model family; reviewers never see each other's work, every report lands on disk as validated JSON, and the host session reconciles them into one document. Not for code diffs — those go to /code-review."
---

# ensemble-review

One reviewer has one shape of blind spot. A panel of reviewers who carry different lenses **and** run on different model families has uncorrelated blind spots, so their agreement is evidence and their disagreement is a pointer at the thing nobody has decided yet.

The prototype that motivates this skill is on disk at `.agents/subprojects/annotate/pm/reviews/`. One round dispatched three reviewers with identical prompts on one model and paid three times the tokens for about one and a half times the coverage. The next round swapped redundancy for lens diversity — fidelity, buildability, consistency, adversarial — and beat it decisively: one blocker and two near-blockers the identical panel missed, and every unique high-severity catch came from exactly one lens. This skill makes that panel repeatable and adds the axis the prototype could not: reviewers from different model families.

The value is governed by **independence**, not by count. Everything here is machinery around that one sentence.

> **This is v0.** It runs a panel end to end and reconciles it with `reconcile.py`, which takes the half of the judgment no script can compute from you as a patch. See _What v0 does not have_ at the bottom before you promise anyone anything.

## First-run setup

No installer yet. The scripts are standard-library Python 3 and the only external dependency is an OpenRouter key, which `dispatch.py` resolves itself: `skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY` first, then `$OPENROUTER_API_KEY` in the environment. If neither answers it exits with both paths it tried. Check once, cheaply:

```bash
python3 skills/ensemble-review/scripts/dispatch.py --help
```

The one thing that does need refreshing is the **model registry**, `templates/models.json` — per concrete model id, what a token costs, how much context it has, which effort strings it accepts, and how many completion tokens one review seat spends on it. It ships seeded, and the cost pre-flight is unimplementable without it:

```bash
python3 skills/ensemble-review/scripts/refresh_models.py            # prices + context limits from the OpenRouter catalogue
python3 skills/ensemble-review/scripts/refresh_models.py --dry-run   # show the diff, write nothing
python3 skills/ensemble-review/scripts/refresh_models.py --add z-ai/glm-5.4
```

It is idempotent — a refresh that changes nothing writes nothing and says so — and it **never touches `output_token_prior`**. The catalogue knows what a token costs; only a run knows how many tokens a lens spends, so priors are updated from run manifests by hand. Offline it exits 1 and leaves the file untouched.

**It resolves the registry through the same cascade, and it is the one script that writes to the package.** With a `models.json` under `<workspace>/.agents/ensemble-review/`, that is the file refreshed. With none, it writes `templates/models.json` inside the package. The read-only invariant is scoped to a _run_ — no report, manifest or reconciliation is ever written there — and refreshing a price cache is maintenance, not a run. If a project needs the package left strictly untouched, give it a workspace registry: `cp templates/models.json <project>/.agents/ensemble-review/models.json`, then pass `--workspace <project>`.

**A resolved seat the registry cannot price is a composition error**, refused before dispatch with exit 1 naming the model and the `--add` line that fixes it. That covers both failures, because presence is not coverage: a model absent from the registry, and a model present with a null input or output price. A seat left out of the projection is a budget gate that does not gate — the run would clear a $5 budget on a projection that priced three of its four seats.

## Dispatch model

Reviewers are **personas**, not harness subagents: a lens prompt, a rubric and an output schema in `agents/`, with no model baked in. A **seat** says which family runs a persona. Concrete model ids live in `templates/config.json` and are resolved at dispatch time, so a panel definition never names a vendor.

**The lens catalog.** Nine personas ship. The first four are the ones with a measured track record — the 2026-06-26 `annotate` full-spec round — and they are what `spec-review` seats.

| Persona | Asks | Needs references | Where it ends |
| --- | --- | --- | --- |
| `lens-fidelity` | Does the artifact faithfully implement the sources it is built from? | **Required** | Does not judge whether the design is good, buildable or self-consistent |
| `lens-buildability` | Could a builder execute this without inventing a missing decision? Is the altitude right? Where are the seams? | Helpful | Stops at the moment the thing is built |
| `lens-consistency` | Do the parts agree with each other? | No | Judges the artifact against itself, never against its sources |
| `lens-adversarial` | What is the strongest case that this is wrong — and then, what is simply missing? | Helpful | Attacks the central bet; the systematic sweeps belong to `completeness` and `security` |
| `lens-completeness` | What is absent, thin, or asserted without support? | Helpful | Sweeps the whole surface against what a document of this kind owes its reader, rather than what an attack needs |
| `lens-source-credibility` | Do the sources deserve the weight the artifact puts on them — authority, currency, independence, sufficiency? | **Required** | Judges the source; `fidelity` judges the reading of it |
| `lens-security` | Who can abuse this? Assets, actors, trust boundaries, attacker-controlled input, blast radius | No | Assumes the design is right and asks who can abuse it; `adversarial` asks whether it is wrong |
| `lens-alternatives` | What approach did this foreclose without arguing why? | No | Enumerates every fork, argued or not; `adversarial` reaches for the one alternative that wins |
| `lens-second-order` | What does this commit us to, and what breaks downstream? | No | Starts where the build ends: month two, year two, the first time somebody changes it |

The catalog deliberately has **no code lenses**. Correctness and simplification/efficiency belong to `/code-review`, and `templates/panels/code-review.json` ships as a named stub that routes there.

Three sentences are **byte-identical in every lens body**, and `scripts/tests/test_catalog.py` asserts it: the severity calibration (rate by consequence, not by how much is unspecified), the `judgment-call` rule (a design fork, not a thin spot), and the verbatim-quote rule. A lens calibrated differently from its neighbours would make the panel's agreement counts mean something different per seat.

**`judgment-call` means a design fork and carries the `fork` tag.** `change_kind` asks whether the fix can be written out or somebody has to decide first, and it is not a measure of size: a determinate fix is a `literal-edit` however large the replacement is, a whole missing section included, and it takes the `gap` tag. The enum could not gain a third value — the frozen replay fixture depends on the two it has — so the tag carries the distinction, and `lib/report.py` enforces it **at ingest**: a reviewer's fresh report is rejected if a `judgment-call` carries no `fork`, or carries `gap`. Reading a stored report back applies neither rule, so a report written before the rule still replays. `reconcile_core` then reads the tag: an all-`judgment-call` cluster goes to a human unless every one of its findings is tagged `gap`, and an untagged cluster goes to the human exactly as it always did.

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

**Tier resolution order**, first match wins, and the manifest records which level decided each seat as `tier_source`:

| Order | Level | Looks like |
| --- | --- | --- |
| 1 | `--model <seat-id>=<model-id>` | `--model fidelity-openai=openai/gpt-6-astra`. Pins one seat to a concrete id, beats everything, repeatable |
| 2 | `--tier` on the command line | Forces a whole panel cheap for a dry run |
| 3 | the seat's own `tier` | One seat at a different depth than its panel |
| 4 | the panel's `tier` | `spec-review` ships at `frontier` |
| 5 | the config's `default_tier` | `frontier` |
| 6 | the persona frontmatter's `model` | Only when it names a key in the tier map. Every shipped persona carries `model: frontier`, which is a key, so this level is live: a seat with nothing above it resolves to `frontier` and records `tier_source: "persona"` |

This inverts framework §8, which puts agent frontmatter above the config default. Here the tier is a property of the run's stakes, not of the lens.

**Family constraints.** A seat's `family` is a named family from the config's tier map — `openai`, `glm`, `kimi`, `xai`, `claude`, `google`, `deepseek` — or one of two constraints. Seats resolve in four passes, so the result does not depend on how the template happens to be written:

1. **Named families** take their family and reserve it — all of them, across the whole template, before anything re-seats.
2. **Missing cells.** A named family with no model at the seat's tier reserved nothing in pass 1; it re-seats here. This is a separate pass because resolving it inside pass 1 lets it take a family a later named seat is about to ask for by name: `[google, claude]` at `frontier` would put two seats on claude while openai sat free, and `[claude, google]` would not — the same panel, two answers, decided by template order.
3. **`non-claude`** takes the first family in the config's declaration order for that seat's tier that is not `claude`, has a model, and no seat already holds.
4. **`distinct`** takes the first family with a model that no other seat holds, reserving against every other seat in both directions. It always resolves last, so it always adds a family the panel did not have.

Three things are re-seated rather than failed, once each, with the substitution recorded in the manifest and named in the reconciliation's method caveat:

- **A missing cell** — a family with no model at the resolved tier, as `google` has at `frontier` — is unreachable. The seat moves to the next eligible family in declaration order that nobody holds.
- **A model the provider refuses** at dispatch time, with a 404 or 400 naming it, is the same condition and takes the same move. The seat's own constraint still binds: a `non-claude` seat is re-seated against what it asked for, not against the family it happened to be holding. An ordinary provider error is _not_ re-seated: re-seating spends a second seat's money, so it is not a guess about what went wrong.
- **An unsatisfiable constraint** does not fail the run either. The seat falls back to the least-held eligible family and records `substitution: {kind: "constraint_unsatisfied", …}`.

**No re-seat of any kind lands on `claude` unless the seat asked for `claude` by name.** Claude is first in the config's declaration order, so without the rule every first re-seat would land on it — and the default panel seats no Claude family on purpose, because the host session that authors and reconciles is itself a Claude model. A substitution that quietly added one would undo that decision with nobody choosing it. When claude is the only _free_ family, the seat doubles up on a non-claude family instead; only a tier with no other family at all is a composition error. Owner ruling, 2026-09-18.

**A seat's `reviewer_id` names what it asked for, not what it got** — `fidelity-openai`, `buildability-non-claude`. A constrained seat's family depends on what else the panel seats, and a re-seated seat would otherwise change its own id, its report filename and its manifest key half way through a run. The resolved family is in the seat's `family`; the difference is in `substitution`.

**Panel templates.** Four ship in `templates/panels/`. A template is a starting point, not a wall: compose seats ad hoc, or drop an edited copy under `<workspace>/.agents/ensemble-review/panels/` and it replaces the shipped one whole.

| Template | Seats | `requires_references` | Notes |
| --- | --- | --- | --- |
| `spec-review` | fidelity, buildability, consistency, adversarial — openai, glm, kimi, xai | `true` | The measured panel: the 2026-06-26 lens set, one family per seat, no Claude seat. A fifth `completeness` seat sits in `optional_seats` on `deepseek` — the one non-Claude family the four seats do not already hold — off by default |
| `research-report` | fidelity, source-credibility, completeness, adversarial — openai, kimi, glm, xai | `true` | Four named distinct families. `verify_web` is off in v1, so the two citing lenses judge the sources you supply rather than the live web |
| `design-decision` | adversarial, alternatives, second-order — xai, openai, glm | `false` | The artifact is the argument. This is also where a run with no references lands |
| `code-review` | none | — | Ships **deferred**: a named stub carrying `"routes_to": "/code-review"`. `run_panel.py` refuses it with exit 1 and says where to go |

**Choosing the panel, when you do not name one.** With no `--panel`, `run_panel.py` infers: a cheap heuristic over the **artifact's own filename** picks `research-report` or `design-decision` when the name says so, and `spec-review` otherwise. Nothing reads the document — a heuristic that opened the artifact would be a second, unreviewed judgement about it before any reviewer has seen it. Then the references rule: **a run with no references never infers a template whose `requires_references` is true.** It falls back to `design-decision`, and the substitution is recorded in the manifest's `panel_inference`, printed on the console, and named in the reconciliation's method caveat. Every run carries that block, named panel or not: `requested` is the `--panel` you gave or null, `heuristic` is what the filename suggested or null when nothing read it, `resolved` is what ran, and `reason` is filled only when the last two differ.

**References are a composition error for two lenses, not a warning.** `fidelity` and `source-credibility` require a `citation` on every finding — `lib/report.py` rejects one from either lens without it — so a seat carrying either with nothing to cite would have every finding it returned thrown out. `run_panel.py` refuses before the first paid call, exit 1, naming the seats. Inference bends to avoid this; an explicitly named `--panel` does not, because an operator who named it should hear why.

**The two panel-shape refusals happen before the run directory is claimed.** The deferred `code-review` stub refuses on the template, and a starved `fidelity` or `source-credibility` seat refuses once the seats resolve and still before the claim. Neither leaves a directory behind, which matters because a claim collision increments the sequence number: a stray `2026-09-18-1` would silently move your retry — the one with `--ref` supplied — into `2026-09-18-2`. Every other refusal comes after the claim and leaves the directory with what it wrote by then — the registry and effort gates, a resume fingerprint mismatch, a materialize failure, and the budget refusal, whose `budget-refusal.json` is written there on purpose — so delete the directory before retrying those.

**`verify_web` is declared and off.** Every live template carries `"verify_web": false`. No seat has a live web tool in v1, so the two citing lenses judge the sources you supply; the flag states that rather than switching anything.

**`min_families` is a target, not a precondition.** The panel's value (default 2) is overridable with `--min-families`. Distinct families are counted **twice** — over the expected seats at Resolve and over the reporting seats at wrap-up — and both land in the manifest as `min_families: {target, seated, reporting}` beside the family lists. A run that misses the target **proceeds**: it is never an error, and the console and `reconcile_core.method_caveat` both say so. One reporting family reads as **"lens-diverse only"**: several lenses, one mind, and no cluster in that run can carry cross-family corroboration at any tier.

**Where files come from: the two-root cascade.** The skill package is read-only: **no run artifact is ever written into it** — reports, renderings, manifests, judgment patches, reconciliations and run directories all land in the reviewed project's tree. The one thing that does appear under the package is CPython's own `__pycache__` bytecode, which the interpreter writes and silently skips when it cannot; that is outside the invariant and outside the test that enforces it. `refresh_models.py` is the other exception, and a deliberate one — see _First-run setup_. Every file resolves through `scripts/lib/paths.py`, which searches two roots in order:

1. `<workspace>/.agents/ensemble-review/` — the project holding the artifact, from `--workspace`, else the working directory.
2. the skill package.

**Files replace whole, first hit wins**: personas (`agents/<name>.md`), panels (`panels/<name>.json`), references (`references/<name>`), schemas (`schemas/<name>.json`), backend drivers (`backends/<type>.py`) and the registry (`models.json`). There is no per-field merge for a file: if the workspace has one, that is the one the run used.

**`config.json` deep-merges**, package first and workspace last, at connector / tier / family / model-id granularity. A project that wants one cell changed writes exactly that cell and nothing else:

```json
{ "openrouter": { "tiers": { "standard": { "glm": "z-ai/glm-5.4" } } } }
```

This is where per-project bindings live, and in particular where the **egress control** lives: a project reviewing confidential artifacts drops a driver at `.agents/ensemble-review/backends/azure_openai.py` and a config fragment binding its families to `"type": "azure_openai"`. The driver is imported from that path — no edit to `scripts/backends/__init__.py` — and the shipped package is untouched. A `type` that resolves in neither root is a composition error, exit 1, naming both roots and every path it tried.

The manifest's `roots` block records the search order, the root each loaded file actually came from, and the list of files a workspace override supplied, so a reader never has to diff two directories to see what a project changed. A workspace that mirrors the package layout (`templates/config.json`, `templates/panels/…`, `scripts/backends/…`) resolves too; the flatter workspace-shaped path is tried first.

`--config` and `--models` remain **operator paths**: given explicitly, they are read as given and never merged.

**The default panel seats no Claude family.** The host session that authors and reconciles is a Claude model, so a Claude reviewer is the seat most correlated with the artifact; the `claude` family maps to Sonnet 5 at every tier and is seated only on request. The harness-subagent path (`run_panel.py --skip-claude`, an Opus subagent on the subscription) exists for that explicitly seated case.

## The interactive path

### 1. Decide whether it is worth a panel

A panel costs real money and real minutes. It earns them when the document is about to gate something expensive, when it has been revised enough that nobody holds the whole thing in their head, or when the cost of a missed defect is a rebuild. A document nobody will be held to gets one reviewer or none.

Then choose the artifact, the references and the panel:

- **Artifact** — one document. Panels do not review a folder.
- **References** — the source-of-truth documents to judge it against: PRD, seed, framework doc, style guide, acceptance criteria. Optional in general, and **required** by any panel seating `fidelity` or `source-credibility`: those two cite on every finding, so `run_panel.py` refuses such a seat with no references as a composition error, exit 1.
- **Panel** — one of the four above, or none at all: with no `--panel` the template is inferred from the artifact's name, and a run with no references never lands on one that needs them. Copy and edit any of them for anything else; a panel template is a starting point, not a wall.

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

**What the run does before it spends anything**, in this order:

| Stage | What happens |
| --- | --- |
| Claim | The run directory is created with an atomic exclusive `mkdir`. On collision the run id's sequence number increments and the claim retries, and the console says which directory the run actually took. |
| Materialize | The artifact and every reference are copied into `<run-dir>/inputs/`, made read-only, and hashed. **A revision is the SHA-256 of the bytes in `inputs/`**, with the git commit id recorded beside it when that file's working tree is clean. Seats read those bytes, never the working tree, so a mid-run edit cannot give two seats two different documents. |
| Resolve | Every file is found through the two-root cascade; seats resolve to tiers, families, models, connectors and efforts by the orders above; the registry is checked to cover every resolved model and every effort is checked against that model's vocabulary. **The manifest is written here, before the first dispatch**, with every seat `pending`, its `tier_source`, and its `substitution` when it was re-seated, so a crash after this point still leaves a manifest that knows how many seats there were and what each one was. |
| Project | The cost and token pre-flight, always printed, always labelled an estimate. It prices each seat at the registry's catalogue rate, then adds two labelled allowances — an output overrun multiplier for the routing premium a real call pays over the catalogue, and a repair allowance — plus a synthesis call when the run will actually make one. See the knobs below. |
| Dispatch | Each seat's record moves `pending` → `dispatching` by compare-and-set before its first paid call, and is updated as it returns. Every structured write is atomic. |

**Knobs on `run_panel.py`:**

| Knob | Default | Effect |
| --- | --- | --- |
| `--workspace` | the working directory | The project whose `.agents/ensemble-review/` overrides the package. Passed straight through to every `dispatch.py` subprocess, so parent and child read the same files. |
| `--model` | none | `--model <seat-id>=<model-id>`, repeatable. Pins one seat to a concrete id and beats every tier source. |
| `--panel` | inferred from the artifact's name, and never a reference-hungry template on a run with no references | The template, by name or by path. |
| `--tier` | the panel's, else the config's | Tier for every seat that does not set its own. |
| `--min-families` | the panel's, else `2` | The family target. A target and not a gate: a run below it proceeds, records `min_families: {target, seated, reporting}`, and says so in the method caveat. |
| `--max-tokens` | `32000` | The completion cap sent on every call. A model's `min_max_tokens` floor in the registry raises it for that seat; the cap actually sent is recorded. |
| `--budget-usd` | `5.00` | The pre-flight budget. Over it, an autonomous run refuses; an interactive one asks. |
| `--approve-budget` | off | Dispatch anyway. |
| `--autonomous` | inferred when stdin is not a tty | There is nobody to ask, so an over-budget projection writes `budget-refusal.json` and exits 4 **before any paid call**. |
| `--reconciler` | the panel's, else `default` | `host`, `synthesis` or `default` (host interactive, synthesis autonomous). Decides whether the projection charges for a synthesis call — an interactive run whose host writes the judgment patch makes none. |
| `--fresh` | off | Claim a new run directory instead of resuming the one `--out` names. |
| `--models` | the skill's `templates/models.json` | Point at another registry. |

**Resume.** Re-running against an existing run directory resumes it, and **the reports on disk are the authority, not the manifest's seat statuses.** A seat whose report is present and validates is never re-dispatched — a repeat of a four-seat frontier panel is not a $2.50 no-op. A seat whose report is missing or invalid **is** re-dispatched, even where the manifest still calls it `ok`: that seat is demoted to `failed` with the reason recorded first, so the manifest stops asserting something the directory contradicts. A seat already `dispatching` is left strictly alone and reported as held by another process. A run whose inputs no longer hash to the manifest's `input_fingerprint` is refused with "start a new run id" rather than mixing two documents' reviews in one directory.

Because an existing directory resumes, **two runs naming the same `--out` share it.** The exclusive `mkdir` only protects a directory that does not exist yet; what protects the money is the per-seat compare-and-set, so only one of the two pays for any given seat.

**Exit codes:** `0` every expected seat validated; `1` usage or composition error, including an unpriced model or a refused resume; `2` terminal infrastructure failure — a provider auth failure halts the run with no further paid call, as does zero reporting seats; `3` the run is under-seated; `4` an autonomous budget refusal.

`dispatch.py` has one more, **`5`, and it is internal**: it is how a child tells `run_panel.py` that the provider refused this model rather than that the reviewer wrote a bad report, which is what the re-seat turns on. The parent consumes it and never re-emits it, so `5` is not in the spec's exit-code table and no panel run returns it. It is visible only when `dispatch.py` is invoked directly on a seat whose model the provider will not serve.

**The three retry paths inside a seat**, which are not the same thing. A transient provider error (429, 5xx, a socket timeout) is retried three times at 1 s, 4 s and 16 s with the same prompt and the same cap. A truncated completion — `finish_reason: length` — is retried **once at double the cap with a fresh prompt**, and the truncated bytes are discarded rather than quoted back; a second `length` falls through to the repair path. A report that does not validate gets one repair re-ask, which is the only prompt that does quote the previous response back. Every call lands in `_meta.attempts` with its cap, its finish reason, its validation errors, its usage and its cost, failed calls included, and the manifest's `cost_usd_total` counts them.

### 4. Spawn the Claude seats as harness subagents (only with `--skip-claude`)

One subagent per skipped seat, spawned in a single message so they run concurrently. Each brief must carry:

- **The persona body verbatim** — everything after the frontmatter in `agents/lens-<lens>.md`, plus `references/finding-schema.md`. Byte-identical to what the OpenRouter leg sends. If the legs drift, the comparison this skill exists to make is meaningless.
- **The artifact and the references by path**, with the instruction to read _only_ those paths and nothing else in the repo.
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

The first run calls no models and writes nothing but `<run-dir>/judgment-request.json`, then exits 3 saying a patch is required. It needs the **pinned artifact** to do even that: it verifies that every `quote` and every `literal_edit.old_text` is real text in the document the seats read, and drops from its cluster any member whose anchor is not, recording the drop in `anchor_drops`. It resolves the artifact from the run's own `inputs/` copy first — so a working-tree document that has since moved no longer matters — then from the manifest's `artifact` path; pass `--artifact <path>` when neither resolves, and it refuses to run, exit 1, when none of them does. That file is your worksheet: one entry per **provisional cluster**, with its members, the key that joined them, its provisional tier and the fields you owe it.

It also checks the **revision**: the resolved artifact's SHA-256 against the manifest's `artifact_revision`. A mismatch refuses, exit 1, naming both hashes — `reconciliation.json` is a function of the artifact's bytes, so reconciling against a document that has moved on checks anchors no reviewer ever saw. **In the ordinary case the check passes silently**: the resolution prefers the run's own `inputs/` copy, whose bytes cannot change, so editing the working-tree document after the panel ran does not stop you reconciling it. The refusal is there for the two cases where it can go wrong — `--artifact <path>` pointing the check at a different file, and an `inputs/` copy that has been deleted, leaving only a working-tree file that may have moved on. A manifest with no `artifact_revision` is **unpinned, not mismatched**: it warns and proceeds, which is how every run written before revisions existed still reconciles. `--render-only` is the one mode that runs without either check.

**What the script did on its own.** It collapsed each reviewer's duplicates, then grouped findings across reviewers on the two keys it can compute, in order — **normalized location equality** (NFKC, case-folded, markup and a leading `§` stripped, every run of non-alphanumerics collapsed to a space; equal, or one a prefix of the other at a space boundary) and then **quote overlap** (normalized the same way; containment either direction, or a longest common substring of at least 60 characters). Each group is a provisional cluster `P-n`. It tiered them, provisionally.

**What you owe it**, written to `<run-dir>/judgment.json` against `schemas/judgment-patch.schema.json`:

- **`claim_joins`** — provisional clusters that name the same defect from different anchors. Claim equivalence is a semantic call and is never computed; every cluster it forms is marked `match_key: "claim"` and `judgment: true`, so a reader auditing the document knows which clusters rest on a mind.
- **`splits`** — a mechanically joined group whose members turn out to name different defects. Two reviewers quoting one sentence to make two arguments is the common case. Splits apply first, so a join can name a split's product (`P-4.1`).
- **`singleton_labels`** — every **post-merge** `singleton` or `corroborated-same-family` cluster is either a **blind-spot catch** or a **family-specific false positive**, with the reason. An unlabelled one is not an allowed output, and the script refuses the patch.
- **`severities`** — the arbitrated severity and why it survived, for any cluster whose members disagree. A severity argued with a `citation` beats one argued without; downgrade one step when a finding is a singleton, below `high` confidence, and uncited. Where the members agree the script takes their severity and records the spread; where they **disagree and you supply no entry**, that is a patch error and the script re-asks, because arbitrating a disagreement is step 4 and step 4 is yours.
- **`dispositions`** — `fix-now`, `flag-for-human` or `defer`, for **every** cluster, with the reason and the reviewers on each side. A `judgment-call` change kind is `flag-for-human` by default, because `judgment-call` means a design fork. As an interactive host you may dispose one some other way and say why in the `disposition_reason` — you have an owner to answer to, and `rulings` is reserved for a real decision of record. The `synthesis` persona has no such latitude: a patch authored by `synthesis` that disposes an all-`judgment-call` cluster anything but `flag-for-human` is rejected by the script. **The one exception is the tag.** A cluster whose judgment calls are every one of them tagged `gap` is a determinate fix filed under the wrong change kind, and `synthesis` may put it on the fix list; untagged is treated as a fork, so a report written before the tag rule is flagged exactly as it always was.
- **`contradictions`**, **`canonical_edits`**, **`altitude_splits`** and the **`method_caveat`** — who flatly disagreed with whom, which literal edit you accept for application and why, which lens is rating a document's seams at a different altitude than the rest (not a conflict, and never reported as one), and how many families actually ran.

Then run it again. It merges your patch, **recomputes every tier from the post-merge membership** — a tier computed before your joins is a tier computed against the wrong member set — validates, mints the final `BK-n` / `SF-n` / `NH-n` ids, computes the run-level verdict from arbitrated severity and disposition together, and writes both files atomically:

```bash
python3 skills/ensemble-review/scripts/reconcile.py --run-dir <run-dir> --judgment <run-dir>/judgment.json
```

There is no verdict field in the patch. If you disagree with the verdict, change a **disposition** — a claim about what should happen to a cluster, which is reviewable — rather than the number a rule produced. If the patch does not hold against the post-merge state, nothing is written and the failing entries are named; fix them and re-run. `--render-only` re-renders the Markdown from the JSON and is the one mode that does not need the artifact. Exit codes: `0` both files written and every expected seat validated; `1` usage, including no resolvable artifact; `3` a patch is required or did not validate, or the run reconciled with a missing seat — in which case both files are written first, with the missing seat named in them.

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
| "How do I override one model, one panel or one driver for my project?" | `scripts/lib/paths.py` — the two-root cascade and the config deep merge |
| "Which family does `non-claude` actually get?" | `scripts/lib/seating.py` — the tier order and the four seating passes |
| "Does the clustering still work?" | `python3 scripts/tests/test_replay.py` — the deterministic replay of the 2026-09-18 panel |
| "Which models run which family?" | `templates/config.json` |
| "What does the measured panel seat?" | `templates/panels/spec-review.json` |
| "Which panel templates are there, and which needs references?" | `templates/panels/` — and the table under _Dispatch model_ |
| "What does each lens actually ask, and where does it stop?" | `agents/lens-*.md` — and the catalog table under _Dispatch model_ |
| "When is a finding a `judgment-call` rather than a gap?" | `references/finding-schema.md` — `change_kind`, with a worked positive and negative |
| "Is the catalog still well-formed?" | `python3 scripts/tests/test_catalog.py` — every persona and every template, as data |
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
| `paths` | `scripts/lib/paths.py` | The workspace-first cascade and the `config.json` deep merge | every script that loads any packaged file |
| `seating` | `scripts/lib/seating.py` | Tier resolution order, family constraints, re-seating | `run_panel.py` at Resolve and after a refused model |
| `replay` | `scripts/tests/test_replay.py` | Acceptance (a): the clustering replayed against a hand reconciliation | run it after any change to the core |

## Dynamic context loading matrix

| Agent | Static context (frontmatter) | Dynamic context (injected at dispatch) |
| --- | --- | --- |
| `lens-fidelity` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-buildability` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-consistency` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-adversarial` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-completeness` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-source-credibility` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-security` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-alternatives` | `finding-schema.md` | Artifact inlined, every reference inlined |
| `lens-second-order` | `finding-schema.md` | Artifact inlined, every reference inlined |

## What v0 does not have

Say this plainly to anyone who asks what the skill does. None of it is stubbed; it is absent.

- **`apply_fixes.py` and auto-apply** — nothing is ever written back to the artifact. `change_kind` and `literal_edit` are collected, and `reconciliation.json` now carries a `canonical_edit` and an `edit_conflict` flag per cluster so the gate can be built, but no code reads them yet.
- **`install.sh`** — there is no installer. The key check is `dispatch.py --help`.
- **The `synthesis` persona and autonomous mode** — `reconcile.py` reads a judgment patch from either author, but nothing here can _produce_ one unattended: there is no reconciler persona and no unattended run. Every run has a human writing `judgment.json`.
- **Mid-flight budget metering** — the projection is a **dispatch gate only**. Nothing meters spend as seats return, and a panel that overruns its projection runs to completion.
- **Per-seat `timeout_s`** — the knob has a specified default of 900 seconds and a specified behaviour, and no implementation. A hung provider hangs that seat until the process is killed.
- **`mode: identical`** — the knob does not exist, so it is not refused by name yet.
- **`verify_web`** — declared `false` on every live template and read by nothing, and the spec's composition error for `verify_web: true` on a tool-less seat is not implemented, so a workspace panel setting it true runs unrefused.
- **Code review** — out of lane, and now visibly so: `code-review.json` ships as a named stub that refuses and routes to `/code-review`. The two code lenses stay out of the catalog.

Built since v0 and no longer on this list: the completion cap and its length retry, the per-model cap floor, the model registry and `refresh_models.py`, the cost and token pre-flight with its budget gate, content-hash revisions with a materialized `inputs/` directory, resume with a compare-and-set claim, the two-root workspace cascade in `lib/paths.py`, the tier resolution order and the `non-claude` / `distinct` constraint resolvers in `lib/seating.py`, re-seating on a missing cell or an unreachable model, the other five lenses (`completeness`, `source-credibility`, `security`, `alternatives`, `second-order`), the other three panel templates, panel inference with its references rule, `min_families` counted at both ends and named in the method caveat, and the `source-credibility` citation rule in the validator.

# ensemble-review

A panel of independent reviewers for one document, instead of one reviewer with one shape of blind spot.

Each seat carries a different **lens** — fidelity to source, buildability, internal consistency, adversarial, completeness, source credibility, security, alternatives, second-order consequences — and runs on a different **model family**. The reviewers never see each other's prompts or outputs, every report lands on disk as validated JSON with a Markdown rendering beside it, and the host session reconciles them into one document that says which findings two families agreed on and which came from exactly one mind.

```bash
python3 scripts/run_panel.py \
  --panel spec-review \
  --artifact pm/technical-requirements.md \
  --ref pm/prd.md --ref .agents/ideas/seed.md \
  --out .agents/reviews/technical-requirements/2026-09-18-1
```

The reason to bother is on disk in this repo. `.agents/subprojects/annotate/pm/reviews/` holds three prototype rounds against one spec. The round that dispatched three reviewers with identical prompts on one model paid three times the tokens for about one and a half times the coverage — the same mind agreeing with itself. The round that swapped redundancy for four different lenses found one blocker and two near-blockers the identical panel missed, and **every unique high-severity catch came from exactly one lens**. Lens diversity is proven. Model diversity is the axis that prototype could not test, and it is why seats here name a family.

`SKILL.md` is the operator's runbook. This file is the build record: what was chosen, why, and what is missing.

## Layout

```text
skills/ensemble-review/
├── SKILL.md                        # the runbook: panel → dispatch → harness seats → reconcile
├── README.md
├── agents/                         # the personas — one file per lens, no model baked in
│   ├── lens-fidelity.md            # the four with a measured track record
│   ├── lens-buildability.md
│   ├── lens-consistency.md
│   ├── lens-adversarial.md
│   ├── lens-completeness.md        # the five added in the v1 rebuild
│   ├── lens-source-credibility.md
│   ├── lens-security.md
│   ├── lens-alternatives.md
│   └── lens-second-order.md
├── references/
│   └── finding-schema.md           # how to fill a finding; loaded into every persona prompt
├── schemas/
│   ├── review-report.schema.json   # the report envelope and the finding, draft 2020-12
│   ├── judgment-patch.schema.json  # the judgment a mind supplies to the reconciler
│   └── reconciliation.schema.json  # the consensus document reconcile.py writes
├── templates/
│   ├── config.json                 # tier × family → concrete model id
│   ├── models.json                 # the registry: prices, context limits, effort vocabularies, priors
│   └── panels/
│       ├── spec-review.json        # the measured four-lens panel
│       ├── research-report.json    # fidelity, source-credibility, completeness, adversarial
│       ├── design-decision.json    # adversarial, alternatives, second-order — needs no references
│       └── code-review.json        # a named stub: deferred, routes to /code-review
└── scripts/
    ├── dispatch.py                 # one persona × one family → a validated report on disk
    ├── run_panel.py                # claim → materialize → resolve → project → dispatch → manifest
    ├── render_harness_report.py    # the same artifacts for the harness leg
    ├── reconcile.py                # the only writer of both reconciliation files
    ├── refresh_models.py           # pull the OpenRouter catalogue, diff the registry, report what moved
    ├── backends/__init__.py        # load_driver: a package module, or a driver at a file path
    ├── backends/openai_compat.py   # the only shipped driver: POST {base_url}/chat/completions
    ├── lib/paths.py                # the two-root cascade: workspace first, then the read-only package
    ├── lib/seating.py              # tier resolution order, family constraints, re-seating
    ├── lib/report.py               # agent-file parsing, validation, rendering, digests
    ├── lib/reconcile_core.py       # match keys, tiers, judgment-patch merge, verdict
    ├── lib/registry.py             # the model registry: caps, prices, priors, the missing-model error
    ├── lib/budget.py               # the cost and token pre-flight, and the refusal document
    ├── lib/runs.py                 # claim, materialize, hash, the manifest's compare-and-set
    ├── lib/schema.py               # the JSON Schema subset both contracts are checked against
    └── tests/                      # stdlib unittest, no network, no paid call
        ├── test_replay.py          # the deterministic replay of the 2026-09-18 panel
        ├── test_dispatch_retry.py  # the cap, the length retry, the registry gate, backoff
        ├── test_run_lifecycle.py   # claim, materialize, projection, budget gate, CAS, resume
        ├── test_paths.py           # the cascade, the config merge, a panel with the package read-only
        ├── test_seating.py         # the tier order, the constraints, both re-seat paths
        ├── test_catalog.py         # every persona and every panel template, checked as data
        ├── test_panel_selection.py # inference, the references rule, the deferred stub
        ├── test_min_families.py    # the family target, counted at both ends, never a gate
        ├── test_reconcile_revision.py  # the artifact-revision check
        ├── harness.py              # a temp workspace: artifact, config, registry, panel, overrides
        └── fake_backend.py         # a scripted connector, loaded by file path like any driver
```

## Tiers and model picks

`templates/config.json` maps **tier × family** to a concrete model id, and carries two per-model maps beside it: `effort`, keyed by concrete model id because reasoning-effort vocabularies differ by vendor, and `provider_routing`, which is passed through as OpenRouter's request-level `provider` object. The tier is chosen by stakes: `frontier` for a spec, plan or decision that gates a build; `standard` for routine documents and second passes; `fast` for a sanity pass or a dry run. The resolution order is `--model`, then `--tier`, then the seat, the panel, the config's `default_tier`, and last the persona frontmatter; the level that decided each seat lands in the manifest as `tier_source`.

Seven families, picked from the OpenRouter catalogue and a leaderboard pass on 2026-09-18:

| Family | `frontier` | `standard` | `fast` |
| --- | --- | --- | --- |
| claude | `anthropic/claude-sonnet-5` | `anthropic/claude-sonnet-5` | `anthropic/claude-sonnet-5` |
| openai | `openai/gpt-6-astra` | `openai/gpt-5.6-sol` | `openai/gpt-5.6-luna` |
| kimi | `moonshotai/kimi-k3` | `moonshotai/kimi-k3` | `moonshotai/kimi-k3` |
| glm | `z-ai/glm-5.3` | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash` |
| xai | `x-ai/grok-4.6` | `x-ai/grok-4.6` | `x-ai/grok-4.6` |
| google | _empty_ | `google/gemini-3.8-flash` | `google/gemini-3.6-flash` |
| deepseek | `deepseek/deepseek-v4-pro-0813` | `deepseek/deepseek-v4-pro-0813` | `deepseek/deepseek-v4.1-flash` |

**Google's `frontier` cell is deliberately empty.** No Pro-class Google model is on OpenRouter; its ceiling is a Flash model the landscape note places at `standard`. Filling the frontier cell with a Flash model would quietly serve a standard-class model to an operator who asked for frontier. Empty, the seat is **re-seated** onto the next available family and the substitution is recorded — which is also what makes the missing-cell behaviour testable, since this is the one missing cell the shipped config has.

Notes on the picks, because they will go stale and the next person needs the reasoning rather than the answer:

- **Every id was confirmed present in `GET https://openrouter.ai/api/v1/models` before it was written down.** Re-run that check before trusting this table; a spec that names today's model ids is stale in a quarter, and so is a config.
- **`frontier` means the newest general reasoning model, not the cheapest or the most specialised.** Mini, flash-lite, `:batch` and image variants are excluded from `frontier` by construction: batch endpoints trade latency for price and a panel is a foreground operation, and the small variants are exactly the reviewers whose blind spots correlate with each other.
- **Google's ceiling is a Flash model.** OpenRouter's newest Gemini Pro is `gemini-3.1-pro-preview` from February; the 3.5–3.8 line is Flash-only and scores higher at lower cost. Google is therefore the one family with no `frontier` cell at all, and a panel that seats it at `frontier` is re-seated rather than served a standard-class model under a frontier label.
- **`xai` and `kimi` name one model across all three tiers,** because each vendor currently fields one model worth seating. That is not a placeholder; it means a `fast` run does not get cheaper on those seats. Watch the cost line.
- **Non-`pro` variants where a `pro` exists.** `openai/gpt-6-astra-pro` is the same underlying model served with `reasoning.mode: pro`. Paying for a higher reasoning mode on one seat and not the others would make that seat's findings incomparable with the rest of the panel, which is the one thing this skill cannot tolerate. Same reasoning for `gpt-5.5-pro`.
- **The Claude family is Sonnet 5 at every tier, and the default panel seats no Claude family.** Decided 2026-09-18 after the first dogfood run: the Fable seat cost $3.58 of a $4.80 panel and its unique findings were matched by a $0.34 GLM seat. The stronger reason is decorrelation: the host session that authors and reconciles these documents is a Claude model, so a Claude reviewer is the seat most correlated with the artifact. Seat `claude` explicitly when a second Claude opinion is wanted. `run_panel.py --skip-claude` still exists for running that seat as an Opus harness subagent on the subscription; it is **off by default**.

## The lens catalog

Nine personas, one file each in `agents/`, and the catalog is closed: `scripts/tests/test_catalog.py` asserts that `agents/` holds exactly these nine and nothing else, so a lens cannot be added without the panel templates and this list being updated with it.

The four with a measured track record are the 2026-06-26 `annotate` full-spec set — fidelity, buildability, consistency, adversarial. The five added in the v1 rebuild are `completeness`, `source-credibility`, `security`, `alternatives` and `second-order`, which the seed and the v3 spec both name.

**Where each new lens stops is the design decision, not what it asks.** Four of the five overlap something the adversarial lens already touches, and the boundary is drawn the same way every time: the adversarial lens goes where it can win, and the new lens sweeps systematically whether or not anything is there to win.

| Lens | Its half of the boundary | The neighbour's half |
| --- | --- | --- |
| `completeness` | Sweeps the whole surface against what a document of this kind owes its reader | `adversarial` Part B sweeps for the absence its attack needs |
| `source-credibility` | Judges whether the source deserves to be cited at all — authority, currency, independence, sufficiency | `fidelity` judges whether the artifact reads the source correctly |
| `security` | Assumes the design is right and asks who can abuse it: assets, actors, trust boundaries, blast radius | `adversarial` asks whether the design is wrong |
| `alternatives` | Enumerates every fork the document passed through and reports the ones with no argument attached | `adversarial` reaches for a foreclosed alternative only when it breaks the central claim |
| `second-order` | Starts where the build ends: month two, year two, the first time somebody changes it | `buildability` stops at the moment the thing is built; `adversarial` attacks the bet as it stands |

**Overlap is not duplication and the bodies say so.** Every persona is told not to trim its findings to avoid a neighbour, because two independent lenses landing on one defect is the corroboration signal the panel exists to collect and it cannot be produced by holding back.

**Three sentences are byte-identical across all nine bodies**, and a test asserts it: the severity calibration, the `judgment-call` rule and the verbatim-quote rule. A lens calibrated differently from its neighbours makes the panel's agreement counts mean something different per seat, which is the one thing this skill cannot tolerate. Frontmatter is identical too — `model: frontier`, `output_type: json_report`, `context: [finding-schema.md]`, `tools: []`.

**`model: frontier`, not `model: high`.** Framework §6 wants a frontmatter `model` that names a key in the tiers map; the personas shipped through stage 2b with the abstract `high`, which names no tier here, so the lowest level of the tier order could never fire. It fires now: a seat with no `--model`, no `--tier`, no seat tier, no panel tier and no config `default_tier` resolves to `frontier` and records `tier_source: "persona"`. The framework §8 inversion is unaffected — the config's `default_tier` still beats it, because the tier is a property of the run's stakes rather than of the lens.

## Panel templates and how one is chosen

| Template | Seats | `requires_references` | `min_families` | `verify_web` |
| --- | --- | --- | --- | --- |
| `spec-review` | fidelity, buildability, consistency, adversarial — openai, glm, kimi, xai | `true` | 2 | `false` |
| `research-report` | fidelity, source-credibility, completeness, adversarial — openai, kimi, glm, xai | `true` | 2 | `false` |
| `design-decision` | adversarial, alternatives, second-order — xai, openai, glm | `false` | 2 | `false` |
| `code-review` | none — `"deferred": true`, `"routes_to": "/code-review"` | — | — | — |

Every live template seats **named, distinct, non-Claude families**, for the reason under _Tiers and model picks_: `distinct` and `non-claude` appear in no shipped template and exist for ad hoc composition. `test_catalog.py` resolves all three against the shipped config at all three tiers and checks each clears its own `min_families`.

**Inference, when no `--panel` is given.** A cheap heuristic over the **artifact's filename** — and nothing else — picks `research-report` or `design-decision` when the name says so, else `spec-review`. The heuristic deliberately does not open the document: a heuristic that read the artifact would be a second, unreviewed judgement about it made before any reviewer has seen it, and the operator can always name the panel.

Then the references rule, which overrides the heuristic: **a run with no references never infers a template whose `requires_references` is true.** It falls back to `design-decision`, the one shipped template that needs none, and the substitution is recorded three times over — in the manifest's `panel_inference`, on the console, and in the reconciliation's method caveat by `reconcile_core.method_caveat`, the same way a seat substitution is.

**A named `--panel` does not bend.** `fidelity` and `source-credibility` require a `citation` on every finding, so a seat carrying either with nothing to cite is a composition error, exit 1, refused before the first paid call and naming the seats. Inference exists to avoid that outcome; an operator who named the panel gets told why instead. This replaces the stderr warning v0 printed, which let a run spend four seats' money on a panel whose fidelity seat could not produce a valid finding.

**The two panel-shape refusals happen before the run directory is claimed**, so neither leaves anything behind: `code-review` refuses on the template, and a starved `fidelity` or `source-credibility` seat refuses once the seats resolve and still before the claim. That matters because `lib/runs.py` increments the sequence number on a collision, so a directory left behind here would silently move the operator's retry — the one with `--ref` supplied — into `<name>-2`. Refusals that need more than the panel, the config and the seats come after the claim and leave the directory with what they wrote: the registry and effort gates, a resume fingerprint mismatch, a materialize failure, and the budget refusal, which writes `budget-refusal.json` into it deliberately.

**`verify_web` is declared and off.** Every live template carries `"verify_web": false`, matching the spec's normative template block. No seat has a live web tool in v1, so the flag states the intent rather than switching anything, and `test_catalog.py` requires it on every live template.

## `min_families` is a target

The spec is explicit that it is a target and not a precondition: a panel that cannot meet it runs anyway, lens-diverse only, and the reconciliation says so. The implementation counts distinct families **twice** and keeps both:

| Count | Taken at | Over | Answers |
| --- | --- | --- | --- |
| `seated` | Resolve | every expected seat, harness seats included | what the panel was composed to reach |
| `reporting` | wrap-up | the seats whose reports validated | what it actually reached |

Both land in the manifest as `min_families: {target, seated, reporting, families_seated, families_reporting}`, and `min_families_target` is kept beside them so the manifests written in stage 2a and 2b still read. `--min-families` overrides the template.

Two numbers rather than one, because a panel that seated four families and heard back from one is a different document from a panel that only ever had one, and the tier table has to be read differently in each case. `reconcile_core.method_caveat` prints both, and **one reporting family reads as "lens-diverse only"** — several lenses, one mind, and no cluster in that run can carry cross-family corroboration at any tier. A run that meets its target adds nothing to the caveat.

## `judgment-call` means a fork

On the 2026-09-18 panel that reviewed this skill's own v1 spec, **24 of 29 clusters were all-`judgment-call`**, and 22 of those were disposed something other than `flag-for-human`. A tag that lands on four fifths of a review discriminates nothing, and the reconciler was left with two dozen clusters that all looked like decisions somebody owed an answer to.

`references/finding-schema.md` and every persona rubric now say the same thing: `change_kind` answers **one** question — can the fix be written out, or does somebody have to decide something first — and it is not a measure of how big the fix is. `judgment-call` means a **design fork**: two defensible answers, the references silent on which is right, and the choice changes what gets built. "The artifact left this thin" is not one. A gap has a determinate fix, so it is `should-fix` or `blocker` by consequence with the replacement drafted as a `literal-edit` — **however large that replacement is**, a whole missing section included, because a section somebody can draft is a section somebody can draft.

**The discriminator is a tag, and that is a deviation worth naming.** The spec's remedy for a thin spot is a `gap` change kind, and the enum in `schemas/review-report.schema.json` has exactly two values that the frozen replay fixture depends on. So the enum is unchanged and the tag carries the distinction instead: **every `judgment-call` finding carries `fork`, and none may carry `gap`**. `gap` stays a live tag on the determinate fixes it describes. The schema doc carries a worked positive (a real fork from that run) and a worked negative (the same run's configuration finding, which is a gap that was filed as a judgment call).

**Enforced at ingest, and only at ingest.** `lib/report.py` rejects a `judgment-call` with no `fork` tag, and rejects one carrying `gap`, at the moment a report first enters the system — a model's response in `dispatch.py`, a subagent's JSON in `render_harness_report.py` — where the reviewer is still there to be handed the error in the repair re-ask. Reading a stored report back applies none of it, because the frozen replay fixture's 44 untagged judgment calls predate the rule and a resume must not re-dispatch a seat over a rule its report could not have known.

**What the reconciler does with the tag.** A cluster whose findings are all `judgment-call` is forced to `flag-for-human` on a patch from `synthesis` — unless every one of those findings is tagged `gap`, in which case it is a determinate fix somebody filed under the wrong change kind and it goes on the fix list. Untagged still goes to the human: silence says nothing, and the safe reading of silence is the one that asks. Since ingest rejects a `gap`-tagged judgment call, that branch only ever fires for a report written before the rule or by hand — which is exactly the case it exists for.

## The driver seam

The HTTP call lives in `scripts/backends/openai_compat.py`, loaded by the provider entry's `"type"` field. `dispatch.py` contains no backend-specific logic.

OpenRouter serves Anthropic's models through the same OpenAI-compatible `/chat/completions` endpoint as everyone else's, so **one driver covers every family on the panel**. A native `anthropic` driver against `api.anthropic.com`, or a `harness` driver that shells out to a local agent CLI, would each be one more file in `backends/` and a `"type"` change in the config — no change to `dispatch.py`. That is the whole point of the seam, and it is the reason the framework's §9 shape is preserved here (minus the tool arguments, since these personas are single-turn and tool-less).

`dispatch()` is the framework-shaped entry point returning a string. `dispatch_detailed()` is the same call returning the decoded response alongside the text, which is how usage and cost reach the `_meta` block and the manifest.

## The workspace cascade

Framework principle 12's carrier is `scripts/lib/paths.py`. Every file a run loads is searched for in two roots, in order: `<workspace>/.agents/ensemble-review/`, then the package.

**What "read-only" covers, exactly.** No **run artifact** is ever written into the package: reports, renderings, manifests, judgment patches, reconciliations and run directories all land in the reviewed project's tree. Two things are outside that claim and are outside the test that enforces it. CPython writes `__pycache__` bytecode beside the scripts and silently skips it when the directory is unwritable, which is why `test_paths.py` excludes `__pycache__` from both the copy it chmods and the fingerprint it compares. And `refresh_models.py` writes `templates/models.json` when no workspace registry exists — deliberate, because refreshing a price cache is maintenance rather than a run, and a project that wants the package untouched gives itself a workspace `models.json`.

**Files replace whole, first hit wins.** Personas, panel templates, references, schemas, backend drivers and the model registry. There is no per-field merge for a file: a workspace copy is the one the run used, and the manifest's `roots` block says so. **`config.json` is the exception and deep-merges**, package first and workspace last, at connector / tier / family / model-id granularity — a fragment carrying `{"openrouter": {"tiers": {"standard": {"glm": "x"}}}}` changes that one cell and leaves the other cells, the other tiers and the connector's own keys alone.

The workspace root mirrors the _logical_ category rather than the package's internal nesting: `config.json` and `models.json` at the top, `panels/`, `backends/`, and `agents/`, `references/`, `schemas/` under their own names. The spec gives one example (`.agents/ensemble-review/backends/azure_openai.py`) and no full layout, so this is a build decision; each category also accepts the package-shaped path under either root, so a workspace that mirrors the package resolves too.

That directory is where the **egress control** lives, which is the reason the cascade exists at all. A project reviewing confidential artifacts drops a driver beside a config fragment binding its families to it; `Paths.driver_ref()` hands the absolute path to `importlib`, so nothing in `scripts/backends/__init__.py` is edited and the shipped package is untouched. A `type` that resolves in neither root is a composition error, exit 1, naming both roots and every path it tried.

`--config` and `--models` stay **operator paths**: given explicitly they are read as given, never merged, and recorded as such. `--workspace` is passed straight through to every `dispatch.py` subprocess, so the child resolves exactly the files the parent did.

The manifest's `roots` block is a map of loaded file to root — `{"search": […], "files": {"config": …, "persona:lens-fidelity": …}, "workspace_overrides": […]}` — chosen over a per-category root because a category can legitimately split across roots (one overridden persona, three packaged ones) and a per-category answer would have to lie about that.

## Seats: tiers, constraints and re-seating

`scripts/lib/seating.py` owns both orderings, because a second builder has to reproduce a run from the same panel and the same config.

The **tier order**, first match wins: `--model <seat-id>=<model-id>` → `--tier` → the seat's `tier` → the panel's `tier` → the config's `default_tier` → the persona frontmatter's `model`. The last level only fires when the frontmatter names a key in the tier map, and since the v1 rebuild every shipped persona carries `model: frontier`, which is one — so a seat with nothing above it resolves to `frontier` and records `tier_source: "persona"`. The level that decided each seat is recorded as `tier_source`.

The **family passes**, over the seats in template order so the result does not depend on how the template was written: every named family reserves first, across the whole template; then each named seat whose family has no cell re-seats; then `non-claude` takes the first non-`claude` family in declaration order that has a model and nobody holds; then `distinct` takes the first family nobody else holds, reserving in both directions, always last.

**Missing cells are a pass of their own, and that is load-bearing.** Resolving one inside the named pass lets it take a family a later named seat is about to ask for by name: on the shipped config at `frontier`, `[google, claude]` put two seats on claude while openai sat free, and `[claude, google]` re-seated onto openai — the same panel, two answers, decided by template order. `test_seating.py` runs both orders and asserts they agree and that no family is doubled while a free one exists.

Three degradations, each recorded as a `substitution` in the manifest seat record and each named in the reconciliation's method caveat by `reconcile_core.method_caveat`:

| Condition | Behaviour | `kind` |
| --- | --- | --- |
| Family has no model at the resolved tier | Re-seat once onto the next eligible family in declaration order | `missing_cell` |
| Provider refuses the model at dispatch — 404 or 400 naming it | The same move, once, against the seat's **request** rather than the family it held, so a `non-claude` seat stays non-claude | `model_unavailable` |
| Every family the constraint allows is already held | Fall back to the least-held eligible family; the run does not fail | `constraint_unsatisfied` |

**No re-seat lands on `claude` unless the seat asked for `claude` by name.** All three draw their candidates from `seating.reseat_candidates()`, which drops the failed family and drops claude. Claude is first in the config's declaration order, so without the rule every first re-seat landed on it — and the default panel seats no Claude family on purpose, because the host session that authors and reconciles is itself a Claude model. A substitution that quietly added one would undo that decision with nobody choosing it. When claude is the only _free_ family the seat doubles up on a non-claude family instead; only a tier with no other family at all is a composition error. Owner ruling, 2026-09-18.

An ordinary provider error is **not** re-seated. Re-seating spends a second seat's money, so it turns on a narrow signal rather than on a guess about what went wrong. A context overflow is never re-seated either, per the spec, and is reported as under-seated.

**A resume adopts a recorded `model_unavailable` substitution** rather than re-deriving the seat from the panel, which still names the refused family. `missing_cell` and `constraint_unsatisfied` are not adopted: they are properties of the config and the panel, so re-resolving reaches the same answer, and adopting them would freeze a stale config decision into the run.

A seat's `reviewer_id` is minted from its lens and **what it asked for** — `fidelity-openai`, `buildability-non-claude` — never from what it got. A constrained seat's family depends on what else the panel seats, so an id derived from it would move between runs of the same panel, and a seat re-seated mid-run would change its own id, its report filename and its manifest key half way through.

## Report schema and validation

`schemas/review-report.schema.json` is JSON Schema draft 2020-12 and carries exactly the spec's two tables: the report envelope, and the finding with its thirteen fields. `references/finding-schema.md` is the prose version that gets loaded into every persona's system prompt, so the reviewer and the validator are reading the same contract.

Validation is hand-written in `scripts/lib/report.py` — required fields, types, enums, length caps, the `F<n>` id pattern — so the skill runs on any Python 3 with nothing installed. It also enforces the rules the schema can only state in prose: a `literal-edit` finding must carry a `literal_edit` block, every finding from the **`fidelity`** and **`source-credibility`** lenses must carry a `citation`, and — at ingest only — every `judgment-call` finding must carry the `fork` tag and must not carry `gap`. A report that fails gets **one repair re-ask** carrying the validation errors back; a second failure writes the raw response to `<reviewer-id>.invalid.txt` and exits non-zero, and the seat is flagged as missing rather than silently dropped.

**Both citing lenses are held to the citation rule at both ends.** `run_panel.py` refuses to seat `fidelity` or `source-credibility` with no references, as a composition error before the first paid call; `report.py` rejects a finding from either lens with a null `citation`. The two halves close the same rule, and closing only the first one left a seated `source-credibility` seat able to return uncited findings that validated.

The dispatcher **overwrites the audit fields** — `schema_version`, `reviewer_id`, `lens`, `family`, `model`, `leg`, `artifact`, `references` — with what it knows, before validating. The model cannot get the audit record wrong, and validation bites on the part that is actually the reviewer's work: verdict, summary, findings, method notes.

## Reconciliation and the judgment patch

`reconcile.py` is the **only** writer of `reconciliation.json` and `reconciliation.md`, in every mode. It splits the algorithm along the line that can actually be drawn: what a script can compute, and what it cannot.

The script computes the clustering on two keys it can reproduce — **normalized location equality** and **quote overlap at a 60-character longest-common-substring threshold** — then the agreement tiers, the final `BK` / `SF` / `NH` ids and the run-level verdict. What it cannot compute arrives as a **judgment patch** at `<run-dir>/judgment.json`, validated against `schemas/judgment-patch.schema.json`: claim joins (two findings naming one defect from different anchors), splits (one anchor carrying two arguments), singleton labels, severity arbitrations, dispositions, contradictions, canonical-edit acceptances and the method caveat. Run the script with no patch and it writes `judgment-request.json` — one entry per provisional cluster with the fields it owes — and exits 3.

Order is load-bearing and is fixed in `lib/reconcile_core.py`: provisional clusters → provisional tiers → merge the patch's splits and joins → **recompute every tier from the post-merge membership** → apply labels, severities and dispositions → validate → mint ids. A tier computed before the joins is a tier computed against the wrong member set, so two patch errors fall straight out of the recompute: a post-merge singleton with no label, and a label that lands on a cluster that is no longer one. Either one writes nothing and names the failing entry.

Every cluster records the `match_key` that joined it and a `judgment` flag that is true exactly when the join was semantic, so a reader can see which clusters rest on a mind. `scripts/tests/test_replay.py` is the control: it feeds the four reports of the 2026-09-18 panel and a `judgment.json` transcribed from that run's hand reconciliation through the core and checks membership, tiers, labels, spreads and the verdict against the hand document. No models are called and nothing is written.

## Smoke test

Run on 2026-09-18 against a 40-line sample spec with two planted contradictions — a digest cap stated as 2000 characters in one section and enforced at 2500 in another, and a "three tiers" sentence followed by a four-item list. One persona (`lens-consistency`), one call per family, `fast` tier, `--max-tokens 6000`.

| Family | Model | Cost | Reasoning tokens | Repairs | Findings | Both planted defects found |
| --- | --- | --- | --- | --- | --- | --- |
| claude | `anthropic/claude-sonnet-5` | $0.0582 | 1,797 | 0 | 4 | yes |
| openai | `openai/gpt-5.6-luna` | $0.0036 | 702 | 0 | 3 | yes |
| kimi | `moonshotai/kimi-k3` | $0.0967 | 3,340 | 0 | 4 | yes |
| glm | `z-ai/glm-5.3-flash` | $0.0075 | 11,519 | 0 | 4 | yes |
| xai | `x-ai/grok-4.6` | $0.0429 | 3,610 | 0 | 4 | yes |
| google | `google/gemini-3.6-flash` | $0.0168 | 2,516 | 0 | 2 | yes |
| deepseek | `deepseek/deepseek-v4.1-flash` | $0.0064 | 6,000 | 1 | 6 | yes |

**All seven families found both planted contradictions.** Six of seven also independently raised a third defect nobody planted — the cap's stated rationale does not survive its own arithmetic (three 2000-character digests exceed the 5500-character channel it cites). That is the corroboration signal the skill is built to produce, arriving unprompted on a 40-line toy.

Six of seven rated the cap conflict `blocker`; `glm` rated it `should-fix`. A one-step severity spread across families on a defect every family found is exactly what the reconciliation's _arbitrate severity_ step exists to resolve, and it showed up on the first run.

`glm` and `deepseek` both failed at `--max-tokens 6000` — GLM burned the cap on reasoning tokens and returned truncated JSON twice, DeepSeek returned an empty `content` with everything in `reasoning`. Both pass at 20000. Two fixes came out of that: the driver now falls back to the `reasoning` field when `content` is empty, and `_meta.reasoning_tokens` is recorded so the cause is visible rather than guessed at. GLM's 11,519 reasoning tokens against a 40-line document is the number to remember when setting a cap.

**Frontier proof call:** `lens-consistency` on `anthropic/claude-fable-5.1`, `--max-tokens 4000`, $0.5445 including one repair re-ask. It found both planted defects plus the arithmetic one. The repair was caused entirely by the 4000-token cap truncating the JSON mid-string — see the rebuild items.

**Parallel panel:** `run_panel.py --panel spec-review --skip-claude --tier fast`, three seats (`buildability-openai`, `consistency-kimi`, `adversarial-glm`) in parallel, 219 s wall clock, $0.2137, zero failures. `manifest.json` recorded every seat with its tier, model, verdict, finding count, repair count, usage, reasoning tokens and cost, plus the skipped `fidelity-claude` seat as `leg: harness, status: pending`. The harness leg was then exercised by feeding a report through `render_harness_report.py`, which validated it, stamped `leg: harness` and wrote the matching `.md`; a deliberately malformed report was rejected with six named errors and exit 3.

Total recorded spend across every smoke call, including the discarded first round run before the final model picks landed: roughly **$1.10**. The `.smoke/` directory was deleted afterwards.

## What v0 does not have

This is a deliberate subset of `design/v1-spec.md`, built to be usable today and fed back through itself. Absent, not stubbed:

- **`apply_fixes.py` and the auto-apply gate** — nothing is written back to the artifact. `change_kind` and `literal_edit` are collected, and `reconciliation.json` carries a `canonical_edit` and an `edit_conflict` flag per cluster so the gate can be built later, but no code reads them today.
- **`install.sh`** — no installer. The key check is `dispatch.py --help`.
- **The `synthesis` persona and autonomous mode** — `reconcile.py` accepts a judgment patch from either author, but nothing here produces one unattended: no reconciler persona, no unattended run.
- **Mid-flight budget metering** — the projection is a dispatch gate only. Nothing meters spend as seats return, so a panel that overruns its projection runs to completion.
- **`mode: identical`** — the knob does not exist, so it is not refused by name.
- **`verify_web`** — every live template declares it `false` and nothing reads it, and the composition error the spec requires for `verify_web: true` on a tool-less seat is not implemented, so a workspace panel setting it true runs unrefused.

Built since the first cut of this list: the completion cap and its length retry, the per-model cap floor, the model registry and `refresh_models.py`, the cost and token pre-flight with its budget gate and `budget-refusal.json`, content-hash revisions with a read-only `inputs/` directory, resume with a compare-and-set claim, the two-root workspace cascade, the tier resolution order, the `non-claude` and `distinct` constraint resolvers, re-seating on a missing cell or an unreachable model, the five remaining lenses, the three remaining panel templates, panel inference with its references rule, and `min_families` counted at both ends.

## Rebuild items

### Fixed after the first real run

The first panel against a real document (`design/v1-spec.md`, 2026-09-18, four seats, $4.80) exposed four defects in the tooling itself. They were fixed in `scripts/` rather than deferred with the rest of the rebuild, because each one destroyed or hid information the run had already paid for:

- **Length caps truncate; they no longer reject.** The GLM seat was lost over a 696-character summary and two quotes at 336 and 301 — 1, 36 and 96 characters past their caps, on a report whose substance was fine. `lib/report.py` now trims an over-long string to its cap with a trailing ellipsis, records the trim in `_meta.truncated` (field path, cap, original length, finding id) and renders a truncation count in the Markdown run line. Rejection is reserved for what a trim cannot repair: missing required fields, wrong types, bad enum values. The rule is in the shared validator, so it applies to the first attempt, the repair re-ask and `render_harness_report.py` alike.
- **Every attempt is recorded.** `_meta.attempts` carries one row per call — attempt number, `ok` or the list of validation errors, usage, cost, `finish_reason`, driver notes, response id — and the manifest surfaces a per-seat `attempts` count and `first_attempt_errors`. Two seats in that run needed a repair and the manifest could not say why. This replaces `_meta.usage_all_attempts` and `_meta.repair_errors`, which the new block subsumes.
- **A failed seat carries its cost.** GLM's two attempts cost about $0.34 by the dashboard and $0 by the manifest. `dispatch.py` now writes `<reviewer-id>.failed.json` — the same `_meta` block a passing seat gets, plus the validation errors and a pointer to the raw response — beside `<reviewer-id>.invalid.txt`, and `run_panel.py` folds its usage, cost and attempt summary into the seat record and into `cost_usd_total`. A failed seat's spend is now in the run total and on the console line.
- **`render_harness_report.py` no longer assumes the harness leg.** A salvaged OpenRouter report came out labelled `leg: harness, tier: standard`, which is a false audit record. `--leg {harness,openrouter}` (default `harness`) and `--tier` say what the seat was; a `leg` or `_meta.tier` already in the JSON is believed over the flag and a note goes to stderr when the two disagree; `_meta.provider` follows the resolved leg; and an unknown tier on the OpenRouter leg stays unset rather than being invented.

### Fixed in the v1 rebuild

- **`max_tokens` is a knob, and `length` is retried before repair.** Every call carries an explicit cap, `--max-tokens` sets it per run (default 32000), and a per-model `min_max_tokens` floor in the registry raises it for the seat that needs it — Kimi K3 ships at 64000, because run 2's consistency seat spent a whole 32000-token cap on reasoning and returned nothing. A `finish_reason` of `length` is retried **once at double the cap with a fresh prompt**: the truncated bytes are discarded rather than quoted into a 90k-token repair prompt, which is what that run paid $0.66 for. A second `length` falls through to the repair path. Every call's cap, finish reason, usage and cost land in `_meta.attempts`, failed calls included.
- **Reasoning-effort vocabularies are now data, and the effort map is live.** `templates/models.json` carries each model's `effort_vocabulary`, refreshed from the catalogue's own `reasoning.supported_efforts`, and `templates/config.json` now carries the spec's normative `effort` map keyed by concrete model id. `dispatch.py` sends the string as `reasoning.effort`; a model absent from the map is sent no effort parameter at all. An effort **outside** that model's vocabulary is a **composition error, exit 1**, naming the model and the allowed values — refused at Resolve by `run_panel.py` and again by `dispatch.py`. It used to be dropped with a warning, which dispatched the seat at whatever depth the provider defaults to; a panel whose seats ran at unintended depths is not the comparison this skill exists to make. A vocabulary the registry simply has not learned is not the same as an unsupported value and is still allowed through. The manifest records the effort actually sent per seat.
- **A projection exists, and it is built on priors rather than on prompt size.** `lib/budget.py` prices each seat as `input_price × prompt tokens + output_price × the model's output-token prior`, plus a 50% repair allowance per seat and one synthesis call. Against run 2's numbers that lands near $1.5 on a run that cost $1.47 and was projected at $1.00 by prompt size alone. Prompt tokens are approximated at four characters per token — no tokenizer dependency — and every figure is labelled an estimate wherever it is printed.
- **The package is read-only and the workspace overrides it.** `lib/paths.py` resolves every packaged file through two roots — `<workspace>/.agents/ensemble-review/` then the package — with whole-file replacement for files and a deep merge for `config.json`. `run_panel.py`, `dispatch.py`, `reconcile.py` and `refresh_models.py` all go through it, and `run_panel.py` passes `--workspace` to each `dispatch.py` subprocess so the child reads what the parent resolved. A driver under the workspace root is imported from its path, so a project binds a family to its own connector without editing the package. `scripts/tests/test_paths.py` runs a whole fake-backend panel against a copy of the package with every file and directory stripped of its write bit, and checks afterwards that nothing in it moved.
- **Seats resolve by a stated order, and constraints resolve.** `lib/seating.py` implements the tier order and the four family passes, and `non-claude` and `distinct` work. A missing cell, an unsatisfiable constraint and a model the provider refuses each record a `substitution` rather than failing the run, no re-seat of any kind adds a Claude seat nobody asked for, and `reconcile_core.method_caveat` appends every substitution to the reconciliation's method caveat — the judgment supplier cannot know about them, because both re-seat paths are decided by `run_panel.py` after the reports are written.
- **The catalog is complete, and the panel is chosen rather than assumed.** Five lenses were written (`completeness`, `source-credibility`, `security`, `alternatives`, `second-order`), three panel templates landed beside `spec-review`, and the personas were retiered from `model: high` to `model: frontier` so framework §6's frontmatter level of the tier order is live. `--panel` is now optional: the template is inferred from the artifact's name and a run with no references never lands on one that needs them. A `fidelity` or `source-credibility` seat with nothing to cite is a composition error rather than a stderr warning, `min_families` is counted at both ends and named in the method caveat, and `judgment-call` was sharpened to mean a design fork rather than a thin spot. The four sections above carry the reasoning; `test_catalog.py`, `test_panel_selection.py` and `test_min_families.py` carry the tests.
- **The artifact has a revision, and three mechanisms use it.** `run_panel.py` copies the artifact and every reference into a read-only `<run-dir>/inputs/`, and a revision is the SHA-256 of the bytes written there, with the commit id beside it when that file's tree is clean. Seats read those bytes; resume refuses when the input fingerprint has moved; `reconcile.py` hashes the artifact it resolves against the manifest's `artifact_revision` and refuses on a mismatch. **In the ordinary case that refusal never fires**, because `reconcile.py` resolves the run's own `inputs/` copy first and those bytes cannot change — editing the working-tree document afterwards is fine and always was. It fires only when `--artifact <path>` overrides the resolution, or when the `inputs/` copy is gone and the working-tree file has moved on since. A null revision is unpinned, warned about, and allowed through, which is what keeps the frozen replay fixture running.

### Still open

- **Reasoning tokens bill as output, and several frontier models will not let you opt out.** `_meta.reasoning_tokens` records them per seat from `usage.completion_tokens_details.reasoning_tokens` when the provider reports it. They are often the larger half of a seat's bill and are invisible in the completion, which is why the projection is built on a measured output-token prior rather than on prompt size.
- **The priors are four measurements and eight defaults.** `openai/gpt-5.6-sol`, `z-ai/glm-5.3-flash`, `moonshotai/kimi-k3` and `x-ai/grok-4.6` carry run 2's accepted-attempt completion tokens and `openai/gpt-6-astra` carries run 1's; every other model in the registry carries a flat 16000 labelled `default`. A projection for a panel of unmeasured models is a guess with a receipt.
- **The repair re-ask resends the whole user message.** It has to, because the reviewer must re-quote verbatim — but it roughly doubles the cost of a seat that needed one. A cheaper repair that sends only the previous output and the errors would save money and risk fabricated quotes; measure before choosing.
- **Panel inference reads a filename, not a document.** The heuristic is three word lists over the artifact's own name, which is cheap, reproducible and wrong whenever a document is named for its subject rather than its kind — a research report called `caching.md` infers `spec-review`. The references rule is the safety net that matters, and it is the one that fires; the heuristic is a convenience and the operator can always pass `--panel`. Whether the heuristic should look at the document's headings is undecided, and doing so would put an unreviewed reading of the artifact ahead of every reviewer's.
- **`min_families` is counted and named, never acted on — deliberately.** The spec makes it a target rather than a precondition, so nothing re-seats to reach it and nothing refuses for missing it. What is genuinely absent is a way to _ask_ for more families: composing a panel across more families is still a hand edit of the template, and `--min-families 3` on a three-family panel records the shortfall rather than seating a fourth.
- **`provider_routing` is passed through on thin evidence.** The shipped map carries the spec's one entry, for `z-ai/glm-5.3`, and the driver sends it as OpenRouter's request-level `provider` object. The per-model endpoint data that would justify a routing choice — latency, throughput, quantization and price per host, from `GET /api/v1/models/{author}/{slug}/endpoints` — is a second pass of `refresh_models.py` that nothing runs yet, so the values are hand-picked.
- **A runtime re-seat spends past the projection.** The budget gate is a pre-flight over the seats as composed; a seat re-seated onto another family makes a call the projection never priced, on a model that may cost more than the one it replaced. Nothing re-gates. That is the same gap as mid-flight metering, one level down, and it is part of why the re-seat is capped at once per seat.
- **`dispatch.py` exit 5 is not in the spec's exit-code table.** It is internal signalling — the child telling the parent that the provider refused the model rather than that the reviewer wrote a bad report — and `run_panel.py` consumes it and never re-emits it, so no panel run returns 5. An operator invoking `dispatch.py` directly on an unserved model will see it.
- **A refused model's first call is recorded as a failure, not as spend.** `dispatch.py` returns before writing `<reviewer-id>.failed.json` when the provider never answered with a body, so a re-seated seat's manifest record carries the re-seated dispatch's cost and not the refused one's. A 404 costs nothing, so this is right today and would be wrong the moment a provider starts billing for one.
- **The manifest's compare-and-set is guarded by a lock directory, not by a transaction.** `manifest.lock` is an exclusive `mkdir` held across one read-modify-write, and a lock older than five minutes is broken so a crashed run cannot wedge a resume. That is enough for the two cases this skill has — several seat threads in one run, and two resumes on one directory — and it is not a distributed lock.
- **Per-seat `timeout_s` is unimplemented.** The spec gives it a 900-second default and a defined behaviour — expiry flags the seat missing at stage `timeout` and the panel continues — and `run_panel.py` passes no timeout to the `dispatch.py` subprocess at all. A hung provider hangs that seat, and with it the `as_completed` loop, until the process is killed. `MISSING_STAGE` already maps a `timeout` status, so the reconciliation half is ready for the run half that is missing.
- **The exclusive-mkdir claim does not stop two runs sharing a directory; the seat compare-and-set does.** The spec says two concurrent runs on one artifact can never write into the same directory, and the claim alone delivers that only for a directory that does not yet exist. Because an existing run directory **resumes** by design, two runs both naming the same `--out` do share it. What actually protects the money is one level down: a seat moves `pending`/`failed` → `dispatching` by compare-and-set before its first paid call, so only one of the two runs pays for any given seat, and the other reports it held.

## Conventions

The skills repo is public. No key value appears in any file here: `dispatch.py` resolves the OpenRouter key through `skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY`, falling back to `$OPENROUTER_API_KEY`, and exits naming both paths it tried if neither answers. Scripts are standard-library Python 3 with a `main()` and `argparse`. Reports never land in the session scratchpad — harness subagents cannot write there — so every run directory lives inside the reviewed project's tree.

# panel-review

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
skills/panel-review/
├── SKILL.md                        # the runbook: panel → dispatch → judge → reconcile → apply
├── README.md
├── install.sh                      # python 3.10+, the key through dispatch.py's own chain, the registry seed
├── agents/                         # the personas — one file per lens, no model baked in
│   ├── lens-fidelity.md            # the four with a measured track record
│   ├── lens-buildability.md
│   ├── lens-consistency.md
│   ├── lens-adversarial.md
│   ├── lens-completeness.md        # the five added in the v1 rebuild
│   ├── lens-source-credibility.md
│   ├── lens-security.md
│   ├── lens-alternatives.md
│   ├── lens-second-order.md
│   └── synthesis.md                # the one non-lens persona: the judgment supplier for unattended runs
├── references/
│   ├── panel-design.md             # is this worth a panel; how to choose seats; what it costs; where the bytes go
│   ├── finding-schema.md           # how to fill a finding; loaded into every lens prompt
│   ├── dispatch.md                 # both legs, the blinding rules, the three retry paths, the digest cap
│   ├── reconciliation.md           # the seven steps as operator instructions; also synthesis.md's context
│   └── auto-apply.md               # the five-condition gate, the threat model, the audit trail
├── schemas/
│   ├── review-report.schema.json   # the report envelope and the finding, draft 2020-12
│   ├── judgment-patch.schema.json  # the judgment a mind supplies to the reconciler
│   └── reconciliation.schema.json  # the consensus document reconcile.py writes
├── templates/
│   ├── config.json                 # run-wide defaults: the connector, the tier, the effort level, the two declaration orders
│   ├── connectors/openrouter.json  # one endpoint per file: driver, base URL, key source, catalogue, routing, billing posture
│   ├── connectors/harness.json     # the session's own agent harness as an endpoint: subscription, no key, no URL
│   ├── connectors/zai-coding.json  # the owner's z.ai GLM coding plan: subscription, dispatched, $0 seats, no catalogue
│   ├── models/<author>__<slug>.json # one model per file: catalogue facts plus the choices — connector, family, tiers, effort map
│   └── panels/
│       ├── spec-review.json        # the measured four-lens panel
│       ├── research-report.json    # fidelity, source-credibility, completeness, adversarial
│       ├── design-decision.json    # adversarial, alternatives, second-order — needs no references
│       ├── draft-review.json       # the draft pass: the same four lenses, all on harness Claude, --draft only
│       └── code-review.json        # a named stub: deferred, routes to /code-review
└── scripts/
    ├── dispatch.py                 # one persona × one family → a validated report on disk; the shared call machinery
    ├── run_panel.py                # claim → materialize → resolve → project → dispatch → manifest → judge → apply
    ├── render_harness_report.py    # the same artifacts for the harness leg
    ├── reconcile.py                # the only writer of both reconciliation files; the Judge stage
    ├── apply_fixes.py              # the five-condition gate, all-or-nothing, and applied.md
    ├── refresh_models.py           # pull the OpenRouter catalogue, diff the registry, report what moved
    ├── backends/__init__.py        # load_driver: a package module, or a driver at a file path
    ├── backends/base.py            # the connector contract: what a driver exports and what it returns
    ├── backends/openai_compat.py   # the one calling driver: POST {base_url}/chat/completions
    ├── backends/harness.py         # the harness leg's driver, written as a refusal: it never calls anywhere
    ├── lib/paths.py                # the three-root cascade: project, this machine, then the read-only package
    ├── lib/connectors.py           # connector files, the billing postures and the spend gate
    ├── lib/drafts.py               # the draft pass: the harness connector, the refusals, the spawn block, the caveat
    ├── lib/seating.py              # tier resolution order, family constraints, re-seating
    ├── lib/judge.py                # who supplies the judgment, where it is seated, what it is shown
    ├── lib/report.py               # agent-file parsing, validation, rendering, digests
    ├── lib/reconcile_core.py       # match keys, tiers, judgment-patch merge, verdict
    ├── lib/registry.py             # the model registry: caps, prices, priors, the missing-model error
    ├── lib/budget.py               # the cost and token pre-flight, and the refusal document
    ├── lib/runs.py                 # claim, materialize, hash, the manifest's compare-and-set
    ├── lib/schema.py               # the JSON Schema subset both contracts are checked against
    └── tests/                      # stdlib unittest, no network, no paid call
        ├── test_replay.py          # the deterministic replay of the 2026-09-18 panel
        ├── test_dispatch_retry.py  # the cap, the length retry, the registry gate, backoff, the driver contract
        ├── test_run_lifecycle.py   # claim, materialize, projection, budget gate, CAS, resume
        ├── test_paths.py           # the cascade, the config merge, a panel with the package read-only
        ├── test_seating.py         # the tier order, the constraints, both re-seat paths
        ├── test_catalog.py         # every persona and every panel template, checked as data
        ├── test_draft_mode.py      # --draft: the refusals, the $0 projection, the spawn block, the caveat
        ├── test_panel_selection.py # inference, the references rule, the deferred stub
        ├── test_min_families.py    # the family target, counted at both ends, never a gate
        ├── test_reconcile_revision.py  # the artifact-revision check
        ├── test_synthesis.py       # the Judge stage: the scripted patch, the repair, the two refusals
        ├── test_apply_fixes.py     # the five conditions, all-or-nothing, the audit log
        ├── test_install.py         # install.sh in a subprocess, PATH and env controlled, no network
        ├── harness.py              # a temp workspace: artifact, config, registry, panel, overrides
        └── fake_backend.py         # a scripted connector, loaded by file path like any driver
```

## Tiers and model picks

The **tier × family** map is derived from the model files rather than written anywhere: each `templates/models/<slug>.json` names its `family` and the `tiers` it plays, and `config.json`'s `family_order` and `tier_order` fix the declaration order the seating passes walk. The same file carries that model's `effort` map, from the abstract levels `light` / `standard` / `deep` onto that model's own rungs, because reasoning-effort vocabularies differ by vendor and only the model's own file can hold the translation. `provider_routing` moved to the connector file, where it is passed through as OpenRouter's request-level `provider` object. **The map is derived per connector**, which is what makes a second endpoint a one-key choice: `derive_tiers` filters to the models naming the connector the run resolved, so the shipped `zai-coding` model file (`glm-5.3`, the z.ai coding plan's own spelling of the id) sits in the registry without touching the OpenRouter map at all. A project that wants its GLM seat on that subscription writes `{"default_connector": "zai-coding"}` into `<project>/.config/panel-review/config.json` and changes nothing else; its seats then price at $0 and the spend gate does not ask. Because that map holds one cell, such a run is single-family — a multi-family template collapses onto `glm` through the re-seat rule rather than refusing — so it is a draft-grade pass, not a corroborating panel. The tier is chosen by stakes: `frontier` for a spec, plan or decision that gates a build; `standard` for routine documents and second passes; `fast` for a sanity pass or a dry run. The resolution order is `--model`, then `--tier`, then the seat, the panel, the config's `default_tier`, and last the persona frontmatter; the level that decided each seat lands in the manifest as `tier_source`.

**Pointing one family at your own model, at one tier, is one dropped file.** Write a model file that names that family and that tier into `~/.config/panel-review/models/` or the project's `.config/panel-review/models/`, and it takes the cell: the outermost root that _declares_ `tiers` wins a contested one, and the model it displaced is recorded in the manifest's `roots` block as a `tier_map_overrides` entry. A file that sets only an `effort` rung merges that rung and claims no cell. Two files at the **same** root claiming one cell is a composition error naming both; the other direction — emptying a cell rather than taking it — is `"tiers": []` in your own layer, since a list replaces on merge.

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

Two numbers rather than one, because a panel that seated four families and heard back from one is a different document from a panel that only ever had one, and the tier table has to be read differently in each case. `reconcile_core.method_caveat` prints both, and **one reporting family reads as "lens-diverse only"** — several lenses, one mind, and no cluster in that run can carry cross-family corroboration at any tier. A run that meets its target with every seat reporting adds nothing to the caveat; a run that meets it **while a seat is missing** gets both counts anyway, because "three families agree" reads differently when it is three out of four.

**What else the method caveat appends**, all of it from the manifest and none of it from the judgment patch, which is written by something that has read the reports and not the run: seat and panel substitutions; the seats that did not report, named with their lens, model and recorded reason; the family counts above; and **projected against billed cost** when the run overran its pre-flight or lost a seat, with unbilled upstream inference in its own clause when there is any. Nothing meters spend as seats return — that gap is the spec's — so the comparison is made after the fact or not at all.

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

**The two accounting fields are optional, on purpose.** `cost_source` and `upstream_unbilled_usd` joined the result contract after the first unattended run, and `base.check_result` accepts a driver that omits them — a connector written before they existed is not a broken connector. It warns to stderr instead, because a caller that cannot tell a billed cost from a reconstructed one has a `cost_usd_total` that means less than it appears to, and `base.with_attempt_defaults` fills both with `None` at the one point a driver's attempt becomes a manifest attempt. Since the fields live in a result rather than in a module, `check_driver` takes an optional `result=` to check them: a module alone can never reveal them.

## The workspace cascade

Framework principle 12's carrier is `scripts/lib/paths.py`. Every file a run loads is searched for in three roots, in order: `<workspace>/.config/panel-review/`, then `~/.config/panel-review/` (the optional **user tier**, honouring `$XDG_CONFIG_HOME`), then the package. The project tier is `.config/` rather than `.agents/` because `.agents/` is the agents framework's working folder for notes, ideas, runs and operational state, and a published skill runs in projects that do not use that framework; `.config/<skill>/` mirrors the user tier, so every project-like root follows one rule.

**What "read-only" covers, exactly.** No **run artifact** is ever written into the package: reports, renderings, manifests, judgment patches, reconciliations and run directories all land in the reviewed project's tree. Two things are outside that claim and are outside the test that enforces it. CPython writes `__pycache__` bytecode beside the scripts and silently skips it when the directory is unwritable, which is why `test_paths.py` excludes `__pycache__` from both the copy it chmods and the fingerprint it compares. And `refresh_models.py` writes a model file back into the outermost root that already holds one for that id, which is `templates/models/` when no outer root does — deliberate, because refreshing a price cache is maintenance rather than a run, and a project that wants the package untouched gives itself copies of the model files it cares about.

**Files replace whole, first hit wins.** Personas, panel templates, **connector files**, references, schemas and backend drivers. There is no per-field merge for a file: an outer copy is the one the run used, and the manifest's `roots` block says so. A connector replaces whole on purpose — an endpoint is a bundle of a driver, a base URL, a key source and a billing posture, and half of one is not an endpoint. **`config.json` and the model files are the exceptions and deep-merge**, package first, user over it, project last — so a fragment carrying `{"default_tier": "fast"}` changes that one key, and a user copy of one model file carrying `{"effort": {"standard": "max"}}` changes that one binding and keeps taking the package's price refreshes for that model. A whole-file replace there would freeze a price on the day somebody wrote the file, which is the failure the merge exists to prevent.

The outer roots mirror the _logical_ category rather than the package's internal nesting: `config.json` at the top, then `connectors/`, `models/`, `panels/`, `backends/`, and `agents/`, `references/`, `schemas/` under their own names. The spec gives one example (`.config/panel-review/backends/azure_openai.py`) and no full layout, so this is a build decision; each category also accepts the package-shaped path under either root, so a workspace that mirrors the package resolves too.

That directory is where the **egress control** lives, which is the reason the cascade exists at all. A project reviewing confidential artifacts drops a driver beside a config fragment binding its families to it; `Paths.driver_ref()` hands the absolute path to `importlib`, so nothing in `scripts/backends/__init__.py` is edited and the shipped package is untouched. A `type` that resolves in neither root is a composition error, exit 1, naming both roots and every path it tried.

`--config` and `--models` stay **operator paths**: given explicitly they are read as given, never merged, and recorded as such. `--workspace` is passed straight through to every `dispatch.py` subprocess, so the child resolves exactly the files the parent did.

The manifest's `roots` block is a map of loaded file to root — `{"search": […], "files": {"config": …, "persona:lens-fidelity": …}, "workspace_overrides": […], "user_overrides": […], "tier_map_overrides": […]}` — chosen over a per-category root because a category can legitimately split across roots (one overridden persona, three packaged ones) and a per-category answer would have to lie about that.

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

## The Judge stage, and the one asymmetry in it

Somebody has to supply that patch. Interactively it is the host session. Unattended it is the `synthesis` persona, and `reconcile.py` dispatches it through **`dispatch.py`'s own machinery** rather than through a second copy of it: `prepare_call()` resolves the model, the registry gate, the effort and the key, and `attempt_loop()` runs the same three retry paths a review seat gets. What differs is the validator — a judgment patch, not a report — and that is exactly the callback `attempt_loop` takes. Two implementations of one call path was the defect the single-writer rule already closed one level up, and it is not reintroduced here.

**Who judges is decided once.** `reconciler` is `host`, `synthesis` or `default`; `default` means host when a human is attached and `synthesis` when nobody is. `run_panel.py` resolves it and **records it in the manifest**, so `reconcile.py` reads a decision rather than asking its own stdin. That matters: a reconcile typed into a pipeline, a CI step or an editor has a stdin that says nothing about whether a human is waiting, and resolving `default` from it would make a paid call on no evidence. `--autonomous` is the explicit override on that side.

**Where the call is seated — it follows the run.** The **tier** resolves by the same order the reviewers' does, first match wins: a template's `synthesis.tier`, then `--tier`, then the panel's `tier`, then the config's `default_tier`, then the `synthesis` persona's own frontmatter. The template's block outranks `--tier` on purpose — an explicit `synthesis.tier` is a choice about the judgment specifically, where `--tier` was aimed at the reviewers. The **family** is the template's `synthesis.family`, else the first non-`claude` family **among the ones the panel actually seated**, in the config's declaration order, else the first non-`claude` family in the config map. `claude` is excluded from both fallbacks, the same exclusion every re-seat draws on and for the same reason; a tier offering only `claude` refuses rather than seating it. `--synthesis-model` pins a concrete id.

Both preferences were earned rather than assumed. Seating the judge at a **fixed** `default_tier` meant a `--tier fast` panel paid $1.70 for a frontier judgment against $1.22 for all four reviewers — the run got cheaper and the judgment did not. Reaching for the first non-`claude` family in the whole **config** meant the judge could land on a family nothing else in the run touched and the operator had never priced; preferring a seated family keeps it on a model the run has already chosen. Which level decided each is recorded: `judge_seat` in the manifest at Resolve, and `judge.tier_source` / `judge.family_source` on the call itself.

**What it is shown, and why all of it.** Every validated report **in full**, the provisional clusters with their `P-n` ids, every reference, and the artifact at its pinned revision. Steps 4 through 7 arbitrate severity, adjudicate false positives and check that quoted text is real; none of that is possible against the reports alone.

**The asymmetry.** An interactive host may dispose a `judgment-call` cluster however it likes and say why, because it has an owner to answer to. The persona may not: an all-`judgment-call` cluster disposed anything but `flag-for-human` in a patch authored by `synthesis` is a patch error, and a `rulings` array from `synthesis` is a **hard error** that earns no re-ask. The persona's own rubric carries both sentences, and `test_catalog.py` asserts it does — a body whose instructions disagree with the validator that judges it is a repair re-ask waiting to happen.

**The patch is validated before it is written.** Schema, then references and enums, then a **full trial merge** through `reconcile_core` — which is the only thing that can catch a label on a cluster the patch's own splits dissolved. Only then does `judgment.json` reach disk, so an invalid patch never does. One repair re-ask names the failing entries; a second failure is exit 3 with nothing written.

**The allowance's prompt rule is accurate; its model was not.** `lib/budget.py` prices the judgment call at 1.5x one seat's composed prompt plus a 16,000-token output prior. Measured with the real code path against the frozen `v2-spec` run — four reports, six references — the judge's actual prompt is **97,641 tokens against roughly 95,000 projected, about 1.03x**. It holds because a seat's prompt already carries the artifact and every reference, and the judge adds the reports on that same base rather than on nothing. The worst case is a run with **no references**, where the shared base is small and the reports dominate: 58,379 against 35,722, **1.63x** — `design-decision`'s shape, and the row to watch rather than the four-seat default.

**The model was the defect, and it is fixed.** The allowance used to be priced on the dearest _seated_ model, which is not what runs the judge. `run_panel.py` now resolves the judge's seat with the same `judge_lib.synthesis_seat()` the judge stage uses, records it as `judge_seat`, honours `--synthesis-model`, prices **that** model, and **fails the registry gate if it cannot price it** — a paid call left out of the projection is the same hole as an unpriced seat. `reconcile.py` reads that record back — **the model included, passed to `prepare_call` as a pin** rather than resolved through the tier map a second time — so a config cell edited between the panel and the judgment cannot redirect the paid call to a model the projection never weighed. The one thing that overrides it is an explicit `--synthesis-model` on the reconcile invocation itself, which is an operator overriding the run's own choice on purpose and is recorded as `family_source: "--synthesis-model"`. A manifest with no `judge_seat` — a run made before the record existed — falls back to resolving the seat from the same inputs Resolve had. With the seat now following the run's tier, the autonomous column in `references/panel-design.md` falls with the tier: the judgment call is $1.70 at `frontier`, $0.34 at `standard`, $0.04 at `fast`.

**The call is accounted like a seat and is deliberately not one.** It lands in the manifest as `judge`, with its model, family, tier, effort level and the parameter it bound to, cap, attempts, usage, reasoning tokens and cost, and its cost goes into `cost_usd_total` — including when it failed or was refused, because a call that spent money and reads as free is the under-accounting this skill has now closed three times. It is **not** in `manifest.seats`: that array is the `unanimous` denominator and every entry in it with no report file is reported as a missing seat, so a judge seated there would make every autonomous run reconcile as under-seated by exactly one.

**One command, unattended.** `run_panel.py --autonomous` runs the Judge and Reconcile stages itself at the end, so the run directory holds all three products when it exits; `--reconcile off` stops after Collect and prints the command. The reconciler's exit code is carried out of the panel, so a run whose reconciliation was never written does not exit 0.

**Zero reporting seats is exit 2 at the judge stage too.** `run_panel.py` already halts there — a reconciliation over zero reports is a lie — and the judge stage halts for the same reason one step later, before spending a call to discover there is nothing to judge. The spec's exit table names the condition for the panel and is silent about the reconciler; this is the consistent reading.

## Auto-apply

`apply_fixes.py` is the only thing here that modifies the document under review, it is off by default, and it reads `reconciliation.json` and `manifest.json` and nothing else — never a report, never a patch, never re-derived prose. Everything the gate turns on was decided upstream by a mind and recorded in the product.

The five conditions, all of which must hold: `disposition` is `fix-now`; `contradicted_by` is empty and `edit_conflict` is false; a `canonical_edit` is present and was explicitly accepted in the patch, with an `old_text` occurring exactly once; the tier is `consensus` or `unanimous` with `n_families >= 2` and never a same-family tier; and the artifact's current content hash equals the manifest's `artifact_revision`.

**Condition 4 is checked in both of its wordings** — the enum half and the evidence half — on every cluster, because the spec states it twice on purpose so the gate cannot silently change meaning if the tier table does. A tier neither wording knows is refused rather than guessed at.

**The authorship assertion is the defence the gate is not.** The artifact is inlined verbatim into every seat's user message, so a hostile document can ask its own reviewers to return a consensus-shaped replacement whose `new_text` is the attacker's paragraph, and if two families comply the five conditions see a well-formed cluster with nothing wrong with any of them. `--i-authored-this` is the operator saying, in the one place it can be checked, that this is their own document; `run_panel.py --auto-apply on` refuses as a composition error without it.

**All-or-nothing, and the distinction that makes it workable.** Every candidate anchor is resolved first, no two edits may overlap, and the file is written once, atomically, with its mode carried across — `os.replace` swaps the directory entry, so without that the document would quietly take the temp file's private mode. A failure at any _anchor_ aborts the whole set. Failing the _gate_ is not failing an anchor: such a cluster is reported as unapplied with its reason and the candidates that passed are still applied. A single-family run cannot satisfy condition 4 at all, so auto-apply there is a no-op that says so.

`applied.md` is written whether or not anything was applied, because a reader has to be able to tell "the gate found nothing to apply" from "the gate found four things and refused all of them". Per applied edit: the cluster, its tier and families, every contributing reviewer, the source seat whose wording was used, who accepted it, and the before/after diff. The one case that writes no log is the single-family no-op, where the gate never weighed anything.

**Three deltas here are this build's rather than the spec's, and all three are user-facing.** `--dry-run` runs **without** the authorship assertion — the spec states that refusal unconditionally — because a reader deciding whether to assert authorship should be able to see what they would be asserting it for, and a dry run has no write path at all: not the artifact, not `applied.md`. `run_panel.py --auto-apply on` adds a **sixth condition**, that the panel exited 0, so a run that exited 3 for one missing seat never writes back unattended; `apply_fixes.py` run by hand still will. And `applied.md` is written whenever the gate ran, where the spec's output layout says "present only when auto-apply ran".

## First-run setup

`install.sh` checks python3 3.10 or newer, checks that the OpenRouter key resolves **through `dispatch.py`'s own chain** rather than through a copy of it — vault first, then the environment, printing which path answered and never the value — and then calls `refresh_models.py` to seed the registry. Idempotent, non-zero with a clear message on any failure, and no third-party dependency anywhere in it.

`--workspace <project>` seeds that project's registry instead of the package's; `--dry-run` shows the diff without writing; `--check-only` skips the catalogue entirely. **All three flags are this build's** — the Shape tree names the script and its three jobs and no interface — and `--check-only` in particular exists because the two checks that can fail on a fresh machine are worth being able to run without a network. `$PANEL_REVIEW_CATALOGUE_URL` overrides the endpoint, which is how `test_install.py` exercises the whole script in a subprocess against a `file://` fixture with **no network call**: the proof is in the registry afterwards, which carries the fixture's prices, and a `PATH` stub proves the version check fires before anything else.

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

**First real unattended run.** On 2026-09-19 the rebuilt skill reviewed its own v3 spec end to end from one command: `spec-review` at `standard` tier, four seats, six references, `--autonomous`. Three seats reported. `buildability-glm` failed after 147 s on a transient `IncompleteRead(528 bytes read)` the retry policy did not cover. The `synthesis` judge ran on `openai/gpt-5.6-sol` at $0.41 for a 127k-token prompt. The run was billed **$2.75 against a $2.30 projection** — confirmed to the cent against OpenRouter's credit ledger — with a further $1.82 of upstream inference run and not billed on one errored attempt. It produced **28 clusters from three seats**, returned **fix-then-ship**, and exited **3** on the missing seat. Everything in _Fixed after the first unattended run_ below came out of it.

## What the build does not have

Absent, not stubbed:

- **Mid-flight budget metering** — the projection is a dispatch gate only. Nothing meters spend as seats return, so a panel that overruns its projection runs to completion.
- **A budget gate on the judgment call itself.** `run_panel.py` charges for it in the pre-flight when the run will make one, but `reconcile.py --reconciler synthesis` invoked on its own makes a paid call with nothing in front of it.
- **Per-seat `timeout_s`** — a specified default of 900 seconds and a specified behaviour, and no implementation. A hung provider hangs that seat until the process is killed.
- **`mode: identical`** — the knob does not exist, so it is not refused by name.
- **`verify_web`** — every live template declares it `false` and nothing reads it, and the composition error the spec requires for `verify_web: true` on a tool-less seat is not implemented, so a workspace panel setting it true runs unrefused.

Built since the first cut of this list: the completion cap and its length retry, the per-model cap floor, the model registry and `refresh_models.py`, the cost and token pre-flight with its budget gate and `budget-refusal.json`, content-hash revisions with a read-only `inputs/` directory, resume with a compare-and-set claim, the three-root cascade, the tier and effort resolution orders, the `non-claude` and `distinct` constraint resolvers, re-seating on a missing cell or an unreachable model, the five remaining lenses, the three remaining panel templates, panel inference with its references rule, `min_families` counted at both ends, **`install.sh`**, **the `synthesis` persona and the whole unattended path**, **`apply_fixes.py` and the five-condition gate**, and **the four reference documents plus the connector contract in `backends/base.py`**.

## Rebuild items

### Fixed after the first unattended run

The 2026-09-19 run described under _Smoke test_ was the first time the whole pipeline ran from one command with nobody watching, and it found five things no scripted test had:

- **`IncompleteRead` is transient now.** `buildability-glm` died on `http.client.IncompleteRead(528 bytes read)` — a provider closing the connection mid-body — after 147 seconds, with zero attempts recorded, no retry and no `.failed.json`. `IncompleteRead` descends from `HTTPException`, not from `OSError`, so it fell straight past a transient set that named the socket branch only. `dispatch.TRANSIENT_EXCEPTIONS` now spells the whole set out, the transport records the **exception class** on the attempt each retry belongs to, and a dispatch that exhausts its retries writes `<reviewer-id>.failed.json` with `failure_stage: "dispatch"` and one attempt entry carrying the cap, the wall time and the error. A seat that was called and failed is never `attempts: null` again.
- **The run's tier and its projection are in the manifest.** The run was dispatched `--tier standard` and printed "projected $2.30 against a budget of $5.00", and neither fact survived into `manifest.json` — `tier_default` recorded only the input to seating, and the pre-flight lived on the console and died with it. Resolve now writes `tier: {resolved, source, unanimous, per_seat}` and Project writes `projection`, the whole block plus the `decision` the budget gate came to, before the first paid call. `reconcile_core.method_caveat` compares projected against actual and names the gap.
- **A zero bill beside $1.82 of upstream inference, and what the ledger says about it.** `consistency-kimi`'s second attempt came back `finish_reason: error` with `cost: 0` and `cost_details.upstream_inference_cost: 1.816461` on 97,967 prompt and 101,504 completion tokens. Checked against OpenRouter's `/credits` endpoint — `total_usage` 7.770718 before the run, 10.522836 after, a delta of $2.752118 against the manifest's recorded $2.752117 — **the errored generation was not billed**, and `usage.cost` is the authoritative figure including when it is 0. So the accounting rule is that the billed cost stands, and the upstream figure is recorded separately as `upstream_unbilled_usd` on the attempt, summed into `_meta` and onto the manifest seat, whenever it exceeds the bill. It never enters `cost_usd` or `cost_usd_total`, which have to reconcile against a credit balance; the method caveat reports it in its own clause and only when non-zero. `_cost` also returns a `cost_source` — `provider` or `estimated` — with `_meta.cost_estimated` flagging any total reconstructed from tokens at catalogue prices, which happens only when the usage block carries no `cost` key at all.
- **The repair quote has a real cap.** The re-ask quoted up to 200,000 characters of invalid output, about 50,000 tokens, on top of a prompt it re-sends whole. The run did not trip it — `consistency-kimi`'s 101,504 completion tokens were 97,562 reasoning tokens, so the unterminated JSON it returned was roughly 15,000 characters and the re-ask cost about 4,000 extra prompt tokens — but the shape of the failure was plain. The cap is now 60,000 characters, head and tail with the middle elided and the elision noted on the attempt.
- **A stale lease can be re-claimed.** The adversarial seat's cluster SF-5 was right: a seat that crashed mid-dispatch stayed `dispatching` forever, and resume could only hold it, so only `--fresh` recovered — re-paying for the whole panel. `claim_seat` now records `claimed_by: {pid, host}` beside `claimed_at`, and a resume demotes a `dispatching` seat with no report to `failed` with `failure_reason: "stale-lease"` when its owner is gone or its claim is past a four-hour ceiling. A live lease is still left strictly alone, which is what keeps two concurrent runs from both paying for one seat.

### Fixed after the first real run

The first panel against a real document (`design/v1-spec.md`, 2026-09-18, four seats, $4.80) exposed four defects in the tooling itself. They were fixed in `scripts/` rather than deferred with the rest of the rebuild, because each one destroyed or hid information the run had already paid for:

- **Length caps truncate; they no longer reject.** The GLM seat was lost over a 696-character summary and two quotes at 336 and 301 — 1, 36 and 96 characters past their caps, on a report whose substance was fine. `lib/report.py` now trims an over-long string to its cap with a trailing ellipsis, records the trim in `_meta.truncated` (field path, cap, original length, finding id) and renders a truncation count in the Markdown run line. Rejection is reserved for what a trim cannot repair: missing required fields, wrong types, bad enum values. The rule is in the shared validator, so it applies to the first attempt, the repair re-ask and `render_harness_report.py` alike.
- **Every attempt is recorded.** `_meta.attempts` carries one row per call — attempt number, `ok` or the list of validation errors, usage, cost, `finish_reason`, driver notes, response id — and the manifest surfaces a per-seat `attempts` count and `first_attempt_errors`. Two seats in that run needed a repair and the manifest could not say why. This replaces `_meta.usage_all_attempts` and `_meta.repair_errors`, which the new block subsumes.
- **A failed seat carries its cost.** GLM's two attempts cost about $0.34 by the dashboard and $0 by the manifest. `dispatch.py` now writes `<reviewer-id>.failed.json` — the same `_meta` block a passing seat gets, plus the validation errors and a pointer to the raw response — beside `<reviewer-id>.invalid.txt`, and `run_panel.py` folds its usage, cost and attempt summary into the seat record and into `cost_usd_total`. A failed seat's spend is now in the run total and on the console line.
- **`render_harness_report.py` no longer assumes the harness leg.** A salvaged OpenRouter report came out labelled `leg: harness, tier: standard`, which is a false audit record. `--leg {harness,openrouter}` (default `harness`) and `--tier` say what the seat was; a `leg` or `_meta.tier` already in the JSON is believed over the flag and a note goes to stderr when the two disagree; `_meta.provider` follows the resolved leg; and an unknown tier on the OpenRouter leg stays unset rather than being invented.

### Fixed in the v1 rebuild

- **`max_tokens` is a knob, and `length` is retried before repair.** Every call carries an explicit cap, `--max-tokens` sets it per run (default 32000), and a per-model `min_max_tokens` floor in the registry raises it for the seat that needs it — Kimi K3 ships at 64000, because run 2's consistency seat spent a whole 32000-token cap on reasoning and returned nothing. A `finish_reason` of `length` is retried **once at double the cap with a fresh prompt**: the truncated bytes are discarded rather than quoted into a 90k-token repair prompt, which is what that run paid $0.66 for. A second `length` falls through to the repair path. Every call's cap, finish reason, usage and cost land in `_meta.attempts`, failed calls included.
- **Effort is abstract, and each model's file holds the translation.** A persona, a panel, a seat and `--effort` all name `light`, `standard` or `deep`; `templates/models/<slug>.json` carries that model's `effort_vocabulary`, refreshed from the catalogue's own `reasoning.supported_efforts`, and an `effort` map binding each level to one of those rungs — or to a reasoning-token budget where the endpoint takes one. `dispatch.py` sends the bound value as `reasoning.effort` or `reasoning.max_tokens`. Two composition errors, exit 1, refused at Resolve by `run_panel.py` and again by `dispatch.py`: a level the model's file does not map, and a mapped word outside that model's vocabulary. A model with no recorded vocabulary at all is sent no reasoning parameter, which is not the same as a ladder nobody has indexed. The manifest records the level, the level that chose it, and the parameter actually sent, per seat.

- **A connector is a file, and it says whether a run may bill an account.** `templates/connectors/<name>.json` holds one endpoint whole — driver `type`, base URL, key source, catalogue URL, endpoint extras such as `provider_routing`, and a `billing` posture of `metered`, `subscription` or `free`. A `metered` endpoint carries `requires_approval: true` and no seat bound to it is dispatched without `--approve-spend`, which answers "you may spend on this endpoint at all" where `--approve-budget` answers "this projection is over the limit you set". An `--autonomous` run exits 4 naming the connector, its seats, the projected spend and the flag; `reconcile.py` applies the same gate to a synthesis judgment call. The answer lands in the manifest as `spend_approval`. A second endpoint is a second file, not a config edit.
- **A projection exists, and it is built on priors rather than on prompt size.** `lib/budget.py` prices each seat as `input_price × prompt tokens + output_price × the model's output-token prior`, plus a 50% repair allowance per seat and one synthesis call. Against run 2's numbers that lands near $1.5 on a run that cost $1.47 and was projected at $1.00 by prompt size alone. Prompt tokens are approximated at four characters per token — no tokenizer dependency — and every figure is labelled an estimate wherever it is printed.
- **The package is read-only and the workspace overrides it.** `lib/paths.py` resolves every packaged file through three roots — `<workspace>/.config/panel-review/`, then `~/.config/panel-review/`, then the package — with whole-file replacement for files and a deep merge for `config.json` and the model files. `run_panel.py`, `dispatch.py`, `reconcile.py` and `refresh_models.py` all go through it, and `run_panel.py` passes `--workspace` to each `dispatch.py` subprocess so the child reads what the parent resolved. A driver under the workspace root is imported from its path, so a project binds a family to its own connector without editing the package. `scripts/tests/test_paths.py` runs a whole fake-backend panel against a copy of the package with every file and directory stripped of its write bit, and checks afterwards that nothing in it moved.
- **Seats resolve by a stated order, and constraints resolve.** `lib/seating.py` implements the tier order and the four family passes, and `non-claude` and `distinct` work. A missing cell, an unsatisfiable constraint and a model the provider refuses each record a `substitution` rather than failing the run, no re-seat of any kind adds a Claude seat nobody asked for, and `reconcile_core.method_caveat` appends every substitution to the reconciliation's method caveat — the judgment supplier cannot know about them, because both re-seat paths are decided by `run_panel.py` after the reports are written.
- **The catalog is complete, and the panel is chosen rather than assumed.** Five lenses were written (`completeness`, `source-credibility`, `security`, `alternatives`, `second-order`), three panel templates landed beside `spec-review`, and the personas were retiered from `model: high` to `model: frontier` so framework §6's frontmatter level of the tier order is live. `--panel` is now optional: the template is inferred from the artifact's name and a run with no references never lands on one that needs them. A `fidelity` or `source-credibility` seat with nothing to cite is a composition error rather than a stderr warning, `min_families` is counted at both ends and named in the method caveat, and `judgment-call` was sharpened to mean a design fork rather than a thin spot. The four sections above carry the reasoning; `test_catalog.py`, `test_panel_selection.py` and `test_min_families.py` carry the tests.
- **The artifact has a revision, and three mechanisms use it.** `run_panel.py` copies the artifact and every reference into a read-only `<run-dir>/inputs/`, and a revision is the SHA-256 of the bytes written there, with the commit id beside it when that file's tree is clean. Seats read those bytes; resume refuses when the input fingerprint has moved; `reconcile.py` hashes the artifact it resolves against the manifest's `artifact_revision` and refuses on a mismatch. **In the ordinary case that refusal never fires**, because `reconcile.py` resolves the run's own `inputs/` copy first and those bytes cannot change — editing the working-tree document afterwards is fine and always was. It fires only when `--artifact <path>` overrides the resolution, or when the `inputs/` copy is gone and the working-tree file has moved on since. A null revision is unpinned, warned about, and allowed through, which is what keeps the frozen replay fixture running.

- **The pipeline closes: judge, reconcile, apply.** The `synthesis` persona was written and `reconcile.py` gained the Judge stage, dispatching it through `dispatch.py`'s own `prepare_call()` and `attempt_loop()` rather than a second copy of the retry paths — the same driver, the same doubled-cap length retry, the same registry gate, the same per-call cost record. Who judges is resolved once in `lib/judge.py` and recorded in the manifest, so `reconcile.py` never re-derives it from its own stdin. `apply_fixes.py` landed with the five-condition gate, all-or-nothing application and `applied.md`; `install.sh` landed with the python check, the key check through the real chain and the registry seed; and the four reference documents the Shape tree names were written, plus `backends/base.py` stating the connector contract with two structural checks a test runs against every shipped driver.

### Still open

- **Reasoning tokens bill as output, and several frontier models will not let you opt out.** `_meta.reasoning_tokens` records them per seat from `usage.completion_tokens_details.reasoning_tokens` when the provider reports it. They are often the larger half of a seat's bill and are invisible in the completion, which is why the projection is built on a measured output-token prior rather than on prompt size.
- **The priors are four measurements and eight defaults.** `openai/gpt-5.6-sol`, `z-ai/glm-5.3-flash`, `moonshotai/kimi-k3` and `x-ai/grok-4.6` carry run 2's accepted-attempt completion tokens and `openai/gpt-6-astra` carries run 1's; every other model in the registry carries a flat 16000 labelled `default`. A projection for a panel of unmeasured models is a guess with a receipt.
- **The repair re-ask resends the whole user message.** It has to, because the reviewer must re-quote verbatim — but it roughly doubles the cost of a seat that needed one. A cheaper repair that sends only the previous output and the errors would save money and risk fabricated quotes; measure before choosing.
- **Panel inference reads a filename, not a document.** The heuristic is three word lists over the artifact's own name, which is cheap, reproducible and wrong whenever a document is named for its subject rather than its kind — a research report called `caching.md` infers `spec-review`. The references rule is the safety net that matters, and it is the one that fires; the heuristic is a convenience and the operator can always pass `--panel`. Whether the heuristic should look at the document's headings is undecided, and doing so would put an unreviewed reading of the artifact ahead of every reviewer's.
- **`min_families` is counted and named, never acted on — deliberately.** The spec makes it a target rather than a precondition, so nothing re-seats to reach it and nothing refuses for missing it. What is genuinely absent is a way to _ask_ for more families: composing a panel across more families is still a hand edit of the template, and `--min-families 3` on a three-family panel records the shortfall rather than seating a fourth.
- **`provider_routing` is passed through on thin evidence.** The shipped connector file carries the spec's one entry, for `z-ai/glm-5.3`, and the driver sends it as OpenRouter's request-level `provider` object. The per-model endpoint data that would justify a routing choice — latency, throughput, quantization and price per host, from `GET /api/v1/models/{author}/{slug}/endpoints` — is a second pass of `refresh_models.py` that nothing runs yet, so the values are hand-picked.
- **A runtime re-seat spends past the projection.** The budget gate is a pre-flight over the seats as composed; a seat re-seated onto another family makes a call the projection never priced, on a model that may cost more than the one it replaced. Nothing re-gates. That is the same gap as mid-flight metering, one level down, and it is part of why the re-seat is capped at once per seat.
- **`dispatch.py` exit 5 is not in the spec's exit-code table.** It is internal signalling — the child telling the parent that the provider refused the model rather than that the reviewer wrote a bad report — and `run_panel.py` consumes it and never re-emits it, so no panel run returns 5. An operator invoking `dispatch.py` directly on an unserved model will see it.
- **A refused model's first call is recorded as a failure, not as spend.** `dispatch.py` returns before writing `<reviewer-id>.failed.json` when the provider never answered with a body, so a re-seated seat's manifest record carries the re-seated dispatch's cost and not the refused one's. A 404 costs nothing, so this is right today and would be wrong the moment a provider starts billing for one.
- **The manifest's compare-and-set is guarded by a lock directory, not by a transaction.** `manifest.lock` is an exclusive `mkdir` held across one read-modify-write, and a lock older than five minutes is broken so a crashed run cannot wedge a resume. That is enough for the two cases this skill has — several seat threads in one run, and two resumes on one directory — and it is not a distributed lock.
- **Per-seat `timeout_s` is unimplemented.** The spec gives it a 900-second default and a defined behaviour — expiry flags the seat missing at stage `timeout` and the panel continues — and `run_panel.py` passes no timeout to the `dispatch.py` subprocess at all. A hung provider hangs that seat, and with it the `as_completed` loop, until the process is killed. `MISSING_STAGE` already maps a `timeout` status, so the reconciliation half is ready for the run half that is missing.
- **Nothing re-gates the judgment call against the budget.** The pre-flight charges for it when `run_panel.py` knows the run will make one, and that is the only gate it ever passes. `reconcile.py --reconciler synthesis` run directly — resuming into the judge stage, say — makes a paid call that no budget has been consulted about. Same shape as the mid-flight metering gap, one stage later.
- **Auto-apply invalidates the reconciliation that authorised it.** Writing to the artifact changes its content hash, so condition 5 fails on any second `apply_fixes.py` against the same run. That is correct — re-applying a review of bytes that no longer exist is what the condition is for — but it means there is no partial-retry story: a set that aborts at one anchor has to be fixed and re-run in full, or applied by hand.
- **The exclusive-mkdir claim does not stop two runs sharing a directory; the seat compare-and-set does.** The spec says two concurrent runs on one artifact can never write into the same directory, and the claim alone delivers that only for a directory that does not yet exist. Because an existing run directory **resumes** by design, two runs both naming the same `--out` do share it. What actually protects the money is one level down: a seat moves `pending`/`failed` → `dispatching` by compare-and-set before its first paid call, so only one of the two runs pays for any given seat, and the other reports it held.

## Conventions

The skills repo is public. No key value appears in any file here: `dispatch.py` resolves the OpenRouter key through `skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY`, falling back to `$OPENROUTER_API_KEY`, and exits naming both paths it tried if neither answers. Scripts are standard-library Python 3 with a `main()` and `argparse`. Reports never land in the session scratchpad — harness subagents cannot write there — so every run directory lives inside the reviewed project's tree.

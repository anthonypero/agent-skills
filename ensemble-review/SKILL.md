---
name: ensemble-review
description: "Review a high-stakes document with a panel of independent reviewers instead of one. Use when a spec, plan, PRD, research report, design decision or technical-requirements document is about to gate a build or a commitment and a single reviewer's blind spot would be expensive; when the user asks for a panel, an ensemble, a multi-model review, a red-team read, or a second and third opinion on a document; or when a document has been revised enough that nobody trusts a single pass over it any more. Each seat carries a different lens — fidelity, buildability, consistency, adversarial, completeness, source credibility, security, alternatives, second-order consequences — and a different model family; reviewers never see each other's work, every report lands on disk as validated JSON, and one script reconciles them into a single document — with the host session supplying the judgment interactively, or a synthesis persona supplying it unattended so the whole pipeline is one command. Not for code diffs — those go to /code-review."
---

# ensemble-review

One reviewer has one shape of blind spot. A panel of reviewers who carry different lenses **and** run on different model families has uncorrelated blind spots, so their agreement is evidence and their disagreement is a pointer at the thing nobody has decided yet.

The prototype that motivates this skill is on disk at `.agents/subprojects/annotate/pm/reviews/`. One round dispatched three reviewers with identical prompts on one model and paid three times the tokens for about one and a half times the coverage. The next round swapped redundancy for lens diversity — fidelity, buildability, consistency, adversarial — and beat it decisively: one blocker and two near-blockers the identical panel missed, and every unique high-severity catch came from exactly one lens. This skill makes that panel repeatable and adds the axis the prototype could not: reviewers from different model families.

The value is governed by **independence**, not by count. Everything here is machinery around that one sentence.

> **This is v1.** It runs a panel end to end, judges it either interactively or unattended, reconciles it with `reconcile.py` — the only writer of both reconciliation files — and can apply the corroborated literal edits back through a five-condition gate. See _Still open_ at the bottom before you promise anyone anything.

## First-run setup

```bash
skills/ensemble-review/install.sh
```

Two checks, one install and one seed, and it is idempotent — re-run it whenever you like.

1. **python3, 3.10 or newer.** Every script here is standard-library Python 3 with nothing to install, so that is the whole runtime requirement.
2. **The OpenRouter key resolves**, through the same chain `dispatch.py` uses and no other: `skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY` first, then `$OPENROUTER_API_KEY` in the environment. It prints **which** path answered and never the value — this repo is public, and a key echoed into a terminal is a key in a scrollback. A connector file that sets `api_key_secret: null` skips the vault leg. Which endpoint is asked about comes from `config.json`'s `default_connector` and the connector file it names.
3. **The harness judge is installed**: `agents/judge.md` is copied to `~/.claude/agents/ensemble-judge.md`. That is what makes it spawnable by name, and its presence at that exact path is also the signal that makes `harness-judge` the default judgment supplier on an unattended run. Installing it twice moves nothing.
4. **The model registry is seeded**, by calling `refresh_models.py`.

`--workspace <project>` seeds that project's own `models/` instead of the package's, `--dry-run` shows what the refresh would change without writing, and `--check-only` does the two checks, **reports whether the judge is installed without installing it**, **reports whether a user tier exists at `~/.config/ensemble-review/` without creating one**, and makes no catalogue call at all. Any failure exits non-zero with the reason and leaves every model file untouched.

The **model registry**, `templates/models/<slug>.json`, is what it seeds: one file per concrete model id, holding what a token costs, how much context it has, which effort strings it accepts, and how many completion tokens one review seat spends on it — plus the choices a person owns about that model: which connector serves it, which family it is, which tiers it plays, and which of its own rungs each abstract effort level means. A file's name is its id with every `/` written `__`, so `moonshotai/kimi-k3` is `moonshotai__kimi-k3.json`, and the file carries its own `id` as well. The cost pre-flight is unimplementable without it. To refresh it by hand later:

```bash
python3 skills/ensemble-review/scripts/refresh_models.py            # prices + context limits from the OpenRouter catalogue
python3 skills/ensemble-review/scripts/refresh_models.py --dry-run   # show the diff, write nothing
python3 skills/ensemble-review/scripts/refresh_models.py --add z-ai/glm-5.4
```

It is idempotent — a refresh that changes nothing writes nothing and says so — and it **writes facts and never choices**. The catalogue owns prices, context limits and effort vocabularies; the connector, the family, the tiers, the effort map, `output_token_prior`, `min_max_tokens` and the measured prices are decisions a person made, and a refresh leaves every one of them alone. `--add` seeds a new model file with the catalogue's facts and null choices. Offline it exits 1 and leaves every file untouched.

**It resolves the registry through the same cascade, and it is the one script that writes to the package.** A model file is refreshed **in the outermost root that already holds one for that id** — the project's `.config/ensemble-review/models/`, else `~/.config/ensemble-review/models/`, else `templates/models/` inside the package. The read-only invariant is scoped to a _run_ — no report, manifest or reconciliation is ever written there — and refreshing a price cache is maintenance, not a run. If a project needs the package left strictly untouched, give it its own copy of the model files it cares about: `cp templates/models/*.json <project>/.config/ensemble-review/models/`, then pass `--workspace <project>`.

**A resolved seat the registry cannot price is a composition error**, refused before dispatch with exit 1 naming the model and the `--add` line that fixes it. That covers both failures, because presence is not coverage: a model absent from the registry, and a model present with a null input or output price. A seat left out of the projection is a budget gate that does not gate — the run would clear a $5 budget on a projection that priced three of its four seats.

## Dispatch model

Reviewers are **personas**, not harness subagents: a lens prompt, a rubric and an output schema in `agents/`, with no model baked in. A **seat** says which family and which abstract effort level runs a persona. Concrete model ids live in `templates/models/`, one file each, and are resolved at dispatch time, so a panel definition never names a vendor and never names a vendor's effort word either.

**The lens catalog.** Nine lens personas ship, plus one non-lens persona, `synthesis`, which supplies the judgment for an unattended run and is never seated on a panel. The first four lenses are the ones with a measured track record — the 2026-06-26 `annotate` full-spec round — and they are what `spec-review` seats.

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

**`synthesis` is a persona and is not a lens.** It reads every validated report, the provisional clusters, the references and the artifact, and returns the **judgment patch** — not a review, and never the reconciliation itself. It carries `context: [reconciliation.md, finding-schema.md]` where a lens carries the finding schema alone, it is validated against `judgment-patch.schema.json`, and `test_catalog.py` asserts that `agents/` holds exactly the nine lenses plus exactly this one non-lens persona and that no shipped template seats it as a lens.

**`agents/judge.md` is not a persona at all.** It is a **harness agent definition** — `ensemble-judge`, pinned to frontier Claude at `high` effort with read-only tools — which `install.sh` copies into the harness agents directory and the orchestrating session spawns by name. It supplies the same judgment `synthesis` supplies, under the same rubric: its body **references** `agents/synthesis.md` rather than restating it, because two copies of one rubric drift and nothing would compare them. Nothing composes a prompt from it, so the persona shape rules do not apply and `test_catalog.py` holds it to its own class. Owner ruling, 2026-09-19: the judge belongs on a family no seat holds, and this is the one written-in exception to the global no-Fable-subagents rule.

Three sentences are **byte-identical in every lens body**, and `scripts/tests/test_catalog.py` asserts it: the severity calibration (rate by consequence, not by how much is unspecified), the `judgment-call` rule (a design fork, not a thin spot), and the verbatim-quote rule. A lens calibrated differently from its neighbours would make the panel's agreement counts mean something different per seat. `synthesis` carries the first of the three, because it arbitrates their severities, and two of its own that the script enforces against it: an all-`judgment-call` cluster is always `flag-for-human`, and it may not emit `rulings`.

**`judgment-call` means a design fork and carries the `fork` tag.** `change_kind` asks whether the fix can be written out or somebody has to decide first, and it is not a measure of size: a determinate fix is a `literal-edit` however large the replacement is, a whole missing section included, and it takes the `gap` tag. The enum could not gain a third value — the frozen replay fixture depends on the two it has — so the tag carries the distinction, and `lib/report.py` enforces it **at ingest**: a reviewer's fresh report is rejected if a `judgment-call` carries no `fork`, or carries `gap`. Reading a stored report back applies neither rule, so a report written before the rule still replays. `reconcile_core` then reads the tag: an all-`judgment-call` cluster goes to a human unless every one of its findings is tagged `gap`, and an untagged cluster goes to the human exactly as it always did.

Prompt composition is fixed, and identical on both legs — that is what makes the comparison meaningful:

- **System message** = the persona body (everything after the frontmatter) + `references/finding-schema.md`.
- **User message** = the artifact and every reference **inlined verbatim**, delimited, each labelled with its repo-relative path.
- Personas have `tools: []`. They never read files. Two reviewers whose inputs differ by what they chose to open are not comparable.

**Tiers, chosen by stakes.** The tier × family map is **derived** from the model files: each one names its `family` and the `tiers` it plays, and `config.json`'s `family_order` and `tier_order` fix the declaration order the seating passes walk. Nothing writes the map itself, so the one place a model's tier is stated is the file about that model.

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

**Effort, chosen the same way.** Personas, panels and seats name an **abstract level** — `light`, `standard` or `deep` — and never a vendor rung. Three levels rather than five because several models on the catalogue offer only three, and a five-level abstraction would bind two levels to one rung for them, which the manifest would then record as two different intentions for one parameter. Each model file's `effort` map binds each level to that model's own word (or, where the endpoint takes one, a reasoning-token budget), so `deep` is `max` on Kimi K3 and `xhigh` on Sonnet 5 without a panel having to know either. The order is tier's, one flag down:

| Order | Level | Looks like |
| --- | --- | --- |
| 1 | `--effort` on the command line | `--effort light` for a cheap pass over a whole panel |
| 2 | the seat's own `effort` | One lens thinking harder than its panel |
| 3 | the panel's `effort` | A template that is deep by nature |
| 4 | the config's `default_effort` | `standard` |
| 5 | the persona frontmatter's `effort` | Every shipped persona carries `effort: standard` |

A level a model's file does not map is a composition error naming the model and the levels it does map; a mapped word outside that model's recorded vocabulary is the same error. A value that is not one of the three levels at all — `standrd`, or a vendor's own `high` — is refused against **the source it was typed in**: the persona, the template seat, the panel or `config.json`, since only `--effort` is checked by the parser. A model with **no** recorded vocabulary has no rung to name and is dispatched with no reasoning parameter at all. The manifest records the level, the level that chose it and the parameter actually sent, per seat.

**The judgment call resolves effort on its own order**, which is this one with the two knobs that are not about the judge replaced by the one that is: the panel template's `synthesis.effort` → the level the run's seats actually ran at, when they agreed on one → the config's `default_effort` → the `synthesis` persona's frontmatter → `standard`. `reconcile.py` has no `--effort`, so a template that already pins the judgment's family and tier is where its depth is pinned too. The harness judge is outside all of it: its model and its effort are lines in its own installed agent file.

**Family constraints.** A seat's `family` is a named family from the derived tier map — `openai`, `glm`, `kimi`, `xai`, `claude`, `google`, `deepseek` — or one of two constraints. Seats resolve in four passes, so the result does not depend on how the template happens to be written:

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

**Panel templates.** Four ship in `templates/panels/`. A template is a starting point, not a wall: compose seats ad hoc, or drop an edited copy under `<workspace>/.config/ensemble-review/panels/` and it replaces the shipped one whole.

**The loader is strict: an unrecognized key is a composition error, exit 1.** Owner ruling, 2026-09-19. It names the key, where it is (the template, `seats[2]`, `optional_seats[0]`, the `synthesis` block) and the template's path, and it fires **before the run directory is claimed and before a seat is priced**. The reasoning is about who writes these files: the author is the orchestrating agent, not a human reading a diff, so a silent `min_famalies` is the agent's typo running the panel at the default and spending the owner's money — and the refusal is what pushes it straight back to the agent. There is no leniency prefix; operator metadata goes in `description`. The vocabulary is in `scripts/lib/panels.py`, and `test_template_schema.py` asserts that every shipped template passes it.

| Scope | Recognized keys |
| --- | --- |
| the template | `name`, `description`, `requires_references`, `min_families`, `tier`, `effort`, `reconciler`, `auto_apply`, `verify_web`, `seats`, `optional_seats`, `synthesis`, `deferred`, `routes_to` |
| a seat, in `seats` or `optional_seats` | `lens`, `family`, `tier`, `effort`, `suffix`, `note` |
| the `synthesis` block | `family`, `tier`, `effort` |

| Template | Seats | `requires_references` | Notes |
| --- | --- | --- | --- |
| `spec-review` | fidelity, buildability, consistency, adversarial — openai, glm, kimi, xai | `true` | The measured panel: the 2026-06-26 lens set, one family per seat, no Claude seat. A fifth `completeness` seat sits in `optional_seats` on `deepseek` — the one non-Claude family the four seats do not already hold — off by default |
| `research-report` | fidelity, source-credibility, completeness, adversarial — openai, kimi, glm, xai | `true` | Four named distinct families. `verify_web` is off in v1, so the two citing lenses judge the sources you supply rather than the live web |
| `design-decision` | adversarial, alternatives, second-order — xai, openai, glm | `false` | The artifact is the argument. This is also where a run with no references lands |
| `draft-review` | fidelity, buildability, consistency, adversarial — all four on `claude` | `false` | The **draft pass**, and not a gate. Run it with `--draft`: every seat is a harness subagent on the subscription, `min_families` is 1, and the reconciliation says the findings carry no corroboration claim. The one shipped template that seats `claude`, and the one that seats a citing lens without requiring references — a reference-free draft run retires that seat instead of refusing |
| `code-review` | none | — | Ships **deferred**: a named stub carrying `"routes_to": "/code-review"`. `run_panel.py` refuses it with exit 1 and says where to go |

**Choosing the panel, when you do not name one.** With no `--panel`, `run_panel.py` infers: a cheap heuristic over the **artifact's own filename** picks `research-report` or `design-decision` when the name says so, and `spec-review` otherwise. Nothing reads the document — a heuristic that opened the artifact would be a second, unreviewed judgement about it before any reviewer has seen it. Then the references rule: **a run with no references never infers a template whose `requires_references` is true.** It falls back to `design-decision`, and the substitution is recorded in the manifest's `panel_inference`, printed on the console, and named in the reconciliation's method caveat. Every run carries that block, named panel or not: `requested` is the `--panel` you gave or null, `heuristic` is what the filename suggested or null when nothing read it, `resolved` is what ran, and `reason` is filled only when the last two differ.

**References are a composition error for two lenses, not a warning.** `fidelity` and `source-credibility` require a `citation` on every finding — `lib/report.py` rejects one from either lens without it — so a seat carrying either with nothing to cite would have every finding it returned thrown out. `run_panel.py` refuses before the first paid call, exit 1, naming the seats. Inference bends to avoid this; an explicitly named `--panel` does not, because an operator who named it should hear why.

**The two panel-shape refusals happen before the run directory is claimed.** The deferred `code-review` stub refuses on the template, and a starved `fidelity` or `source-credibility` seat refuses once the seats resolve and still before the claim. Neither leaves a directory behind, which matters because a claim collision increments the sequence number: a stray `2026-09-18-1` would silently move your retry — the one with `--ref` supplied — into `2026-09-18-2`. Every other refusal comes after the claim and leaves the directory with what it wrote by then — the registry and effort gates, a resume fingerprint mismatch, a materialize failure, and the budget refusal, whose `budget-refusal.json` is written there on purpose — so delete the directory before retrying those.

**`verify_web` is declared and off.** Every live template carries `"verify_web": false`. No seat has a live web tool in v1, so the two citing lenses judge the sources you supply; the flag states that rather than switching anything.

**`min_families` is a target, not a precondition.** The panel's value (default 2) is overridable with `--min-families`. Distinct families are counted **twice** — over the expected seats at Resolve and over the reporting seats at wrap-up — and both land in the manifest as `min_families: {target, seated, reporting}` beside the family lists. A run that misses the target **proceeds**: it is never an error, and the console and `reconcile_core.method_caveat` both say so. One reporting family reads as **"lens-diverse only"**: several lenses, one mind, and no cluster in that run can carry cross-family corroboration at any tier.

**Where files come from: the three-root cascade.** The skill package is read-only: **no run artifact is ever written into it** — reports, renderings, manifests, judgment patches, reconciliations and run directories all land in the reviewed project's tree. The one thing that does appear under the package is CPython's own `__pycache__` bytecode, which the interpreter writes and silently skips when it cannot; that is outside the invariant and outside the test that enforces it. `refresh_models.py` is the other exception, and a deliberate one — see _First-run setup_. Every file resolves through `scripts/lib/paths.py`, which searches three roots in order:

1. `<workspace>/.config/ensemble-review/` — the project holding the artifact, from `--workspace`, else the working directory. It is `.config/` rather than `.agents/` because `.agents/` is the agents framework's working folder for notes, ideas, runs and operational state, and this skill is published into projects that do not use that framework; `.config/<skill>/` mirrors the user tier's `~/.config/<skill>/`, so every project-like root follows one rule.
2. `~/.config/ensemble-review/` — **the user tier**, this machine's owner (`$XDG_CONFIG_HOME` is honoured). Optional, and where a per-owner choice lives: a connector this machine trusts, one model's effort rung, a key source. A machine with no such directory resolves exactly as it did with two roots.
3. the skill package.

**Files replace whole, first hit wins**: personas (`agents/<name>.md`), panels (`panels/<name>.json`), **connectors (`connectors/<name>.json`)**, references (`references/<name>`), schemas (`schemas/<name>.json`) and backend drivers (`backends/<type>.py`). There is no per-field merge for a file: if an outer root has one, that is the one the run used. A connector is a whole file on purpose — an endpoint is a bundle of a driver, a base URL, a key source and a billing posture, and half of one is not an endpoint.

**`config.json` and the model files deep-merge**, package first, user over it, project last. A project that wants one thing changed writes exactly that and nothing else:

```json
{ "default_tier": "standard" }
```

```json
{ "effort": { "standard": "max" } }
```

The second is `~/.config/ensemble-review/models/moonshotai__kimi-k3.json`: one choice about one model, on one machine, that keeps taking the package's price refreshes for it. A whole-file replace would freeze the price from the day it was written, which is why model files merge — and why a refresh writes back only the keys your file already has plus the facts that actually moved, so your two-key file stays a two-key file.

**Putting your own model in a tier cell is the same one edit.** Drop a model file that names a `family` and the `tiers` it plays into either outer root and it takes that cell from the packaged model: the outermost root that declares `tiers` wins, and the displaced model is recorded in the manifest's `roots.tier_map_overrides`. A file that sets only an `effort` rung, like the one above, claims no cell. To empty a cell instead of taking it, write `"tiers": []` in your own layer, since a list replaces. Two files at the **same** root claiming one cell is a composition error naming both.

This is where per-project bindings live, and in particular where the **egress control** lives: a project reviewing confidential artifacts drops a driver at `.config/ensemble-review/backends/azure_openai.py` and a connector file at `.config/ensemble-review/connectors/azure.json` naming `"type": "azure_openai"`, then points `config.json`'s `default_connector` at it. A second endpoint is a second file, not a config edit. The driver is imported from that path — no edit to `scripts/backends/__init__.py` — and the shipped package is untouched. A `type` that resolves in no root is a composition error, exit 1, naming every root and every path it tried.

**Every connector declares a billing posture**, and `run_panel.py` enforces it before the first call:

| `billing` | Means | Gate |
| --- | --- | --- |
| `metered` | Every call costs money on an account | `requires_approval: true` by default. The run refuses without `--approve-spend`; `--autonomous` exits 4 naming the connector, the seats bound to it, the projected spend and the flag |
| `subscription` | A plan already paid for | None: the marginal call is free |
| `free` | No charge at all | None |

**Two connectors ship.** `openrouter.json` is `metered` with the gate on, and `harness.json` is `subscription` with no key, no base URL and no catalogue — it is the orchestrating session's own agent harness seated as an endpoint, and `--draft` is what selects it. Its driver, `backends/harness.py`, exists so the `type` resolves and is written as a refusal rather than an implementation: a seat on that connector is marked a harness seat at Resolve and spawned by the session, so reaching the driver at all means something upstream broke, and it says so instead of calling anywhere.

**`--approve-spend` is a different question from `--approve-budget`**: one answers "you may spend on this endpoint at all", the other "this projection is over the limit you set", and a run can be well under budget and still be the first time anybody said yes to paying. `--smoke-test` is gated too, because it also spends. `reconcile.py` applies the same gate to a `synthesis` judgment call and takes the same flag — and also honours a **granted `spend_approval` in the manifest of the run directory it was handed**, so a one-command autonomous run does not bill a whole panel and then refuse its own judgment, and the printed host-run line works without a pre-typed flag. That record covers one run directory and says nothing about the endpoint in general; which of the two satisfied the gate is recorded on `manifest.judge` as `spend_approval_source`. The answer lands in the manifest as `spend_approval: {required, granted, source}`, so the audit trail shows a person said yes rather than a default. An outer-root connector file may set `requires_approval: false` for a metered endpoint it has decided to trust, and the manifest then records both that the gate was off and which root turned it off.

The manifest's `roots` block records the search order, the root each loaded file actually came from, and **two** override lists — the project's and this machine's, kept apart because a project override travels with the repository and a per-owner one does not. A workspace that mirrors the package layout (`templates/config.json`, `templates/panels/…`, `scripts/backends/…`) resolves too; the flatter workspace-shaped path is tried first.

`--config` and `--models` remain **operator paths**: given explicitly, they are read as given and never merged.

**The default panel seats no Claude family.** The host session that authors and reconciles is a Claude model, so a Claude reviewer is the seat most correlated with the artifact; the `claude` family maps to Sonnet 5 at every tier and is seated only on request. That rule is about the panels a document is **promoted** on. The draft pass below is the written-in exception: Opus is allowed on subscription early-draft reviewer seats, the once-per-stage gate still seats no Claude family, and Fable never runs a reviewer seat at all (owner ruling, 2026-09-19).

## The draft pass — $0

While a document is still being drafted, run the panel on the harness instead of on the API:

```bash
python3 skills/ensemble-review/scripts/run_panel.py \
  --draft --panel draft-review \
  --artifact <artifact> --ref <reference> \
  --out <run-dir>
```

`--draft` composes the run against the **`harness` connector** rather than the config's `default_connector`, so the tier map it seats from is built out of the model files that name that endpoint — `claude-opus-5` today. Every seat therefore lands on the harness leg, exactly as a `claude` seat does under `--skip-claude`: nothing is dispatched, no driver runs, no call is made. It costs nothing beyond the plan the owner already pays for, so run it as often as the draft changes.

**What it claims, and what it does not.** A draft pass is a real review by four real lenses. What it cannot claim is **corroboration**: one mind behind four lens prompts is not four minds, so the family target is 1, no cluster can reach a cross-family tier, and the reconciliation opens by saying so — in different words from `--smoke-test`, deliberately. A smoke test says the run is **not evidence about the artifact at all**; a draft pass says the findings are real and **uncorroborated**. Read a draft's findings one by one; never cite its agreement counts, its tiers or its verdict, and never promote a document on it.

**The mode is defined by its price, so it is enforced rather than hoped for:**

- `--draft` and `--smoke-test` are different runs and cannot be one run; asking for both is a composition error naming what each does.
- **A `draft_only` template refuses to run without `--draft`**, with exit 1 at template load, before anything is claimed or priced. The flag is what selects the harness endpoint, so without it a draft template's seats would resolve against the _metered_ map and be billed like any other panel's — the one way the draft panel could cost money. `--skip-claude` does not satisfy it: it leaves `claude` seats to the session on an otherwise metered run and selects no endpoint at all.
- A panel a **metered** endpoint would serve is refused with exit 1 before the run directory is claimed, naming every offending seat, the family it asked for, and the model and connector that would have billed it. `--draft --panel spec-review` is that refusal: `openai`, `glm`, `kimi` and `xai` are served by OpenRouter and nothing else. A `--model` pin onto a metered model is refused the same way. For a cheap **metered** pass, drop `--draft` and use `--tier fast`, which is priced, budgeted and spend-gated like any other run.
- The connector's **spend gate never fires**, and the manifest says why rather than leaving a reader to infer it: `spend_approval: {required: false, reason: "no metered connector seated"}`.
- The judgment cannot fall to the `synthesis` persona, which is a paid call and the dearest single call most runs make. On a draft run that resolves there, `run_panel.py` refuses and names the two ways out: `./install.sh` to install the harness judge, which judges on the subscription, or `--reconciler host`.
- Every seat is still **priced**, at zero. A harness model file whose price is _missing_ rather than zero is a model nobody has classified, and the registry gate refuses it: a run that says it costs nothing has to have looked.

**How a draft run finishes.** `run_panel.py` does everything up to the spawn and then stops, exactly as the harness judge stage does — a script cannot spawn a harness agent. It prints one exact block per seat: the seat, an explicit model override of `opus` (never `subagent_type: "fork"`, which inherits the parent's model), the persona body and `references/finding-schema.md` by absolute path, the pinned `inputs/` copies of the artifact and every reference, the report envelope fields no script fills in (`artifact` above all), the seat's own staging path, and the digest cap. Spawn all of them in **one message** so they run concurrently and blind. Then render and move each report in as in step 5 below, and **re-run the same `run_panel.py` command** — the printed block ends with it — to reach the judge stage. Resuming re-reads the reports from disk, so a seat whose report did not validate is the only one you are asked to spawn again. **The pass that prints the block exits `0`** — the same code the harness judge stage's stop uses, because both mean the same thing: stopped for you to act, not under-seated. **A resume is where the harness leg counts**: you have been asked for those reports once, so a pass that still finds one absent or invalid exits `3`. On a mixed `--skip-claude` run a pending harness seat is the normal mid-flight state at any point and does not move the code.

**The shipped template is `draft-review`**: the same four lenses as `spec-review`, all four on `claude`, `draft_only: true`, `min_families: 1`, `reconciler: default`, and `requires_references: false` so it also serves a seed document with no source of truth. It seats `fidelity`, which cites on every finding — so on a draft run with **no** references that seat is **retired** rather than the run refused, recorded in the manifest as `failed` with `failure_reason: "no-references"`, and named in the reconciliation as a seat that did not report. Supply `--ref` and it is seated like any other. On a metered run the same seat still refuses the whole run, because there the wasted output costs money.

## The autonomous path — one command

When nobody is attached, the whole pipeline is one invocation:

```bash
python3 skills/ensemble-review/scripts/run_panel.py \
  --artifact <artifact> --ref <reference> \
  --out <run-dir> --autonomous
```

`--autonomous` is also **inferred when stdin is not a tty**, so a run from a script or a scheduler behaves the same way without being told. What changes, compared with the interactive path:

- **Over budget, it refuses** rather than asking: `budget-refusal.json` and exit 4, before any paid call.
- **The judgment comes from an unattended judge.** `reconciler: default` means host when a human is attached and, when nobody is, the **harness judge** where `ensemble-judge` is installed and the **`synthesis` persona** where it is not. The run **records which** in its manifest — the answer, not the premise — so `reconcile.py` reads a decision rather than asking its own stdin a question the panel already answered. Where it has to resolve a `default` of its own, it asks the same machine the same question.
- **`run_panel.py` runs the Judge and Reconcile stages itself** at the end **when the judge is `synthesis`**, so the run directory holds `judgment.json`, `reconciliation.json` and `reconciliation.md` when it exits. `--reconcile off` stops after Collect and prints the reconcile command instead.
- **With the harness judge it runs the worksheet and then hands you the spawn.** A script cannot spawn a harness agent, but the agent answers the provisional clusters and only `reconcile.py`'s first pass computes them — so the run invokes that pass, which writes `<run-dir>/judgment-request.json` and prints the spawn instruction over it: the agent name, the run directory, the package path, the worksheet, the staging path, and the `reconcile.py --judgment <staging>` line that ingests what comes back. See below.
- **Auto-apply is still off.** `--auto-apply on` arms it after the reconciliation is written, and it is refused as a composition error without `--i-authored-this`.

**What the judgment call is.** `reconcile.py` composes the persona's user message from every validated report in full, the provisional clusters with their `P-n` ids, the references and the artifact at its pinned revision — steps 4 through 7 arbitrate severity, adjudicate false positives and check that quoted text is real, and none of that is possible against the reports alone. It dispatches through `dispatch.py`'s own machinery: same driver, same doubled-cap length retry, same registry gate, same per-call cost record. **The seat follows the run.** Its tier resolves by the same order the reviewers' does — a template's `synthesis.tier` first, then `--tier`, the panel's `tier`, the config's `default_tier`, and last the persona's own frontmatter — so a `--tier fast` panel has a `fast` judge rather than a frontier one costing more than all four reviewers. Its family is the template's `synthesis.family`, else the first non-`claude` family **the panel itself seated**, in the config's declaration order, else the first non-`claude` family in the config map; `claude` is excluded from both fallbacks for the reason every re-seat excludes it. `--synthesis-model` pins a concrete id. Both choices, and which level decided each, are recorded in the manifest as `judge_seat` and again on the call itself as `judge.tier_source` and `judge.family_source`.

**The patch is validated before it reaches disk** — the schema, then the references and enums, then a full trial merge — so `judgment.json` is never written invalid. One repair re-ask names the failing entries; a second failure is exit 3 with nothing written. A patch from `synthesis` carrying `rulings` is a **hard error** and earns no re-ask: stating a decision of record on a design fork is the one thing an unattended judge may not do.

**It is priced before it is made, on its own model.** The pre-flight resolves the judge's seat with the same function the judge stage uses, records it in the manifest as `judge_seat`, and prices **that** model — so the projection charges for the call that will actually happen, and the call that is made is the one the budget gate weighed. Because the seat follows the run's tier, the autonomous projection falls with `--tier` as an operator would expect: on the shipped `spec-review` panel the judgment call is $1.70 at `frontier`, $0.34 at `standard` and $0.04 at `fast`. A judge model the registry cannot price is a composition error, the same as an unpriced seat: a paid call left out of the projection is a budget gate that does not gate.

**The call is accounted like a seat and is not one.** It lands in the manifest as `judge` — model, family, tier, effort level and the parameter it bound to, cap, attempts, usage, reasoning tokens, cost, and `spend_approval_source` when the endpoint was gated — and its cost is added to `cost_usd_total`. It is deliberately **not** in `manifest.seats`: that array is the `unanimous` denominator and every entry in it with no report file is reported as a missing seat, so a judge seated there would make every autonomous run reconcile as under-seated by exactly one.

**The harness judge, end to end.** With `ensemble-judge` installed, an unattended run's judge stage is three steps and no money:

1. `run_panel.py` finishes Collect and **runs `reconcile.py`'s first pass**, which computes the provisional clusters, writes `<run-dir>/judgment-request.json` — the worksheet the agent answers — and prints the spawn instruction over it: the agent, the run directory, the package path, the worksheet, and the staging path at `<run-dir>/../.ensemble-staging/<run-id>/ensemble-judge/judgment.json`. **The worksheet has to exist before the spawn**: an agent pointed at a run directory with no `judgment-request.json` in it finds no question and halts, and that is a wasted turn nobody is told about. Invoking `reconcile.py --run-dir <dir>` yourself does the same two things in the same order.
2. You spawn exactly one subagent of that type. **Its brief is the paths above and nothing else**: the installed agent definition already carries what to read, in what order, and what it may not do. It reads `agents/synthesis.md` for the rubric, the run's reports, the pinned `inputs/` copies and the worksheet; it writes the patch to the staging path with its one `Write` tool; it returns a digest.
3. `reconcile.py --run-dir <dir>` ingests it — it looks in the run directory and then in that staging directory, so the `--judgment` flag is optional — validates it, merges it and writes both files.

**Zero reporting seats stops before any of that.** A reconciliation over zero reports is a lie whoever writes it, so the judge stage exits 2 rather than printing a spawn instruction over an empty run directory.

**It is held to its author, exactly as `synthesis` is.** The patch must say `author: "harness-judge"`; one claiming `host` is a patch error, exit 3, nothing written, because the author decides whether a design fork may be disposed anything but `flag-for-human` and whether `rulings` is allowed. Pointing at the staging path with `--judgment` does not excuse it — that is the _ordinary_ ingest, not an operator vouching for a file — and `--reconciler host` remains the way to say a human really wrote it. The mechanical floor binds it too: being a frontier model with file tools makes it more capable than the persona and no more entitled, because capability is not an owner. It lands in the manifest as `judge` with `leg: harness` and a null cost, and `cost_usd_total` does not move.

**A `judgment.json` already on disk is merged rather than re-bought** — that is the resume path. When the run's judgment is `synthesis`, such a patch must say `author: synthesis`; one claiming `host` is a patch error, exit 3, nothing written, because the author decides whether a design fork may be disposed anything but `flag-for-human` and whether `rulings` is allowed. If a human really did write it, say so with `--reconciler host` or point at it with `--judgment <path>`.

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

Add `--skip-claude` **only** when you intend to run the claude seats as harness subagents — which means the panel is at `standard` tier. Skipped seats are recorded in the manifest as `leg: harness, status: pending`, and step 4 below is where you spawn them.

**What the run does before it spends anything**, in this order:

| Stage | What happens |
| --- | --- |
| Claim | The run directory is created with an atomic exclusive `mkdir`. On collision the run id's sequence number increments and the claim retries, and the console says which directory the run actually took. |
| Materialize | The artifact and every reference are copied into `<run-dir>/inputs/`, made read-only, and hashed. **A revision is the SHA-256 of the bytes in `inputs/`**, with the git commit id recorded beside it when that file's working tree is clean. Seats read those bytes, never the working tree, so a mid-run edit cannot give two seats two different documents. |
| Resolve | Every file is found through the three-root cascade; seats resolve to tiers, families, models, connectors and efforts by the orders above; the registry is checked to cover every resolved model and every seat's abstract effort level is bound onto its model's own rung and checked against that model's vocabulary. **The manifest is written here, before the first dispatch**, with every seat `pending`, its `tier_source`, and its `substitution` when it was re-seated, so a crash after this point still leaves a manifest that knows how many seats there were and what each one was. It also carries `tier: {resolved, source, unanimous, per_seat}` — the tier the run actually resolved to and the level that decided it, distinct from `tier_default`, which is only the input to seating. A panel whose seats carry their own tiers has no single answer, and the block says so rather than picking one. |
| Project | The cost and token pre-flight, always printed, always labelled an estimate. It prices each seat at the registry's catalogue rate, then adds two labelled allowances — an output overrun multiplier for the routing premium a real call pays over the catalogue, and a repair allowance — plus a synthesis call when the run will actually make one. **It is written into the manifest as `projection`** — the per-seat table, the totals, the budget and the `decision` the gate came to (`within-budget`, `approved-over-budget`, `declined-over-budget`, `refused-over-budget`, `refused-spend-not-approved`, `declined-spend-not-approved`) — before the first paid call, so even a refusal leaves the numbers it refused over on disk and `cost_usd_total` has something to be compared against. **The connector's spend gate is answered here too, ahead of the budget gate**, and its answer recorded as `spend_approval`: "may this run bill this endpoint at all" is the prior question, and it is asked after the projection because the refusal names the projected spend. A context-overflowing seat is dropped from the panel here, and the recorded projection is then **recomputed over the seats that remain**, with `excluded_seats` naming what went: a projection for four seats is not the thing to measure three seats' billed spend against. The printed pre-flight is still the full table, overflow warning included. See the knobs below. |
| Dispatch | Each seat's record moves `pending` → `dispatching` by compare-and-set before its first paid call, and is updated as it returns. Every structured write is atomic. |

**Knobs on `run_panel.py`:**

| Knob | Default | Effect |
| --- | --- | --- |
| `--workspace` | the working directory | The project whose `.config/ensemble-review/` overrides the package. Passed straight through to every `dispatch.py` subprocess, so parent and child read the same files. |
| `--model` | none | `--model <seat-id>=<model-id>`, repeatable. Pins one seat to a concrete id and beats every tier source. **The seat's `family` is relabelled from the model** — the registry's `family` first, then the config's tier map — with the difference recorded in `family_relabel`. `reviewer_id` never moves, because it is minted from what the seat asked for. |
| `--smoke-test` | off | `--smoke-test <model-id>`: pin **every** seat to one model, drop the family target to 1, mark the manifest `smoke_test: true`, print a banner, and open the reconciliation's method caveat with "this run is a smoke test and is not evidence". For proving the pipeline end to end for cents. **Still priced and still gated by both gates** — the budget gate and the connector's spend gate, so a smoke test on a `metered` connector needs `--approve-spend` like any other run. A flag that turned off the controls in front of the money would be the opposite of what it is for. |
| `--draft` | off | The draft pass at $0: compose against the `harness` connector so every seat is a subagent the session spawns, drop the family target to 1, mark the manifest `draft: true`, print the spawn block and stop. The reconciliation opens by saying the run carries no corroboration claim — different words from a smoke test's, on purpose. Refuses a panel a metered endpoint would serve, and refuses a `synthesis` judgment, because the mode is defined by its price. **Mutually exclusive with `--smoke-test`.** See _The draft pass_ above. |
| `--panel` | inferred from the artifact's name, and never a reference-hungry template on a run with no references | The template, by name or by path. |
| `--tier` | the panel's, else the config's | Tier for every seat that does not set its own. |
| `--min-families` | the panel's, else `2` | The family target. A target and not a gate: a run below it proceeds, records `min_families: {target, seated, reporting}`, and says so in the method caveat. |
| `--max-tokens` | `32000` | The completion cap sent on every call. A model's `min_max_tokens` floor in the registry raises it for that seat; the cap actually sent is recorded. |
| `--budget-usd` | `5.00` | The pre-flight budget. Over it, an autonomous run refuses; an interactive one asks. |
| `--approve-budget` | off | Dispatch anyway. |
| `--autonomous` | inferred when stdin is not a tty | There is nobody to ask, so an over-budget projection writes `budget-refusal.json` and exits 4 **before any paid call**. |
| `--reconciler` | the panel's, else `default` | `host`, `synthesis`, `harness-judge` or `default` (host interactive; harness judge when unattended and installed, else synthesis). Resolved here, recorded in the manifest, and read back by `reconcile.py`. Also decides whether the projection charges for a judgment call — a host writes the patch itself and a harness judge runs on the subscription, so neither is charged. |
| `--synthesis-model` | none | Pins the judgment call to a concrete model id. Priced in the pre-flight exactly as it will be charged, and passed through to the judge stage, so the call the budget gate weighed is the call that is made. |
| `--reconcile` | `auto` | `auto` runs the Judge and Reconcile stages at the end of an autonomous run whose judgment comes from `synthesis`; `off` always stops after Collect and prints the command. |
| `--auto-apply` | `off` | `on` arms `apply_fixes.py`'s five-condition gate after the reconciliation is written — and **only on a run that exited 0**. A run that exited 3 for a missing seat has a reconciliation worth reading and agreement counts that are not the ones the panel was composed to produce, so nothing is written back from it unattended. That sixth condition is this build's, not the spec's; run `apply_fixes.py` by hand to apply from an under-seated run. |
| `--i-authored-this` | off | Asserts you wrote the document under review. Required by `--auto-apply on`. |
| `--fresh` | off | Claim a new run directory instead of resuming the one `--out` names. |
| `--models` | the cascade's `models/` directories, deep-merged per model | Point at another directory of model files, taken as given. |
| `--effort` | the seat's, the panel's, then the config's `default_effort` | `light`, `standard` or `deep` for every seat that does not set its own. |
| `--approve-spend` | off | Allow paid calls on a `billing: metered` connector at all. Not the same as `--approve-budget`. |

**Resume.** Re-running against an existing run directory resumes it, and **the reports on disk are the authority, not the manifest's seat statuses.** A seat whose report is present and validates is never re-dispatched — a repeat of the four-seat frontier panel is not a $3.09 no-op. A seat whose report is missing or invalid **is** re-dispatched, even where the manifest still calls it `ok`: that seat is demoted to `failed` with the reason recorded first, so the manifest stops asserting something the directory contradicts. A seat `dispatching` under a **live lease** is left strictly alone and reported as held by another process. A run whose inputs no longer hash to the manifest's `input_fingerprint` is refused with "start a new run id" rather than mixing two documents' reviews in one directory.

**A lease has an owner and an expiry.** `claim_seat` records `claimed_at` and `claimed_by: {pid, host}` beside the `dispatching` status. On resume, a `dispatching` seat with no report on disk is checked against that record: the claim carries no usable time, or the owning process ran on this host and is gone (past a 60-second grace window), or the claim is older than the four-hour ceiling — any of the three makes it a **stale lease**, and the seat is demoted to `failed` with `failure_reason: "stale-lease"` and re-claimed, exactly as a missing report is. Without this a run killed mid-dispatch left its seats `dispatching` forever and only `--fresh` could recover, which re-pays for the whole panel. The ceiling is four hours because a seat can legitimately run a very long time: the first unattended run's Kimi seat spent 3,286 s across three attempts, one of which alone took 1,801 s. A `dispatching` seat whose report **did** land is kept, not re-purchased: its owner died between writing the report and updating the manifest.

Because an existing directory resumes, **two runs naming the same `--out` share it.** The exclusive `mkdir` only protects a directory that does not exist yet; what protects the money is the per-seat compare-and-set, so only one of the two pays for any given seat.

**Exit codes:** `0` every expected seat validated; `1` usage or composition error, including an unpriced model or a refused resume; `2` terminal infrastructure failure — a provider auth failure halts the run with no further paid call, as does zero reporting seats; `3` the run is under-seated; `4` an autonomous budget refusal.

`dispatch.py` has one more, **`5`, and it is internal**: it is how a child tells `run_panel.py` that the provider refused this model rather than that the reviewer wrote a bad report, which is what the re-seat turns on. The parent consumes it and never re-emits it, so `5` is not in the spec's exit-code table and no panel run returns it. It is visible only when `dispatch.py` is invoked directly on a seat whose model the provider will not serve.

**The three retry paths inside a seat**, which are not the same thing. A transient provider error is retried three times at 1 s, 4 s and 16 s with the same prompt and the same cap. A truncated completion — `finish_reason: length` — is retried **once at double the cap with a fresh prompt**, and the truncated bytes are discarded rather than quoted back; a second `length` falls through to the repair path. A report that does not validate gets one repair re-ask, which is the only prompt that does quote the previous response back, and the quote it carries is capped at 60,000 characters, head and tail, with the elision noted on the attempt. Every call lands in `_meta.attempts` with its cap, its finish reason, its validation errors, its usage and its cost, failed calls included, and the manifest's `cost_usd_total` counts them.

**What counts as transient**, spelled out in `dispatch.TRANSIENT_EXCEPTIONS` rather than left to the standard library's exception hierarchy: 429, any 5xx, `urllib.error.URLError`, `socket.timeout`, `TimeoutError`, `ConnectionError`, `ConnectionResetError` and the whole `http.client.HTTPException` branch, `IncompleteRead` above all. The first unattended run lost a seat to `IncompleteRead(528 bytes read)` — a provider closing the connection part way through the body — because `IncompleteRead` descends from `HTTPException` and not from `OSError`, so it fell straight past a set that caught every socket failure and missed the one that happened. Each retry records the **exception class** on the attempt it belongs to. A 401 or 403 is not transient: it raises `AuthFailure` and halts the run with exit 2.

**A seat that dies in the transport still writes its record.** Exhausted retries, a provider error or a malformed body leaves `<reviewer-id>.failed.json` carrying `failure_stage: "dispatch"`, the exception class, the cap sent, the wall time and the transport's retries, and `run_panel.py` folds it into the manifest. A seat that was called and failed is never recorded with `attempts: null` and a null cost.

**`usage.cost` is the billed figure, zero included, and unbilled inference is recorded beside it.** Each attempt carries a `cost_source` — `provider` for the billed figure, `estimated` for one reconstructed from usage tokens at the registry's catalogue prices when the usage block carries no `cost` key at all, which is what `_meta.cost_estimated` flags. It also carries `upstream_unbilled_usd` when `usage.cost_details.upstream_inference_cost` exceeds what was billed, rolled up to `_meta.upstream_unbilled_usd` and to the manifest seat. The first unattended run answered one attempt with `finish_reason: error`, `cost: 0` and an upstream cost of $1.816461 on 97,967 prompt and 101,504 completion tokens — and the credit ledger settles what that means: `/credits` read `total_usage` 7.770718 before the run and 10.522836 after it, a delta of $2.752118 against the manifest's $2.752117. **An errored generation costs the account nothing.** So the upstream figure is never folded into `cost_usd` — `cost_usd_total` has to reconcile against a credit balance, and a total carrying $1.82 of inference nobody paid for does not — but it is not thrown away either, because it is what explains a seat that spent half an hour and billed zero.

### 4. Spawn the harness seats as subagents

**`--draft` is the normal route onto this leg, and on a draft run you do not compose this step at all.** `run_panel.py --draft` prints one exact spawn block per seat and stops the way the judge stage stops. Spawn all of them in one message and **follow the block rather than rewriting it**. What the block carries and how the run resumes afterwards is stated once, under _The draft pass — $0_ above.

**The hand-written brief below is for `--skip-claude` on a non-draft panel**, which is the only case that prints no block: a mixed run where some seats go through OpenRouter and the explicitly seated `claude` ones do not. Such a seat resolved against the **metered** tier map and the subagent will not run the model that resolved, so its `model` and `connector` stay null in the manifest — which a draft seat's do not, because there the resolved model is the one the block names.

**The brief and the printed block must carry the same content.** They are two renderings of one contract, and a seat briefed differently from its siblings is not comparable with them, which is the whole thing this skill measures. One subagent per skipped seat, spawned in a single message so they run concurrently. Each brief must carry:

- **The persona body verbatim** — everything after the frontmatter in `agents/lens-<lens>.md`, plus `references/finding-schema.md`. Byte-identical to what the OpenRouter leg sends. If the legs drift, the comparison this skill exists to make is meaningless.
- **The artifact and the references by path**, with the instruction to read _only_ those paths and nothing else in the repo.
- **The instruction to write** its report as JSON to its **seat-private staging path** — `<run-dir>/../.ensemble-staging/<run-id>/<reviewer-id>/<reviewer-id>.json`, which `scripts/lib/runs.py`'s `staging_dir()` computes — and to write nothing else. **Not into `<run-dir>`**: that directory holds its siblings' reports, and the staging rule exists so that no seat is ever _given_ a path into it. Blinding on this leg is prompt-enforced rather than sandboxed, so what staging buys is narrower exposure and not a guarantee.
- **The report envelope fields no script fills in** — `artifact` above all, plus `artifact_revision`. `render_harness_report.py` stamps only what it can derive from the filename and its flags, so a report with no `artifact` fails validation _after_ the turn has been spent.
- **The instruction to return a digest under 2000 characters** — reviewer id, verdict, counts by severity, the claim lines of its top three findings, and the report path. Nothing more: inter-agent messages truncate near 5500 characters, so disk is the channel and the message is only a pointer.
- **The instruction not to look for, read, or ask about any other reviewer's output.** Blindness is the invariant.

Per the global subagent rule, pin the model explicitly. Never `subagent_type: "fork"`.

### 5. Render the harness reports, then move them in

```bash
python3 skills/ensemble-review/scripts/render_harness_report.py <staging-dir>/<lens>-claude.json --model "<model the subagent ran on>"
mv <staging-dir>/<lens>-claude.json <staging-dir>/<lens>-claude.md <run-dir>/
```

It validates against the same schema the OpenRouter leg is held to and writes the `.md` beside the JSON, so both legs leave the same artifacts. **The move is yours**, and that is a build delta: `references/dispatch.md` says the script validates and moves it in, and the shipped script validates and renders in place. Do the move only after it validates — a run directory holding an unvalidated report is the state resume exists to repair. If it reports validation errors, send them back to that subagent as one repair re-ask; if the second attempt also fails, retire the seat and record it as a **missing seat** in the manifest and in the reconciliation. Never silently drop a reviewer — a four-seat panel that reconciled three reports has to say so, because agreement counts are meaningless otherwise.

### 6. Reconcile

This is the product. The reports are inputs. **`reconcile.py` is the only writer of `reconciliation.json` and `reconciliation.md`** — you never write either by hand. It computes what a script can compute and takes the rest from you as a **judgment patch**.

```bash
python3 skills/ensemble-review/scripts/reconcile.py --run-dir <run-dir>
```

The first run calls no models and writes nothing but `<run-dir>/judgment-request.json`, then exits 3 saying a patch is required. It needs the **pinned artifact** to do even that: it verifies that every `quote` and every `literal_edit.old_text` is real text in the document the seats read, and drops from its cluster any member whose anchor is not, recording the drop in `anchor_drops`. It resolves the artifact in three steps, and this is the order `_resolve_artifact` actually takes: **`--artifact <path>` first**, then the run's own `inputs/` copy — so a working-tree document that has since moved no longer matters — then the manifest's `artifact` path. It refuses to run, exit 1, when none of them resolves. Whether `--artifact` _should_ outrank the pinned `inputs/` copy is an open question in the spec, not a settled design: the flag is an escape hatch for a reconstructed run, and the revision check below is what stops it pointing the anchor check at the wrong document. That file is your worksheet: one entry per **provisional cluster**, with its members, the key that joined them, its provisional tier and the fields you owe it.

It also checks the **revision**: the resolved artifact's SHA-256 against the manifest's `artifact_revision`. A mismatch refuses, exit 1, naming both hashes — `reconciliation.json` is a function of the artifact's bytes, so reconciling against a document that has moved on checks anchors no reviewer ever saw. **In the ordinary case the check passes silently**: with no `--artifact`, the resolution takes the run's own `inputs/` copy, whose bytes cannot change, so editing the working-tree document after the panel ran does not stop you reconciling it. The refusal is there for the two cases where it can go wrong — `--artifact <path>` pointing the check at a different file, and an `inputs/` copy that has been deleted, leaving only a working-tree file that may have moved on. A manifest with no `artifact_revision` is **unpinned, not mismatched**: it warns and proceeds, which is how every run written before revisions existed still reconciles. `--render-only` is the one mode that runs without either check.

**What the script did on its own.** It collapsed each reviewer's duplicates, then grouped findings across reviewers on the two keys it can compute, in order — **normalized location equality** (NFKC, case-folded, markup and a leading `§` stripped, every run of non-alphanumerics collapsed to a space; equal, or one a prefix of the other at a space boundary) and then **quote overlap** (normalized the same way; containment either direction, or a longest common substring of at least 60 characters). Each group is a provisional cluster `P-n`. It tiered them, provisionally.

**What you owe it**, written to `<run-dir>/judgment.json` against `schemas/judgment-patch.schema.json`:

- **`claim_joins`** — provisional clusters that name the same defect from different anchors. Claim equivalence is a semantic call and is never computed; every cluster it forms is marked `match_key: "claim"` and `judgment: true`, so a reader auditing the document knows which clusters rest on a mind.
- **`splits`** — a mechanically joined group whose members turn out to name different defects. Two reviewers quoting one sentence to make two arguments is the common case. Splits apply first, so a join can name a split's product (`P-4.1`).
- **`singleton_labels`** — every **post-merge** cluster whose members are all one family — `singleton`, `same-family` or `corroborated-same-family` — is either a **blind-spot catch** or a **family-specific false positive**, with the reason. An unlabelled one is not an allowed output, and the script refuses the patch.
- **`severities`** — the arbitrated severity and why it survived, for any cluster whose members disagree. A severity argued with a `citation` beats one argued without; downgrade one step when a finding is a singleton, below `high` confidence, and uncited. Where the members agree the script takes their severity and records the spread; where they **disagree and you supply no entry**, that is a patch error and the script re-asks, because arbitrating a disagreement is step 4 and step 4 is yours.
- **`dispositions`** — `fix-now`, `flag-for-human` or `defer`, for **every** cluster, with the reason and the reviewers on each side. A `judgment-call` change kind is `flag-for-human` by default, because `judgment-call` means a design fork. As an interactive host you may dispose one some other way and say why in the `disposition_reason` — you have an owner to answer to, and `rulings` is reserved for a real decision of record. The `synthesis` persona has no such latitude: a patch authored by `synthesis` that disposes an all-`judgment-call` cluster anything but `flag-for-human` is rejected by the script. **The one exception is the tag.** A cluster whose judgment calls are every one of them tagged `gap` is a determinate fix filed under the wrong change kind, and `synthesis` may put it on the fix list; untagged is treated as a fork, so a report written before the tag rule is flagged exactly as it always was.
- **`contradictions`**, **`canonical_edits`**, **`altitude_splits`** and the **`method_caveat`** — who flatly disagreed with whom, which literal edit you accept for application and why, which lens is rating a document's seams at a different altitude than the rest (not a conflict, and never reported as one), and how many families actually ran.

Then run it again. It merges your patch, **recomputes every tier from the post-merge membership** — a tier computed before your joins is a tier computed against the wrong member set — validates, mints the final `BK-n` / `SF-n` / `NH-n` ids, computes the run-level verdict from arbitrated severity and disposition together, and writes both files atomically:

```bash
python3 skills/ensemble-review/scripts/reconcile.py --run-dir <run-dir> --judgment <run-dir>/judgment.json
```

There is no verdict field in the patch. If you disagree with the verdict, change a **disposition** — a claim about what should happen to a cluster, which is reviewable — rather than the number a rule produced. If the patch does not hold against the post-merge state, nothing is written and the failing entries are named; fix them and re-run. `--render-only` re-renders the Markdown from the JSON and is the one mode that does not need the artifact. Exit codes: `0` both files written and every expected seat validated; `1` usage, including no resolvable artifact; `2` zero reporting seats **at the judge stage**, or a provider auth failure on the judgment call — a reconciliation over zero reports is a lie, and there is nothing for a judge to judge, so it halts rather than spending a call to find that out; `3` a patch is required or did not validate, the composed judgment prompt does not fit the judge model's context window, or the run reconciled with a missing seat — in which case both files are written first, with the missing seat named in them; `4` a judgment call nothing has priced.

**Two gates sit in front of a direct `reconcile.py --reconciler synthesis`**, because that path makes a paid call and the cost pre-flight lives in `run_panel.py`.

- **Budget.** It refuses, exit 4, unless the run's own manifest carries a projection that priced a synthesis call — which every panel run dispatched with `synthesis` as its reconciler does. `--approve-budget` is the operator saying they have weighed it. The judgment call is the dearest single call most runs make: 45% of run 4's entire spend.
- **Context.** The composed judgment prompt is measured against the judge model's `context_limit` before it is sent. The per-report cap bounds one seat's share and not the whole, so four capped reports plus the artifact, every reference and the provisional clusters can overflow together. An overflow is a judge-stage failure: nothing written, the reason on `manifest.judge`, exit 3. A report the cap did cut is elided in the **middle** with a marker, the way the repair quote is, and every cut is recorded on `manifest.judge.report_elisions`.

`references/reconciliation.md` is the algorithm in full, as operator instructions, and is also the `synthesis` persona's own context file, so the two minds that can supply a patch are reading the same page.

### 7. Apply the corroborated fixes — optional, and off by default

```bash
python3 skills/ensemble-review/scripts/apply_fixes.py --run-dir <run-dir> --i-authored-this
python3 skills/ensemble-review/scripts/apply_fixes.py --run-dir <run-dir> --dry-run
```

This is the only thing in the skill that modifies the document under review. It reads `reconciliation.json` and `manifest.json` and nothing else, and a cluster is applied only if **all five** conditions hold: `disposition` is `fix-now`; `contradicted_by` is empty and `edit_conflict` is false; a `canonical_edit` is present and was explicitly accepted in the judgment patch, with an `old_text` occurring **exactly once**; the tier is `consensus` or `unanimous` with `n_families >= 2`, and never a same-family tier; and the artifact's current content hash equals the manifest's `artifact_revision`.

**The authorship assertion is not a formality.** The artifact is inlined verbatim into every seat's user message, so a hostile document can ask its own reviewers to return a consensus-shaped replacement, and the gate cannot tell that apart from a real corroborated fix. An author can. Without `--i-authored-this` it refuses and says why.

**`--dry-run` is the one path that runs without the assertion**, and it is the reason the assertion can stay strict: it resolves every anchor and prints exactly what would be applied while writing nothing — not the artifact, not even `applied.md`. A reader deciding whether to assert authorship should be able to see what they would be asserting it for. The spec states the refusal unconditionally; this is a build delta, and it is safe only because a dry run has no write path at all.

**Application is all-or-nothing.** Every candidate anchor is resolved against the current file first, no two edits may overlap, and only then is the file written once, atomically, with its mode preserved. A failure at any anchor aborts the whole set. A cluster that simply does not pass the gate is not a failure: it is reported as unapplied with its reason and the rest proceed. A single-family run cannot satisfy condition 4 at all, so auto-apply there is a **no-op that says so** rather than a quietly relaxed gate.

`applied.md` is the audit trail, and it is written whether or not anything was applied: per applied edit the cluster, the reviewers behind it, the source seat whose wording was used, who accepted it and the before/after diff — and then every candidate that was **not** applied with the condition it failed. `references/auto-apply.md` carries the gate and the threat model behind it.

## Independence invariants

A build that violates these is not this skill.

- **Blind and parallel.** Reviewers never see each other's prompts, outputs, digests or file paths. Sequencing is fine; leakage is not.
- **Write full, return a digest.** Every report lands on disk as JSON inside the project tree. The orchestrator receives at most 2000 characters back.
- **Differentiated by default.** Seats carry different lenses. Identical briefs are for corroboration-voting on a single yes/no question, and v1 does not implement that mode.
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
| "How do I override one model, one panel or one driver for my project or this machine?" | `scripts/lib/paths.py` — the three-root cascade and the two merge rules |
| "Which family does `non-claude` actually get?" | `scripts/lib/seating.py` — the tier order and the four seating passes |
| "Does the clustering still work?" | `python3 scripts/tests/test_replay.py` — the deterministic replay of the 2026-09-18 panel |
| "Which models run which family?" | `templates/models/` — one file per model, each naming its family and tiers |
| "Which endpoint does a run bill, and may it?" | `templates/connectors/openrouter.json` |
| "How do I review a draft without paying anything?" | `run_panel.py --draft --panel draft-review` — and _The draft pass_ above |
| "What does the measured panel seat?" | `templates/panels/spec-review.json` |
| "Which panel templates are there, and which needs references?" | `templates/panels/` — and the table under _Dispatch model_ |
| "What does each lens actually ask, and where does it stop?" | `agents/lens-*.md` — and the catalog table under _Dispatch model_ |
| "When is a finding a `judgment-call` rather than a gap?" | `references/finding-schema.md` — `change_kind`, with a worked positive and negative |
| "Is the catalog still well-formed?" | `python3 scripts/tests/test_catalog.py` — every persona and every template, as data |
| "Is it worth running a panel on this at all?" | `references/panel-design.md` — the gate, the seats, and what it costs |
| "How does a seat actually get dispatched?" | `references/dispatch.md` — both legs, the three retry paths, the digest cap |
| "What do I owe the reconciler, in prose?" | `references/reconciliation.md` — the seven steps and how to write a patch by hand |
| "When will it write to my document?" | `references/auto-apply.md` — the five conditions and the authorship assertion |
| "Who judges when nobody is watching?" | `agents/synthesis.md`, `agents/judge.md` for the harness judge, and `scripts/lib/judge.py` for who is chosen and where it is seated |
| "What may a panel template say?" | `scripts/lib/panels.py` — the whole recognized vocabulary, and the refusal |
| "How do I prove the pipeline without paying for a review?" | `run_panel.py --smoke-test <model>` |
| "How do I set this up on a new machine?" | `./install.sh` |
| "What does a report of this quality read like?" | `.agents/subprojects/annotate/pm/reviews/fullspec-review-*.md` |
| "What should the reconciliation look like?" | `.agents/subprojects/annotate/pm/reviews/fullspec-reconciliation.md` |

## Reference file index

| Key | File | Domain | Used by |
| --- | --- | --- | --- |
| `panel-design` | `references/panel-design.md` | Is this worth a panel; how to choose seats; the cost figures; where the bytes go | step 1 |
| `dispatch` | `references/dispatch.md` | Both legs, the blinding rules, the three retry paths, the digest cap | steps 3 to 5 |
| `finding-schema` | `references/finding-schema.md` | Domain knowledge — the output contract | every lens persona, both legs |
| `reconciliation` | `references/reconciliation.md` | The seven steps as operator instructions, and how to write a judgment patch | step 6; also the `synthesis` persona's own context |
| `auto-apply` | `references/auto-apply.md` | The five-condition gate, the threat model, the audit trail | step 7 |
| `install` | `install.sh` | First-run setup: python, the key, the registry seed | once per machine |
| `reconcile` | `scripts/reconcile.py` | The only writer of both reconciliation files; the Judge stage | step 6, interactively and autonomously |
| `apply-fixes` | `scripts/apply_fixes.py` | The gated literal edits and `applied.md` | step 7 |
| `judge` | `scripts/lib/judge.py` | Who supplies the judgment, where the call is seated, what it is shown, whether it fits | `run_panel.py` and `reconcile.py` |
| `judge-agent` | `agents/judge.md` | The harness judge: what it reads, what it writes, what it may not do | the orchestrating session, at the judge stage |
| `panels` | `scripts/lib/panels.py` | The panel-template vocabulary and the unknown-key refusal | `run_panel.py` at load |
| `driver-contract` | `scripts/backends/base.py` | What a connector must export and return | anyone writing a second driver |
| `reconcile-core` | `scripts/lib/reconcile_core.py` | Match keys, tiers, patch merge, verdict — importable without the CLI | `reconcile.py`, the replay test |
| `judgment-patch` | `schemas/judgment-patch.schema.json` | The contract for the judgment you supply | step 6 |
| `reconciliation` | `schemas/reconciliation.schema.json` | The contract for the product | `reconcile.py`, later `apply_fixes.py` |
| `paths` | `scripts/lib/paths.py` | The three-root cascade, the two merge rules and the model-file slug rule | every script that loads any packaged file |
| `connectors` | `scripts/lib/connectors.py` | What a connector file holds, the billing postures and the spend gate | anyone binding a second endpoint |
| `drafts` | `scripts/lib/drafts.py` | The draft pass: the harness connector, the metered refusal, the spawn block and the caveat prologue | `run_panel.py` under `--draft`, and `reconcile_core` for the prologue |
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
| `synthesis` | `reconciliation.md`, `finding-schema.md` | Every validated report in full, the provisional clusters, every reference inlined, the artifact at its pinned revision |
| `ensemble-judge` (harness) | none — it reads `agents/synthesis.md` and both references from the package itself | The run directory and the package path, in the spawn instruction. It goes and gets the reports, the clusters and the pinned `inputs/` copies |

## Still open

Say this plainly to anyone who asks what the skill does. None of it is stubbed; it is absent.

- **Mid-flight budget metering** — the projection is a **dispatch gate only**. Nothing meters spend as seats return, and a panel that overruns its projection runs to completion. A seat re-seated onto another family mid-run makes a call the projection never priced.
- **Harness reports are not moved in by any script.** `references/dispatch.md` says the seat writes to staging and the script validates and moves it in; `render_harness_report.py` validates and renders in place, and the move is the operator's `mv`. The staging path is now what the brief in step 4 tells the subagent to write to, so the contract holds on the leg that matters — but the last step is a hand one.
- **Per-seat `timeout_s`** — the knob has a specified default of 900 seconds and a specified behaviour, and no implementation. A hung provider hangs that seat until the process is killed.
- **`mode: identical`** — the knob does not exist, so it is not refused by name yet.
- **`verify_web`** — declared `false` on every live template and read by nothing, and the spec's composition error for `verify_web: true` on a tool-less seat is not implemented, so a workspace panel setting it true runs unrefused.
- **Harness-leg blinding is prompt-enforced, not sandboxed.** A harness subagent keeps file tools on the repository, so staging narrows exposure rather than removing it. That leg is opt-in for exactly this reason.
- **Code review** — out of lane by decision rather than by omission: `code-review.json` ships as a named stub that refuses and routes to `/code-review`, and the two code lenses stay out of the catalog.

Built since the last revision and no longer on this list: **the draft pass** (`--draft`, the `harness` connector, the `draft-review` template and the printed spawn block), **the harness judge** (`agents/judge.md`, installed by `install.sh`, the default unattended judgment supplier where it is present), **the strict panel-template loader**, **`--smoke-test`**, **the budget gate and the context-limit check on a direct judgment call**, and the four smaller follow-ups five dogfood runs exposed — the `--model` family relabel, `tier.source` no longer reading `--model`, the judge's per-report cap eliding the middle and recording it, and the measured cost table carried through run 5.

Built since v0 and no longer on this list: the completion cap and its length retry, the per-model cap floor, the model registry and `refresh_models.py`, the cost and token pre-flight with its budget gate, content-hash revisions with a materialized `inputs/` directory, resume with a compare-and-set claim, the three-root cascade in `lib/paths.py`, the tier resolution order and the `non-claude` / `distinct` constraint resolvers in `lib/seating.py`, re-seating on a missing cell or an unreachable model, the other five lenses, the other three panel templates, panel inference with its references rule, `min_families` counted at both ends and named in the method caveat, the `source-credibility` citation rule in the validator, **`install.sh`**, **the `synthesis` persona and the whole autonomous path**, **`apply_fixes.py` and the five-condition gate**, and **the four reference documents plus the driver contract in `backends/base.py`**.

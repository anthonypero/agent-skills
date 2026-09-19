# ensemble-review

A panel of independent reviewers for one document, instead of one reviewer with one shape of blind spot.

Each seat carries a different **lens** — fidelity to source, buildability, internal consistency, adversarial — and runs on a different **model family**. The reviewers never see each other's prompts or outputs, every report lands on disk as validated JSON with a Markdown rendering beside it, and the host session reconciles them into one document that says which findings two families agreed on and which came from exactly one mind.

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
│   ├── lens-fidelity.md
│   ├── lens-buildability.md
│   ├── lens-consistency.md
│   └── lens-adversarial.md
├── references/
│   └── finding-schema.md           # how to fill a finding; loaded into every persona prompt
├── schemas/
│   ├── review-report.schema.json   # the report envelope and the finding, draft 2020-12
│   ├── judgment-patch.schema.json  # the judgment a mind supplies to the reconciler
│   └── reconciliation.schema.json  # the consensus document reconcile.py writes
├── templates/
│   ├── config.json                 # tier × family → concrete model id
│   ├── models.json                 # the registry: prices, context limits, effort vocabularies, priors
│   └── panels/spec-review.json     # the measured four-lens panel
└── scripts/
    ├── dispatch.py                 # one persona × one family → a validated report on disk
    ├── run_panel.py                # claim → materialize → resolve → project → dispatch → manifest
    ├── render_harness_report.py    # the same artifacts for the harness leg
    ├── reconcile.py                # the only writer of both reconciliation files
    ├── refresh_models.py           # pull the OpenRouter catalogue, diff the registry, report what moved
    ├── backends/__init__.py        # load_driver: a package module, or a driver at a file path
    ├── backends/openai_compat.py   # the only shipped driver: POST {base_url}/chat/completions
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
        ├── test_reconcile_revision.py  # the artifact-revision check
        ├── harness.py              # a temp workspace: artifact, config, registry, panel
        └── fake_backend.py         # a scripted connector, loaded by file path like any driver
```

## Tiers and model picks

`templates/config.json` maps **tier × family** to a concrete model id. The tier is chosen by stakes: `frontier` for a spec, plan or decision that gates a build; `standard` for routine documents and second passes; `fast` for a sanity pass or a dry run of the pipeline. `--tier` defaults to `frontier`; a panel may set its own and a seat may override that; `--tier` on the command line beats both so an operator can force a whole panel cheap; `--model` beats everything.

Seven families, picked from the OpenRouter catalogue and a leaderboard pass on 2026-09-18:

| Family | `frontier` | `standard` | `fast` |
| --- | --- | --- | --- |
| claude | `anthropic/claude-sonnet-5` | `anthropic/claude-sonnet-5` | `anthropic/claude-sonnet-5` |
| openai | `openai/gpt-6-astra` | `openai/gpt-5.6-sol` | `openai/gpt-5.6-luna` |
| kimi | `moonshotai/kimi-k3` | `moonshotai/kimi-k3` | `moonshotai/kimi-k3` |
| glm | `z-ai/glm-5.3` | `z-ai/glm-5.3-flash` | `z-ai/glm-5.3-flash` |
| xai | `x-ai/grok-4.6` | `x-ai/grok-4.6` | `x-ai/grok-4.6` |
| google | `google/gemini-3.8-flash` | `google/gemini-3.8-flash` | `google/gemini-3.6-flash` |
| deepseek | `deepseek/deepseek-v4-pro-0813` | `deepseek/deepseek-v4-pro-0813` | `deepseek/deepseek-v4.1-flash` |

Notes on the picks, because they will go stale and the next person needs the reasoning rather than the answer:

- **Every id was confirmed present in `GET https://openrouter.ai/api/v1/models` before it was written down.** Re-run that check before trusting this table; a spec that names today's model ids is stale in a quarter, and so is a config.
- **`frontier` means the newest general reasoning model, not the cheapest or the most specialised.** Mini, flash-lite, `:batch` and image variants are excluded from `frontier` by construction: batch endpoints trade latency for price and a panel is a foreground operation, and the small variants are exactly the reviewers whose blind spots correlate with each other.
- **Google's ceiling is a Flash model.** OpenRouter's newest Gemini Pro is `gemini-3.1-pro-preview` from February; the 3.5–3.8 line is Flash-only and scores higher at lower cost. Google is therefore the one family whose `frontier` entry is a Flash model, and that is a real asymmetry to keep in mind when reading a panel where Google was the dissenting seat.
- **`xai` and `kimi` name one model across all three tiers,** because each vendor currently fields one model worth seating. That is not a placeholder; it means a `fast` run does not get cheaper on those seats. Watch the cost line.
- **Non-`pro` variants where a `pro` exists.** `openai/gpt-6-astra-pro` is the same underlying model served with `reasoning.mode: pro`. Paying for a higher reasoning mode on one seat and not the others would make that seat's findings incomparable with the rest of the panel, which is the one thing this skill cannot tolerate. Same reasoning for `gpt-5.5-pro`.
- **The Claude family is Sonnet 5 at every tier, and the default panel seats no Claude family.** Decided 2026-09-18 after the first dogfood run: the Fable seat cost $3.58 of a $4.80 panel and its unique findings were matched by a $0.34 GLM seat. The stronger reason is decorrelation: the host session that authors and reconciles these documents is a Claude model, so a Claude reviewer is the seat most correlated with the artifact. Seat `claude` explicitly when a second Claude opinion is wanted. `run_panel.py --skip-claude` still exists for running that seat as an Opus harness subagent on the subscription; it is **off by default**.

## The driver seam

The HTTP call lives in `scripts/backends/openai_compat.py`, loaded by the provider entry's `"type"` field. `dispatch.py` contains no backend-specific logic.

OpenRouter serves Anthropic's models through the same OpenAI-compatible `/chat/completions` endpoint as everyone else's, so **one driver covers every family on the panel**. A native `anthropic` driver against `api.anthropic.com`, or a `harness` driver that shells out to a local agent CLI, would each be one more file in `backends/` and a `"type"` change in the config — no change to `dispatch.py`. That is the whole point of the seam, and it is the reason the framework's §9 shape is preserved here (minus the tool arguments, since these personas are single-turn and tool-less).

`dispatch()` is the framework-shaped entry point returning a string. `dispatch_detailed()` is the same call returning the decoded response alongside the text, which is how usage and cost reach the `_meta` block and the manifest.

## Report schema and validation

`schemas/review-report.schema.json` is JSON Schema draft 2020-12 and carries exactly the spec's two tables: the report envelope, and the finding with its thirteen fields. `references/finding-schema.md` is the prose version that gets loaded into every persona's system prompt, so the reviewer and the validator are reading the same contract.

Validation is hand-written in `scripts/lib/report.py` — required fields, types, enums, length caps, the `F<n>` id pattern — so the skill runs on any Python 3 with nothing installed. It also enforces the two rules the schema can only state in prose: a `literal-edit` finding must carry a `literal_edit` block, and every finding from the **fidelity** lens must carry a `citation`. A report that fails gets **one repair re-ask** carrying the validation errors back; a second failure writes the raw response to `<reviewer-id>.invalid.txt` and exits non-zero, and the seat is flagged as missing rather than silently dropped.

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
- **Four of the eight lenses** — `completeness`, `security`, `alternatives`, `second-order` are specced and unwritten. `spec-review.json` carries a fifth seat under `optional_seats` (completeness on `xai`) that cannot be enabled until that persona exists.
- **The `non-claude` and `distinct` family constraint resolvers** — not implemented. A seat's `family` must be a named family from the config tier map, so `spec-review.json` names four concrete families where the spec writes constraints. Composing a panel means choosing families by hand.
- **The other three panel templates** — `research-report`, `design-decision`, and the deferred `code-review` stub.
- **`lib/paths.py` and the workspace override cascade** — every file still loads from the package. `--config` and `--models` point elsewhere by hand, and `backends/__init__.py` will load a driver from a file path so a project can bind a family to its own connector, but there is no `<project>/.agents/ensemble-review/` root and no deep merge of `config.json`. The manifest's `roots` block already records which directory each loaded file came from, so the audit record is ready for a cascade that does not exist.
- **Mid-flight budget metering** — the projection is a dispatch gate only. Nothing meters spend as seats return, so a panel that overruns its projection runs to completion.
- **`min_families` enforcement and re-seating** — `min_families_target` is recorded and never acted on, and an unreachable family is not re-seated. A context overflow is deliberately never re-seated and is reported as under-seated instead.
- **`mode: identical` and `verify_web`** — neither knob exists, so neither is refused by name.

Built since the first cut of this list: the completion cap and its length retry, the per-model cap floor, the model registry and `refresh_models.py`, the cost and token pre-flight with its budget gate and `budget-refusal.json`, content-hash revisions with a read-only `inputs/` directory, and resume with a compare-and-set claim.

## Rebuild items

### Fixed after the first real run

The first panel against a real document (`design/v1-spec.md`, 2026-09-18, four seats, $4.80) exposed four defects in the tooling itself. They were fixed in `scripts/` rather than deferred with the rest of the rebuild, because each one destroyed or hid information the run had already paid for:

- **Length caps truncate; they no longer reject.** The GLM seat was lost over a 696-character summary and two quotes at 336 and 301 — 1, 36 and 96 characters past their caps, on a report whose substance was fine. `lib/report.py` now trims an over-long string to its cap with a trailing ellipsis, records the trim in `_meta.truncated` (field path, cap, original length, finding id) and renders a truncation count in the Markdown run line. Rejection is reserved for what a trim cannot repair: missing required fields, wrong types, bad enum values. The rule is in the shared validator, so it applies to the first attempt, the repair re-ask and `render_harness_report.py` alike.
- **Every attempt is recorded.** `_meta.attempts` carries one row per call — attempt number, `ok` or the list of validation errors, usage, cost, `finish_reason`, driver notes, response id — and the manifest surfaces a per-seat `attempts` count and `first_attempt_errors`. Two seats in that run needed a repair and the manifest could not say why. This replaces `_meta.usage_all_attempts` and `_meta.repair_errors`, which the new block subsumes.
- **A failed seat carries its cost.** GLM's two attempts cost about $0.34 by the dashboard and $0 by the manifest. `dispatch.py` now writes `<reviewer-id>.failed.json` — the same `_meta` block a passing seat gets, plus the validation errors and a pointer to the raw response — beside `<reviewer-id>.invalid.txt`, and `run_panel.py` folds its usage, cost and attempt summary into the seat record and into `cost_usd_total`. A failed seat's spend is now in the run total and on the console line.
- **`render_harness_report.py` no longer assumes the harness leg.** A salvaged OpenRouter report came out labelled `leg: harness, tier: standard`, which is a false audit record. `--leg {harness,openrouter}` (default `harness`) and `--tier` say what the seat was; a `leg` or `_meta.tier` already in the JSON is believed over the flag and a note goes to stderr when the two disagree; `_meta.provider` follows the resolved leg; and an unknown tier on the OpenRouter leg stays unset rather than being invented.

### Fixed in the v1 rebuild

- **`max_tokens` is a knob, and `length` is retried before repair.** Every call carries an explicit cap, `--max-tokens` sets it per run (default 32000), and a per-model `min_max_tokens` floor in the registry raises it for the seat that needs it — Kimi K3 ships at 64000, because run 2's consistency seat spent a whole 32000-token cap on reasoning and returned nothing. A `finish_reason` of `length` is retried **once at double the cap with a fresh prompt**: the truncated bytes are discarded rather than quoted into a 90k-token repair prompt, which is what that run paid $0.66 for. A second `length` falls through to the repair path. Every call's cap, finish reason, usage and cost land in `_meta.attempts`, failed calls included.
- **Reasoning-effort vocabularies are now data.** `templates/models.json` carries each model's `effort_vocabulary`, refreshed from the catalogue's own `reasoning.supported_efforts`. `dispatch.py` reads the per-model effort map in `config.json`, checks the string against that vocabulary, and sends it as `reasoning.effort`; a model absent from the map is still sent no effort parameter at all, and an effort the model does not accept is dropped with a warning rather than sent into a 400. The manifest records the effort actually sent per seat, so a reader can tell a panel that ran at mixed depths.
- **A projection exists, and it is built on priors rather than on prompt size.** `lib/budget.py` prices each seat as `input_price × prompt tokens + output_price × the model's output-token prior`, plus a 50% repair allowance per seat and one synthesis call. Against run 2's numbers that lands near $1.5 on a run that cost $1.47 and was projected at $1.00 by prompt size alone. Prompt tokens are approximated at four characters per token — no tokenizer dependency — and every figure is labelled an estimate wherever it is printed.
- **The artifact has a revision, and three mechanisms use it.** `run_panel.py` copies the artifact and every reference into a read-only `<run-dir>/inputs/`, and a revision is the SHA-256 of the bytes written there, with the commit id beside it when that file's tree is clean. Seats read those bytes; resume refuses when the input fingerprint has moved; `reconcile.py` hashes the artifact it resolves against the manifest's `artifact_revision` and refuses on a mismatch. **In the ordinary case that refusal never fires**, because `reconcile.py` resolves the run's own `inputs/` copy first and those bytes cannot change — editing the working-tree document afterwards is fine and always was. It fires only when `--artifact <path>` overrides the resolution, or when the `inputs/` copy is gone and the working-tree file has moved on since. A null revision is unpinned, warned about, and allowed through, which is what keeps the frozen replay fixture running.

### Still open

- **Reasoning tokens bill as output, and several frontier models will not let you opt out.** `_meta.reasoning_tokens` records them per seat from `usage.completion_tokens_details.reasoning_tokens` when the provider reports it. They are often the larger half of a seat's bill and are invisible in the completion, which is why the projection is built on a measured output-token prior rather than on prompt size.
- **The priors are four measurements and eight defaults.** `openai/gpt-5.6-sol`, `z-ai/glm-5.3-flash`, `moonshotai/kimi-k3` and `x-ai/grok-4.6` carry run 2's accepted-attempt completion tokens and `openai/gpt-6-astra` carries run 1's; every other model in the registry carries a flat 16000 labelled `default`. A projection for a panel of unmeasured models is a guess with a receipt.
- **The repair re-ask resends the whole user message.** It has to, because the reviewer must re-quote verbatim — but it roughly doubles the cost of a seat that needed one. A cheaper repair that sends only the previous output and the errors would save money and risk fabricated quotes; measure before choosing.
- **The spec's `min_families` is recorded and never enforced.** `run_panel.py` writes `min_families_target` into the manifest and does nothing with it. The degradation path — re-seat a lens onto an available family, record the substitution, continue — is specced and unbuilt.
- **`google` at `frontier` is a Flash model.** The v3 spec's normative config block leaves the Google frontier cell **empty**, because no Pro-class Google model is on OpenRouter, and adds the `effort` and `provider_routing` keys. `templates/config.json` still carries the Flash model at frontier and neither key; the file was out of scope for the stage that built the registry, so the config and the spec disagree here today.
- **The manifest's compare-and-set is guarded by a lock directory, not by a transaction.** `manifest.lock` is an exclusive `mkdir` held across one read-modify-write, and a lock older than five minutes is broken so a crashed run cannot wedge a resume. That is enough for the two cases this skill has — several seat threads in one run, and two resumes on one directory — and it is not a distributed lock.
- **Per-seat `timeout_s` is unimplemented.** The spec gives it a 900-second default and a defined behaviour — expiry flags the seat missing at stage `timeout` and the panel continues — and `run_panel.py` passes no timeout to the `dispatch.py` subprocess at all. A hung provider hangs that seat, and with it the `as_completed` loop, until the process is killed. `MISSING_STAGE` already maps a `timeout` status, so the reconciliation half is ready for the run half that is missing.
- **The exclusive-mkdir claim does not stop two runs sharing a directory; the seat compare-and-set does.** The spec says two concurrent runs on one artifact can never write into the same directory, and the claim alone delivers that only for a directory that does not yet exist. Because an existing run directory **resumes** by design, two runs both naming the same `--out` do share it. What actually protects the money is one level down: a seat moves `pending`/`failed` → `dispatching` by compare-and-set before its first paid call, so only one of the two runs pays for any given seat, and the other reports it held.

## Conventions

The skills repo is public. No key value appears in any file here: `dispatch.py` resolves the OpenRouter key through `skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY`, falling back to `$OPENROUTER_API_KEY`, and exits naming both paths it tried if neither answers. Scripts are standard-library Python 3 with a `main()` and `argparse`. Reports never land in the session scratchpad — harness subagents cannot write there — so every run directory lives inside the reviewed project's tree.

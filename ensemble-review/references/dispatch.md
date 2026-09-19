# Dispatch — both legs, the blinding rules, and the digest cap

How a persona, an artifact and a family become a validated report on disk. Two legs reach a reviewer, and the invariants below are what make their outputs comparable.

## The invariant the whole skill rests on

**One system message, both legs.** The persona body plus its `context` references is **byte-identical** on the OpenRouter leg and the harness leg. If the legs drift, the comparison this skill exists to make is meaningless, and no amount of care downstream recovers it.

The **user** message differs by necessity: the OpenRouter leg receives the artifact and references inlined, the harness leg receives paths to the materialized copies. That difference is recorded per seat in the manifest as `input_delivery: inlined | materialized-paths`, rather than denied.

## Prompt composition

**System message** = the persona body — everything after the frontmatter — plus every reference its frontmatter `context` names, in that order, separated by a rule. A lens names one (`finding-schema.md`); `synthesis` names two (`reconciliation.md`, then `finding-schema.md`).

**User message** = the artifact and every reference **inlined verbatim**, delimited by explicit BEGIN/END markers and each labelled with its **repo-relative** path. The bytes come from the run's read-only `inputs/` copy; the label is the working-tree path, because a finding that cited `inputs/v3-spec.md` would be unusable. It closes with the instruction to return only the JSON object, no prose and no fence.

**Personas have `tools: []` and never read files.** The alternative — giving each persona file-read tools — makes every leg a tool-use loop, makes the connector carry tool translation for no gain, and makes two reviewers' inputs differ by what they chose to open. Inlining makes the user message identical across families, which is the precondition for comparing them.

**The artifact is untrusted input.** It is inlined verbatim, and a document can contain instructions as easily as content. Two rules follow here — personas quote verbatim, and the reconciler checks that every quoted span is real text in the pinned artifact — and the third is in `auto-apply.md`.

## The blinding rules

- **Reviewers never see each other's prompts, outputs, digests or file paths.** Each gets a fresh context holding only the artifact, the references, its own lens body and the finding schema.
- **Parallel is an optimisation; blind is the invariant.** A run that cannot fan out still runs the reviewers blind and in any order. Sequencing is fine; leakage is not.
- **On the OpenRouter leg this holds by construction.** The personas are tool-less, everything is inlined, and there is no path by which one seat could reach another's bytes.
- **On the harness leg it does not, and this file says so plainly.** A harness subagent keeps file tools on the repository, so it _could_ glob its way to a sibling's report. What staging buys is narrower exposure, not a guarantee. Blinding there is **prompt-enforced**: the prompt names only the materialized inputs and the seat's own staging path, and the seat is instructed to read nothing else. Closing the gap needs a spawn sandbox that denies reads outside `inputs/` and the seat's staging directory, which the harness does not offer today.

## The OpenRouter leg

`run_panel.py` fans out one `dispatch.py` subprocess per seat, so a seat that fails takes down only its own seat.

Before the first paid call, in this order: the run directory is claimed with an atomic exclusive `mkdir`; the artifact and every reference are copied into a read-only `<run-dir>/inputs/` and hashed; seats resolve to families, tiers, models, connectors and efforts, and the registry is checked to cover every one of them; the manifest is written with every seat `pending`; and the cost and token pre-flight runs. Each seat's record then moves `pending -> dispatching` by compare-and-set before its own first call, which is what stops two resumes on one directory both paying for the same seat.

Each seat writes `<run-dir>/<reviewer-id>.json` and `.md` and prints a digest.

## The three retry paths, which are not the same thing

They are separate mechanisms with separate causes, and conflating them is what one dogfood run paid $0.66 to learn.

1. **A transient provider error** is retried **three times at 1 s, 4 s and 16 s**, same prompt, same cap. It lives inside the transport, so a driver never implements it. A 401 or 403 is **not** transient: it raises `AuthFailure`, and the run halts with exit 2 rather than spending three more calls proving the key is still wrong.
2. **A truncated completion** — `finish_reason: length` — is retried **once at double the cap, with a fresh prompt**. The truncated bytes are discarded and never quoted back. A truncation is not a reviewer error, so it is handled _before_ validation; only a second `length` falls through to the repair path.
3. **A report that does not validate** gets **one repair re-ask**, which is the only prompt that does quote the previous response back, because there the previous response is the thing to fix. The quote is capped at `dispatch.REPAIR_QUOTE_CHARS` — 60,000 characters, about 15,000 tokens, kept as head and tail with the middle elided and the elision noted on the next attempt. That is enough to carry a whole 20-finding report verbatim and an order of magnitude below the 200,000 characters it replaced, which was less a cap than a promise to pay for one. A second failure retires the seat: the raw response is kept at `<reviewer-id>.invalid.txt`, the attempt record at `<reviewer-id>.failed.json`, and the seat is recorded as **missing** in the manifest and in the reconciliation. It is never silently dropped.

**What counts as transient** is spelled out in `dispatch.TRANSIENT_EXCEPTIONS` rather than left to the standard library's exception hierarchy: 429, any 5xx, `urllib.error.URLError`, `socket.timeout`, `TimeoutError`, `ConnectionError`, `ConnectionResetError`, and the whole `http.client.HTTPException` branch — `IncompleteRead` above all. The first real unattended run lost a seat to `IncompleteRead(528 bytes read)` 147 seconds in, because `IncompleteRead` descends from `HTTPException` and not from `OSError`; the old set caught every socket failure and missed the one that happened. Each retry records the **exception class** on the attempt it belongs to, so a reader can tell a flaky provider from a slow model.

**Every call is logged**, failed calls included: `_meta.attempts` carries one entry per call with the cap it was sent, its finish reason, its validation errors, its usage and its cost. A failed seat's spend is in `cost_usd_total`. One run's manifest reported $0.00 for a seat that had spent about $0.34, and the run total was under-reported by exactly that.

**A call that raised is still a call that was made.** Exhausted transient retries, a provider error or a malformed body writes `<reviewer-id>.failed.json` with `failure_stage: "dispatch"` and one attempt entry carrying the exception class, the cap sent, the wall time and the transport's retries. Before this, a seat that died in the transport reached the manifest as a 147-second seat with `attempts: null` and a null cost — a seat that reads as though it was never called.

**`cost_source`, and `upstream_unbilled_usd` beside it.** Each attempt carries a source for its `cost_usd`: `provider` for the account's billed figure, and `estimated` for one reconstructed from usage tokens at the registry's catalogue prices, used only when the usage block carries no `cost` key at all — `_meta.cost_estimated` flags it, because catalogue prices are not what a routed call pays.

**`usage.cost` is authoritative including when it is 0**, and that was settled against the ledger rather than assumed. The first unattended run answered one attempt with `finish_reason: error`, `cost: 0` and `cost_details.upstream_inference_cost: 1.816461` on 97,967 prompt and 101,504 completion tokens. OpenRouter's `/credits` endpoint read `total_usage` 7.770718 before the run and 10.522836 after it — a delta of $2.752118 against the manifest's recorded $2.752117, to the cent. The errored generation was **not** billed. So the upstream figure is recorded separately as `upstream_unbilled_usd` (on the attempt, summed into `_meta` and onto the manifest seat) whenever it exceeds the bill, and is never added to `cost_usd`: the billed total is the number that has to reconcile against a credit balance. What it buys is the explanation for a seat that ran for half an hour and billed nothing.

## The completion cap

Every call carries an explicit `max_tokens`, default **32000**, settable per run with `--max-tokens`. **Reasoning tokens bill against it on most providers**, so it is a cost knob and a truncation knob at once: one seat spent an entire 32,000-token cap on reasoning and returned zero content. A per-model `min_max_tokens` floor in the registry raises the cap for the seat that needs it, and the cap actually sent is recorded per attempt, so a reader can tell a model that ran out of room from a model that produced bad JSON.

## Effort

Reasoning-effort vocabularies differ by vendor, so the effort map is **keyed by concrete model id** rather than by tier or family, and a model absent from the map is dispatched with **no effort parameter at all**. An effort the registry says the model does not accept is a **composition error, exit 1** — refused at Resolve and again in `dispatch.py` — because a panel whose seats ran at unintended depths is not the comparison this skill exists to make. A vocabulary the registry simply has not learned is not the same as an unsupported value and is allowed through.

## Validation, and the audit fields

Every reviewer emits JSON against `schemas/review-report.schema.json`. Prose is not an accepted output.

The dispatcher **overwrites the audit fields** — `schema_version`, `reviewer_id`, `lens`, `family`, `model`, `leg`, `artifact`, `references`, `artifact_revision` — with what it already knows, before validating, so a model cannot get the audit record wrong and validation bites only on the reviewer's actual work: verdict, summary, findings, method notes.

**Rejection is for shape and enum errors only.** String-length caps — `summary` at 600, `quote` and `citation.quote` at 300 — **truncate and flag**; they never reject. One run lost a whole model family's report to overruns of 96, 36 and 1 characters, and a validator that retires a reviewer over one character is a validator defect.

**Two rules bite only at ingest**, when the reviewer is still there to be asked for a repair: every `judgment-call` finding carries the `fork` tag, and none may carry `gap`. Reading a stored report back applies neither, so a report written before the rule still replays and a resume does not re-dispatch a seat over a rule its report predates.

## The digest cap

**Write full, return a digest.** Every reviewer's complete report lands on disk as JSON inside the project tree. The orchestrator receives **at most 2000 characters** back: reviewer id, model, verdict, counts by severity, cost, the `claim` line of the top three findings by severity, and the report path. Nothing else.

This is not only context hygiene. Harness subagents cannot write to the session scratchpad, and inter-agent messages truncate near 5500 characters. **Disk is the channel; the message is a pointer.** The orchestrator reads the file if it wants more.

## The harness leg

Reachable only with `--skip-claude` on a panel that has explicitly seated a `claude` family. It exists for the case where the operator wants a Claude opinion on the subscription rather than on the API. Its constraints, stated plainly:

- **Model is pinned at spawn.** The host passes an explicit model override, never `subagent_type: "fork"`, never the parent session's model. The manifest records the model the host says it spawned.
- **Effort is not guaranteed.** There is no per-spawn effort parameter, and these personas are deliberately never installed as harness agent files. The manifest records `effort: null` for harness seats, and a panel mixing legs is recorded as effort-heterogeneous.
- **Inputs are materialized, not live.** Harness seats are pointed at the pinned copies in `inputs/`, never at working-tree paths. Otherwise a mid-run edit gives two seats different documents and the reconciliation clusters quotes that never coexisted.
- **Writes are staged.** The seat writes into a seat-private staging directory **outside** the run directory; `render_harness_report.py` validates it by the same code path and moves it in. No seat is given a path into a directory holding a sibling's report.
- **Blinding is prompt-enforced, not sandboxed**, as above.

Each brief must carry the persona body verbatim plus the finding schema, the artifact and references by path with the instruction to read only those, the instruction to write its report to one named path and nothing else, the instruction to return a digest under 2000 characters, and the instruction not to look for or ask about any other reviewer's output.

## The connector seam

Every seat goes through a **connector**: a driver module loaded by its config entry's `type`. v1 ships one, `openai_compat`, pointed at OpenRouter, which serves every family — Anthropic included — through one OpenAI-compatible endpoint.

A `type` that names a `.py` file, or that resolves to one under `<workspace>/.agents/ensemble-review/backends/`, is loaded from that path. That is how a project binds a confidential review to a connector carrying a data agreement without editing the read-only package. `backends/base.py` states the contract a driver must satisfy; `scripts/backends/__init__.py` loads it.

The key is resolved by `dispatch.py` itself: `skills/lastpass/scripts/lp get global/OPENROUTER_API_KEY` first, then `$OPENROUTER_API_KEY` in the environment, then exit naming both paths it tried. A connector whose `api_key_secret` is null skips the vault leg. **No key value is written to any file in this repo**, which is public.

## Exit codes

`0` every expected seat validated · `1` usage or composition error · `2` terminal infrastructure failure — a provider auth failure, or zero reporting seats · `3` the run is under-seated · `4` an autonomous budget refusal.

`dispatch.py` has one more, **`5`, and it is internal**: it is how a child tells `run_panel.py` that the provider refused this model rather than that the reviewer wrote a bad report, which is what the re-seat-once path turns on. The parent consumes it and never re-emits it.

# Panel design — is this worth a panel, and which seats

A panel costs real money and real minutes. This file is the gate in front of that spend, and then the shape of the panel once the gate is passed.

## Is this worth a panel at all?

One reviewer has one shape of blind spot. A panel earns its cost when that blind spot is expensive and when nothing else in the process would catch it.

**Run one when two or more of these hold.**

- **The document is about to gate something.** A spec a builder will be held to, a plan somebody is about to execute, a research report a decision rests on. The test is not how important the subject is; it is whether somebody will act on this document without re-deriving it.
- **The cost of a missed defect is a rebuild.** A wrong interface in a spec costs a week when it is found in code and an hour when it is found in review. A wrong figure in a memo costs an apology.
- **It has been revised enough that nobody holds the whole thing in their head.** This is the condition the `consistency` lens exists for, and it is the one that most reliably produces defects a single careful reader misses: a document's third revision contradicts its first in a place neither author was looking.
- **The author is also the only available reviewer.** A review by the mind that wrote it is a spell-check. This is also why the default panel seats no Claude family: the host session that authors and reconciles is a Claude model, so a Claude reviewer is the seat most correlated with the artifact.

**Do not run one when any of these hold.**

- **Nobody will be held to it.** A note, a scratch plan, a document whose next reader is its author tomorrow. One reviewer, or none.
- **It is a code diff.** Out of lane. `/code-review` owns correctness and simplification on code; `templates/panels/code-review.json` ships as a named stub that says so and refuses.
- **You already know what is wrong with it.** A panel is for the defects you cannot see. Fix the ones you can first — a review whose findings you could have written yourself has spent four seats' money agreeing with you.
- **It is not finished enough to fault.** Reviewers rate by consequence, and a draft with three sections missing produces three seats' worth of "this section is missing". Outline-stage documents get a reader, not a panel.
- **There are no source-of-truth documents and the question is fidelity.** A `fidelity` or `source-credibility` seat with nothing to cite is refused before the first paid call. If the question is "does this match the PRD", supply the PRD.

**Scale the panel to the stakes.** Two seats, single-family, multi-lens is a quick check. Four seats across four families is the standard audit and is what every shipped live template composes. The ceiling — the full cross-product of families and lenses — is still available to an operator composing ad hoc, and was narrowed to four seats by default for budget reasons rather than because more stops helping.

## What a seat is

A seat is `{lens, family, tier}`.

The **lens** is a persona in `agents/` — a prompt, a rubric and an output schema, with no model baked in. The **family** is which model family runs it, a named family from the config's tier map or one of two constraints (`non-claude`, `distinct`). The **tier** is `frontier`, `standard` or `fast`, and it resolves to a concrete model id at dispatch time, so a panel definition never names a vendor.

**Differentiation is the whole product.** Seats carry different lenses by default, and `mode: identical` is not supported in v1: N samples of one brief is a majority vote on one question, which needs an answer field and a tie rule this skill does not have.

## Choosing the lenses

The catalog is nine lenses, and the boundary between overlapping pairs is the design decision rather than what each one asks.

| Lens | Asks | Needs references | Stops where |
| --- | --- | --- | --- |
| `fidelity` | Does the artifact faithfully implement the sources it is built from? | **Required** | Does not judge whether the design is good, buildable or self-consistent |
| `buildability` | Could a builder execute this without inventing a missing decision? | Helpful | Stops at the moment the thing is built |
| `consistency` | Do the parts agree with each other? | No | Judges the artifact against itself, never against its sources |
| `adversarial` | What is the strongest case that this is wrong — then, what is simply missing? | Helpful | Attacks the central bet; the systematic sweeps belong to `completeness` and `security` |
| `completeness` | What is absent, thin, or asserted without support? | Helpful | Sweeps the whole surface; `adversarial` sweeps only for the absence its attack needs |
| `source-credibility` | Do the sources deserve the weight the artifact puts on them? | **Required** | Judges the source; `fidelity` judges the reading of it |
| `security` | Who can abuse this? Assets, actors, trust boundaries, blast radius | No | Assumes the design is right; `adversarial` asks whether it is wrong |
| `alternatives` | What approach did this foreclose without arguing why? | No | Enumerates every fork; `adversarial` reaches for the one that wins |
| `second-order` | What does this commit us to, and what breaks downstream? | No | Starts where the build ends: month two, year two |

**Overlap is not duplication, and the lens bodies say so.** Every persona is told not to trim its findings to avoid a neighbour: two independent lenses landing on one defect is the corroboration signal the whole skill exists to collect, and it cannot be produced by holding back.

**Lens diversity is the part that is proven.** Three prototype rounds on 2026-06-26 against one spec: three identical reviewers on one model paid three times the tokens for about one and a half times the coverage; a four-lens pass on the same document found one blocker and two near-blockers the identical panel missed, and every unique high-severity catch came from exactly one lens.

## Choosing the families

**Model diversity is supported by two runs and proven by neither.** On 2026-09-18 a four-family frontier panel returned eight real findings from non-Claude seats that neither the Claude seat nor a repository-aware Claude checker raised, thirteen clusters spanning two or more families, and no non-Claude finding discarded outright. The same day the same four lenses at `standard` tier with no Claude seat returned two blockers the first panel's own reconciler had written into the document. That is the whole evidence base, and it stays provisional until a third run agrees.

Three rules follow from it.

**Distinct families, not distinct models.** Two models from one vendor share training data, tokenizer and house style, and their blind spots correlate. A panel of four models from one family is `majority` at best on the tier table, and the table says so on purpose.

**`min_families` is a target, not a precondition.** Default 2. A run that cannot meet it proceeds, lens-diverse only, and the reconciliation's method caveat says so. One reporting family reads as **"lens-diverse only"**: several lenses, one mind, and no cluster in that run can carry cross-family corroboration at any tier.

**No re-seat lands on `claude` unless the seat asked for it by name.** Claude is first in the config's declaration order, so without the rule every missing cell and every unreachable model would re-seat onto the one family the default panel is designed not to have.

## The four shipped templates

| Template | Seats | Needs references | Use it for |
| --- | --- | --- | --- |
| `spec-review` | fidelity, buildability, consistency, adversarial — openai, glm, kimi, xai | yes | The measured panel. A spec, plan or technical-requirements document judged against its sources |
| `research-report` | fidelity, source-credibility, completeness, adversarial — openai, kimi, glm, xai | yes | A report whose claims rest on cited sources. `verify_web` is off in v1, so the citing lenses judge the sources you supply rather than the live web |
| `design-decision` | adversarial, alternatives, second-order — xai, openai, glm | no | The artifact is the argument. This is also where a run with no references lands |
| `code-review` | none | — | Ships **deferred**: a named stub routing to `/code-review` |

A template is a starting point, not a wall: compose seats ad hoc, or drop an edited copy under `<workspace>/.agents/ensemble-review/panels/` and it replaces the shipped one whole.

## What it costs

Two things are measured and the rest is projected, so they are kept apart. The seed's estimate — 40–50k input plus "a few k output" — is wrong by 5–10x, because **reasoning tokens bill as output** and four of five frontier models cannot turn reasoning off.

**Measured**, from the two 2026-09-18 dogfood runs:

| Panel | Composition | Cost |
| --- | --- | --- |
| Four frontier seats **with** a Claude frontier seat | Fable 5.1, GPT-6 Astra, Kimi K3, GLM-5.3 | **$4.80** by manifest, $5.13 by dashboard once failed attempts counted. The Fable seat alone was $3.03. 698 s |
| Four standard seats, **no Claude** | the shipped `spec-review` panel at `standard` | **$1.47** against a ~$1.00 projection built on prompt size alone. 794 s |

**Projected by the shipped formula**, which is a different number from the one v2 printed: these are `lib/budget.py` run against `templates/models.json` and `templates/config.json` for a **60,000-token prompt**, the figure the stage 2a cost delta is quoted against, with the judgment call seated by the same `judge_lib.synthesis_seat()` the judge stage uses and priced on **its own** model. The two money columns are the two reconcilers — an interactive run whose host writes the judgment patch makes no synthesis call and is not charged for one.

| Panel | Tier | Seats | Host | Autonomous | Of which, the judge |
| --- | --- | --- | --- | --- | --- |
| `spec-review` | `frontier` | 4 | $3.09 | **$4.79** | $1.70 |
| `spec-review` | `standard` | 4 | $1.52 | $1.86 | $0.34 |
| `spec-review` | `fast` | 4 | $1.22 | $1.26 | $0.04 |
| `design-decision` | `frontier` | 3 | $2.34 | $4.04 | $1.70 |
| `design-decision` | `standard` | 3 | $0.77 | $1.11 | $0.34 |
| `design-decision` | `fast` | 3 | $0.47 | $0.51 | $0.04 |

`research-report` projects identically to `spec-review`: four seats on the same four families.

**The judgment call follows the run.** Its tier is the run's, not a fixed one — so `--tier fast` makes the judge cheap along with the reviewers, and the autonomous column falls with the tier as an operator reading it would expect. On every shipped panel the judge lands on `openai`, a family each of them already seats, so the judgment call runs on a model the run has already priced rather than in a corner of the tier map nothing else touches. `--synthesis-model` pins it outright, and a template may fix the judgment's own depth with a `synthesis: {"tier": …}` block when the judgment deserves more thought than the review did — that block is the one thing that outranks `--tier`.

**The frontier panel now projects at $4.79 against a $5.00 gate, not the ~$2.50 v2 quoted.** The difference is not the models; it is the two allowances the shipped formula carries and v2's figure did not — an output overrun multiplier for the routing premium a real call pays over the catalogue, and a repair allowance per seat. So an unattended frontier run on a document much longer than 60k tokens **refuses**, and that is the live budget question rather than a defect: the owner's signal of 2026-09-18 is that $5.00 per spec is already more than they want to spend, so the direction to push is a cheaper panel shape rather than a higher gate.

The standard panel's 47% overrun in 2026-09 is the case the pre-flight exists to catch and did not: one seat spent 47,253 reasoning tokens across two attempts and 68% of the run's dollars, and the first of those attempts was a truncation that produced no content and was then quoted back into a 90k-token repair prompt. Both causes are fixed — the projection is built on a per-model output-token prior rather than on prompt size, and a truncation is now retried once at double the cap with a fresh prompt. The same formula reprojects that run at $1.52 against its measured $1.47.

**The synthesis allowance's prompt rule is accurate, and was checked rather than assumed.** It prices the judgment call at 1.5x one seat's composed prompt. Measured with the real code path against the frozen `v2-spec` run — four reports, six references — the judge's actual prompt is **97,641 tokens against roughly 95,000 projected, about 1.03x**. The multiplier holds because a seat's prompt already carries the artifact and every reference, and the judge adds the reports on that same base rather than on nothing. Its worst case is a run with **no references**, where the shared base is small and the reports dominate: on the same run with the references stripped it is **1.63x**, 58,379 against 35,722. That is `design-decision`'s shape — the one shipped template that needs no references — so it is the row to watch rather than the four-seat default.

What was wrong until stage 2d's second fix round was not the prompt rule but the **model**: the allowance was priced on the dearest _seated_ model, and the judge does not run on a seated model unless the run's tier happens to equal the config's default. The table above is computed with the judge's own seat.

**`budget_usd` defaults to 5.00.** Over budget, an interactive run reports the projection and asks; an autonomous run **refuses before any paid call**, writes `budget-refusal.json` and exits 4. `--approve-budget` overrides it. The panel is never silently shrunk to fit: that changes the evidentiary value of the review without telling anyone.

**Read the projection, not just the total.** It breaks the number into catalogue prices, an output overrun allowance for the routing premium a real call pays over the catalogue, a repair allowance, and the synthesis call when the run will make one. Every figure is an estimate and is labelled one; prompt tokens are approximated at four characters per token, because this skill is standard-library only and six vendors mean six tokenizers.

**The projection is a dispatch gate only.** Nothing meters spend as seats return, and a panel that overruns its projection runs to completion. A seat re-seated onto another family mid-run makes a call the projection never priced. Both gaps are known and are why the re-seat is capped at once per seat.

## Where the bytes go

**Egress is a property of the connector a family is bound to, not of the artifact.** The default connector — `openai_compat` against OpenRouter — routes to third-party hosts, which is why GLM is cheaper there than at Z.ai's own API. A confidential artifact is handled by binding its families to connectors that carry data agreements — Azure OpenAI, Bedrock, a direct vendor endpoint — each of which is one more `backends/<type>.py` file plus a config fragment, **placed under `<project>/.agents/ensemble-review/` as a workspace override** so the shipped package stays read-only and the binding travels with the project that needs it. Personas and panel templates are unchanged either way, and the manifest records the connector and the upstream provider that served each seat, so the audit trail says where the content went.

There is no per-artifact gate and no prompt. A gate the operator answers the same way every time is not a control; the configuration is the control.

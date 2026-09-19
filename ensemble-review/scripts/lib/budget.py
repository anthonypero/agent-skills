"""The cost and token pre-flight: what this run is about to spend, before it spends any of it.

Every number here is an **estimate** and is labelled as one wherever it is printed or written. Three
of them are the reason the estimate exists at all:

- the **output-token prior** from the registry, measured on a past run of a different artifact;
- the **repair allowance**, because a seat that needs a second attempt pays for its whole prompt
  again — two of run 2's four seats did;
- the **synthesis allowance**, because the judgment call is a paid call too.

The projection is a **dispatch gate only**. Nothing meters spend as seats return, and a panel that
overruns its projection runs to completion; that gap is the spec's, and it is named in Open.

Prompt tokens are approximated at `CHARS_PER_TOKEN` characters per token rather than by a real
tokenizer: this skill is standard-library only, the panel spans six vendors with six tokenizers, and
a projection accurate to a few per cent against a budget stated in whole dollars does not earn a
dependency. The approximation is stated in the printed projection so nobody reads it as a count.
"""

import datetime
import json
import os

# One token per four characters. Crude, vendor-independent, and stated everywhere it is used.
CHARS_PER_TOKEN = 4

# Fixed scaffolding around the inlined documents: the delimiters, the instruction blocks, the
# "return only JSON" tail. Measured at roughly 1200 characters on the shipped prompt builder.
PROMPT_SCAFFOLDING_CHARS = 1200

# Half of one extra full call per seat. Run 2 needed a second attempt on two of four seats, and a
# repair re-ask re-sends the whole prompt, so the allowance is a fraction of the seat's own base
# cost rather than a flat figure.
REPAIR_ALLOWANCE_FRACTION = 0.5

# **The output overrun allowance.** The registry's prices are OpenRouter's *catalogue* prices, and
# the catalogue is not what a run pays: OpenRouter routes to third-party hosts, and the two frozen
# 2026-09-18 runs were served above the base rate on the output side by measurable margins —
# `moonshotai/kimi-k3` at 1.5e-05/token against a catalogue 1.095e-05 (1.37x) and
# `z-ai/glm-5.3-flash` at 5e-07 against 3e-07 (1.67x). Reasoning tokens bill as output and are the
# larger half of most seats' bills, so the gap lands almost entirely on this line. Seeded at 1.6 —
# between the two measured ratios, rounded up — which reproduces run 2's actual $1.47 from the
# catalogue's own prices; at 1.5 the same projection lands at $1.46 and under-reads the run it is
# calibrated against. Each model's measured rate is recorded in the registry as
# `measured_output_price`, informational, so the gap stays visible and this multiplier can be retired
# when per-seat routing is pinned.
OUTPUT_OVERRUN_ALLOWANCE = 1.6

# The judgment call: the synthesis persona sees every report, the provisional clusters and the
# artifact, so its prompt runs larger than a seat's, and its completion is a prior of its own. It is
# charged only when the run will actually dispatch it — an interactive run whose host writes the
# judgment patch never makes this call, and charging it there inflates the gate by about a quarter.
SYNTHESIS_PROMPT_MULTIPLIER = 1.5
SYNTHESIS_OUTPUT_PRIOR = 16000

ESTIMATE_NOTE = (
    "ESTIMATE — prompt tokens approximated at {0} chars/token; completion tokens from each model's "
    "output-token prior, measured on a different artifact; output priced at {1:.2f}x catalogue for "
    "routing overrun, plus a {2:.0%} repair allowance per seat.".format(
        CHARS_PER_TOKEN, OUTPUT_OVERRUN_ALLOWANCE, REPAIR_ALLOWANCE_FRACTION)
)


def approx_tokens(text_or_chars):
    """Prompt tokens, approximated. Accepts a string or a character count."""
    chars = text_or_chars if isinstance(text_or_chars, int) else len(text_or_chars or "")
    return int(chars / CHARS_PER_TOKEN) + 1


def prompt_chars(system_chars, document_chars):
    """Characters in one composed prompt: the system message, the inlined documents, the scaffolding."""
    return int(system_chars) + int(document_chars) + PROMPT_SCAFFOLDING_CHARS


class SeatProjection(dict):
    """One seat's row of the projection. A dict so it serializes straight into `budget-refusal.json`."""


def project_seat(reviewer_id, model, entry, prompt_tokens):
    """Project one seat. `entry` is its registry record; unknown prices project as None, never zero."""
    input_price = _f(entry.get("input_price_per_token"))
    output_price = _f(entry.get("output_price_per_token"))
    prior = entry.get("output_token_prior")
    prior = int(prior) if prior is not None else None

    base = None
    overrun = None
    if input_price is not None and output_price is not None and prior is not None:
        catalogue = input_price * prompt_tokens + output_price * prior
        overrun = output_price * prior * (OUTPUT_OVERRUN_ALLOWANCE - 1.0)
        base = catalogue + overrun
    repair = base * REPAIR_ALLOWANCE_FRACTION if base is not None else None

    return SeatProjection({
        "reviewer_id": reviewer_id,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "prior_tokens": prior,
        "prior_source": entry.get("prior_source"),
        "catalogue_usd": _round(base - overrun if base is not None else None),
        "overrun_allowance_usd": _round(overrun),
        "base_usd": _round(base),
        "repair_allowance_usd": _round(repair),
        "projected_usd": _round(base + repair if base is not None else None),
        "context_limit": entry.get("context_limit"),
        "context_overflow": _overflows(prompt_tokens, entry.get("context_limit")),
    })


def project_synthesis(seats, registry, prompt_tokens, model=None, seat=None):
    """The judgment call, priced on **the model that will actually make it**.

    `model` is the judge's own model and `seat` is the record that chose it — family, tier and the
    level of each order that decided them — both resolved by the caller through the same
    `judge_lib.synthesis_seat()` the judge stage uses. Pricing this on the dearest *seated* model
    used to under-read the call several times over, because the judge does not run on a seated
    model unless the seating happens to land there.

    The seat is carried into the detail rather than described here, because none of it is fixed:
    the judge's tier follows the run's, its family prefers one the panel seated, and either can be
    overridden by a template block or pinned by `--synthesis-model`. A rendering that asserted any
    particular one of those would be wrong on most runs.

    `model=None` falls back to the dearest seated model, which is a guess and is labelled one. It is
    reachable only from a caller that did not resolve the seat.

    Returns (usd, detail). `usd` is None when the model has no price, which is the same
    "unknown, not zero" rule the seats follow.
    """
    seat = seat or {}
    guessed = False
    entry = registry.get(model) if model else None
    if entry is None:
        guessed = True
        priced = []
        # Not `seat`: that name is the judge's own seat record, and rebinding it here would make the
        # detail below describe whichever reviewer happened to be last in the list.
        for reviewer in seats:
            candidate = registry.get(reviewer["model"]) or {}
            output_price = _f(candidate.get("output_price_per_token"))
            if output_price is not None:
                priced.append((output_price, reviewer["model"], candidate))
        if not priced:
            return None, {
                "model": model,
                "note": ("the judgment call's model carries no price" if model
                         else "no seated model carries a price"),
            }
        _price, model, entry = max(priced)

    input_price = _f(entry.get("input_price_per_token")) or 0.0
    output_price = _f(entry.get("output_price_per_token"))
    if output_price is None:
        return None, {"model": model, "note": "the judgment call's model carries no output price"}
    tokens = int(prompt_tokens * SYNTHESIS_PROMPT_MULTIPLIER)
    usd = input_price * tokens + output_price * SYNTHESIS_OUTPUT_PRIOR
    return _round(usd), {
        "model": model,
        "prompt_tokens": tokens,
        "prior_tokens": SYNTHESIS_OUTPUT_PRIOR,
        "resolved": not guessed,
        "family": seat.get("family"),
        "tier": seat.get("tier"),
        "tier_source": seat.get("tier_source"),
        "family_source": seat.get("family_source"),
        "note": ("the judgment call's own seat, resolved as the judge stage resolves it" if not guessed
                 else "GUESS: the judge's seat was not resolved, so this is the dearest seated model"),
    }


def describe_synthesis_seat(detail):
    """The one-line reading of where the judgment call sits, for the printed projection.

    Every part of it moves per run — the tier follows `--tier`, the family prefers one the panel
    seated, a template block or `--synthesis-model` overrides either — so the line states what was
    actually chosen and which level chose it, rather than asserting a rule. It used to read "at the
    config's default_tier", which stopped being true the moment the judge's tier began following
    the run's and was then false on every non-default tier.
    """
    if not detail.get("resolved"):
        return detail.get("note", "")
    bits = []
    if detail.get("tier"):
        bits.append("tier {0}{1}".format(
            detail["tier"], " ({0})".format(detail["tier_source"]) if detail.get("tier_source") else ""))
    if detail.get("family"):
        bits.append("family {0}{1}".format(
            detail["family"], " ({0})".format(detail["family_source"]) if detail.get("family_source") else ""))
    return ", ".join(bits) if bits else detail.get("note", "")


def project(seats, registry, budget_usd, now=None, with_synthesis=True, synthesis_model=None,
            synthesis_seat=None):
    """The whole run's projection. `seats` are dicts with reviewer_id, model and prompt_tokens.

    `with_synthesis` is False for a run whose judgment patch comes from the host in-session: that run
    makes no synthesis call, so charging one against its budget gate is charging for a call that will
    not happen. `synthesis_model` and `synthesis_seat` are the judge's own resolved model and the record that
    chose it — see `project_synthesis`.
    """
    rows = []
    for seat in seats:
        entry = registry.get(seat["model"]) or {}
        rows.append(project_seat(seat["reviewer_id"], seat["model"], entry, seat["prompt_tokens"]))

    dispatchable = [row for row in rows if not row["context_overflow"]]
    known = [row["projected_usd"] for row in dispatchable if row["projected_usd"] is not None]

    prompt_tokens = max([seat["prompt_tokens"] for seat in seats] or [0])
    if with_synthesis:
        synthesis_usd, synthesis_detail = project_synthesis(
            [s for s in seats if s["reviewer_id"] in {r["reviewer_id"] for r in dispatchable}],
            registry, prompt_tokens, model=synthesis_model, seat=synthesis_seat)
    else:
        synthesis_usd, synthesis_detail = None, {
            "model": None,
            "note": "this run's judgment patch comes from the host, so no synthesis call is made",
        }

    total = sum(known) + (synthesis_usd or 0.0)
    return {
        "generated_at": (now or datetime.datetime.now().astimezone().replace(microsecond=0)).isoformat(),
        "estimate": True,
        "estimate_note": ESTIMATE_NOTE,
        "budget_usd": _round(budget_usd),
        "projection_usd": _round(total),
        "shortfall_usd": _round(max(total - budget_usd, 0.0)),
        "per_seat": rows,
        "catalogue_usd": _round(sum(row["catalogue_usd"] for row in dispatchable if row["catalogue_usd"] is not None)),
        "overrun_allowance_usd": _round(sum(row["overrun_allowance_usd"] for row in dispatchable if row["overrun_allowance_usd"] is not None)),
        "repair_allowance_usd": _round(sum(row["repair_allowance_usd"] for row in dispatchable if row["repair_allowance_usd"] is not None)),
        "output_overrun_multiplier": OUTPUT_OVERRUN_ALLOWANCE,
        "synthesis_allowance_usd": synthesis_usd,
        "synthesis": synthesis_detail,
        "context_overflows": [row["reviewer_id"] for row in rows if row["context_overflow"]],
    }


def over_budget(projection):
    return (projection["projection_usd"] or 0.0) > (projection["budget_usd"] or 0.0)


def render(projection):
    """The projection as the operator reads it before dispatch. Always printed, always labelled."""
    lines = []
    lines.append("Cost pre-flight — {0}".format(ESTIMATE_NOTE))
    lines.append("")
    lines.append("  {0:<24} {1:<30} {2:>10} {3:>10} {4:>10}".format("seat", "model", "prompt", "prior", "projected"))
    for row in projection["per_seat"]:
        lines.append("  {0:<24} {1:<30} {2:>10} {3:>10} {4:>10}{5}".format(
            row["reviewer_id"][:24],
            (row["model"] or "?")[:30],
            row["prompt_tokens"],
            row["prior_tokens"] if row["prior_tokens"] is not None else "?",
            "${0:.4f}".format(row["projected_usd"]) if row["projected_usd"] is not None else "unpriced",
            "  CONTEXT OVERFLOW" if row["context_overflow"] else ""))
    lines.append("")
    lines.append("  of which:")
    lines.append("    {0:<36} ${1:>8.4f}".format("catalogue prices, all seats", projection["catalogue_usd"]))
    lines.append("    {0:<36} ${1:>8.4f}   measured routing premium over the catalogue".format(
        "output overrun allowance ({0:.2f}x)".format(projection["output_overrun_multiplier"]),
        projection["overrun_allowance_usd"]))
    lines.append("    {0:<36} ${1:>8.4f}   two of run 2's four seats needed a second attempt".format(
        "repair allowance ({0:.0%} of a seat)".format(REPAIR_ALLOWANCE_FRACTION),
        projection["repair_allowance_usd"]))
    if projection["synthesis_allowance_usd"] is not None:
        # Not truncated to the column width: which model runs the judge is the fact this line
        # exists to carry, and a model id cut mid-slug tells the operator nothing they can act on.
        lines.append("    {0:<36} ${1:>8.4f}   {2}".format(
            "synthesis call on {0}".format(projection["synthesis"].get("model") or "?"),
            projection["synthesis_allowance_usd"],
            describe_synthesis_seat(projection["synthesis"])))
    else:
        lines.append("    {0:<36} {1:>9}   {2}".format(
            "synthesis call", "not charged", projection["synthesis"].get("note", "")))
    lines.append("")
    lines.append("  projected ${0:.2f} against a budget of ${1:.2f}".format(
        projection["projection_usd"], projection["budget_usd"]))
    if projection["context_overflows"]:
        lines.append("  CONTEXT OVERFLOW: {0} — these seats are not dispatched and the run is under-seated".format(
            ", ".join(projection["context_overflows"])))
    return "\n".join(lines)


def refusal_document(projection):
    """`budget-refusal.json`, in the shape the spec gives, plus the workings behind the number."""
    return {
        "budget_usd": projection["budget_usd"],
        "projection_usd": projection["projection_usd"],
        "shortfall_usd": projection["shortfall_usd"],
        "per_seat": [
            {
                "reviewer_id": row["reviewer_id"],
                "model": row["model"],
                "projected_usd": row["projected_usd"],
                "prior_tokens": row["prior_tokens"],
            }
            for row in projection["per_seat"]
        ],
        "estimate": True,
        "estimate_note": projection["estimate_note"],
        "generated_at": projection["generated_at"],
        "catalogue_usd": projection["catalogue_usd"],
        "overrun_allowance_usd": projection["overrun_allowance_usd"],
        "output_overrun_multiplier": projection["output_overrun_multiplier"],
        "repair_allowance_usd": projection["repair_allowance_usd"],
        "synthesis_allowance_usd": projection["synthesis_allowance_usd"],
        "refusal": (
            "autonomous run: the projection exceeds the budget and there is nobody to ask, so no paid "
            "call was made. Raise --budget-usd, pass --approve-budget, or compose a cheaper panel."
        ),
    }


def _overflows(prompt_tokens, context_limit):
    try:
        limit = int(context_limit)
    except (TypeError, ValueError):
        return False
    return prompt_tokens > limit


def _f(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _round(value):
    return None if value is None else round(float(value), 6)


def file_chars(path):
    return os.path.getsize(path)


def dumps(document):
    return json.dumps(document, indent=2, ensure_ascii=False)

"""The Judge stage: who supplies the judgment patch, and what the `synthesis` persona is shown.

Everything here is pure — no network, no disk beyond reading what the caller hands it — so the
decisions can be tested without a call. `reconcile.py` owns the call itself, through `dispatch.py`'s
`prepare_call()` and `attempt_loop()`, and remains the only writer of both reconciliation files in
either mode.

**Who judges.** `reconciler` is `host`, `synthesis` or `default`, and `default` means host when a
human is attached and `synthesis` when nobody is. "Nobody is attached" is the same test the budget
gate already turns on — `--autonomous`, or stdin is not a tty — and it is here rather than in
`run_panel.py` so the panel and the reconciler cannot disagree about whether this run has a human.

**What `synthesis` is shown.** Steps 4 through 7 arbitrate severity, adjudicate false positives and
check that quoted text is real, and none of that is possible against the reports alone. So the user
message carries every validated report in full, the provisional clusters `reconcile.py` computed
with their `P-n` ids, the references, and the artifact **at its pinned revision** — the bytes in
`inputs/`, not the working tree.

**What it may not do.** The `rulings` array is the host's alone. An unattended persona stating a
ruling on a design fork is a human decision recorded as settled by nobody, so the persona is told
not to emit one and `reconcile_core.validate_patch_shape` rejects a patch from `synthesis` that
does. The same asymmetry governs dispositions: an all-`judgment-call` cluster is always
`flag-for-human` from this author.
"""

import json
import sys

RECONCILERS = ("host", "synthesis", "default")

SYNTHESIS_PERSONA = "synthesis"
SYNTHESIS_AUTHOR = "synthesis"

# How much of one report is inlined. A report is the reviewer's whole argument and the persona is
# arbitrating it, so the cap is high enough never to bite on a real report and low enough that one
# pathological seat cannot push the prompt past every model's context limit on its own.
REPORT_CHAR_CAP = 120000


class JudgeError(Exception):
    """The judgment call cannot be composed or seated. The caller turns this into exit 1."""


# --- who judges -----------------------------------------------------------------------------------

def is_autonomous(flag=False, stream=None):
    """`--autonomous`, or stdin is not a tty — the same test the budget gate turns on.

    A run nobody is watching must not block on a prompt and must not leave the judgment unwritten:
    it refuses over budget rather than asking, and it takes its judgment from `synthesis` rather
    than from a host that is not there.
    """
    if flag:
        return True
    stream = stream if stream is not None else sys.stdin
    try:
        return not stream.isatty()
    except (AttributeError, ValueError):
        return True


def resolve_reconciler(requested=None, declared=None, autonomous=False):
    """Who supplies the judgment patch, by the spec's order: CLI, then the run's, then `default`.

    `declared` is what the panel template said and the manifest recorded. `default` resolves here
    and nowhere else: host when a human is attached, `synthesis` when nobody is.
    """
    for value in (requested, declared):
        if value in ("host", "synthesis"):
            return value
        if value is not None and value not in RECONCILERS:
            raise JudgeError(
                "unknown reconciler {0!r}; it is one of {1}".format(value, ", ".join(RECONCILERS)))
    return SYNTHESIS_AUTHOR if autonomous else "host"


# --- where the judgment call is seated ---------------------------------------------------------

DEFAULT_TIER = "frontier"

# What decided the judge's tier and family, recorded beside them the way a seat records
# `tier_source`. Exhaustive, and asserted so by `synthesis_seat` before it returns: a source
# string the manifest carries and this tuple does not name is a value a reader cannot look up.
# `"override"` is the caller passing `tier=` or `family=` outright, which only a test does today.
TIER_SOURCES = ("synthesis", "--tier", "panel", "config", "persona", "default", "override")
FAMILY_SOURCES = ("synthesis", "panel", "config", "override", "--synthesis-model")


def synthesis_seat(config_entry, panel=None, cli_tier=None, seated_families=None,
                   persona_tier=None, tier=None, family=None):
    """Where the judgment call sits: `{family, tier, family_source, tier_source}`.

    **The tier follows the run, not the config alone.** It used to be the config's `default_tier`
    unconditionally, which meant a panel dispatched at `--tier fast` had a frontier judge costing
    more than its four reviewers put together — the run got cheaper and the judgment did not. The
    order now mirrors the one seats resolve by, first match wins:

    1. the template's `synthesis.tier` — an explicit judge tier is a deliberate choice about the
       judgment specifically, so it outranks a blanket `--tier` that was aimed at the reviewers;
    2. the run's `--tier`;
    3. the panel's `tier`;
    4. the config's `default_tier`;
    5. the `synthesis` persona's frontmatter, when it names a tier this config offers.

    **The family prefers a family the panel already seated.** The template's `synthesis.family`
    first; then the first non-`claude` family **among the panel's own seated families**, walking
    the resolved tier's cells in the order the config declares them, that has a model there;
    then, only if none of those qualify, the first non-`claude` family in that same cell order.
    ("Declaration order" is the key order of the tier's own map, which is what `families_at`
    reads for a seat — one order, used by both.) Preferring a seated family keeps the
    judge on a model the run has already priced and the operator has already chosen, instead of
    reaching into a corner of the tier map that nothing else in this run touches.

    `claude` is excluded from both fallbacks, the same exclusion every re-seat draws on (Decisions,
    stage 2b) and for the same reason: the host session that would otherwise judge is itself a
    Claude model, so a Claude judge is the mind most correlated with the artifact. Naming it
    explicitly in a template still works.

    `tier` and `family` are hard overrides. `--synthesis-model` pins the model outright and the
    caller records `family_source: "--synthesis-model"`.
    """
    block = (panel or {}).get("synthesis")
    block = block if isinstance(block, dict) else {}
    offered = config_entry.get("tiers") or {}

    if tier:
        tier_source = "override"
    else:
        levels = (
            ("synthesis", block.get("tier")),
            ("--tier", cli_tier),
            ("panel", (panel or {}).get("tier")),
            ("config", config_entry.get("default_tier")),
            ("persona", persona_tier if persona_tier in offered else None),
        )
        tier, tier_source = DEFAULT_TIER, "default"
        for name, value in levels:
            if value:
                tier, tier_source = value, name
                break

    cells = offered.get(tier) or {}
    if not cells:
        raise JudgeError(
            "the judgment call resolves to tier {0!r} (from {1}), which the config does not offer. "
            "It offers: {2}".format(tier, tier_source, ", ".join(sorted(offered)) or "no tiers at all"))

    if family or block.get("family"):
        family_source = "override" if family else "synthesis"
        family = family or block.get("family")
        if not cells.get(family):
            raise JudgeError(
                "the judgment call asks for family {0!r} at tier {1!r}, which has no model there. "
                "That tier offers: {2}".format(family, tier, ", ".join(sorted(cells))))
        return _seat(family, tier, family_source, tier_source)

    # Declaration order is the config's, filtered to what the panel actually seated.
    seated = set(seated_families or [])
    for candidate, model in cells.items():
        if model and candidate != "claude" and candidate in seated:
            return _seat(candidate, tier, "panel", tier_source)

    for candidate, model in cells.items():
        if model and candidate != "claude":
            return _seat(candidate, tier, "config", tier_source)

    raise JudgeError(
        "tier {0!r} offers no non-`claude` family to seat the judgment call on. A Claude judge is "
        "the mind most correlated with a Claude-authored artifact, which is why the re-seat rule "
        "excludes it; name a family explicitly if that is really what you want.".format(tier))


def _seat(family, tier, family_source, tier_source):
    """One seat record, with both source strings checked against the vocabularies above.

    A source the tuples do not name would reach the manifest as a word with nothing to look it up
    against, which is the same defect as an undocumented enum. Cheap to assert, and it fails in a
    test rather than in a run.
    """
    assert tier_source in TIER_SOURCES, tier_source
    assert family_source in FAMILY_SOURCES, family_source
    return {"family": family, "tier": tier, "family_source": family_source, "tier_source": tier_source}


# --- what the persona is shown -------------------------------------------------------------------

def build_user_message(run_id, request, reports, artifact_text, artifact_label, artifact_revision,
                       references=None):
    """The synthesis persona's user message: references, artifact, reports, provisional clusters.

    Order is deliberate. The references come first because they are what a severity argued with a
    citation is argued against; the artifact next, because steps 4 to 7 check quoted text against
    it; the reports next, in full, because arbitration is over their reasoning and not over their
    headline severities; and the provisional clusters last, because they are the question.
    """
    blocks = []
    blocks.append(
        "You are supplying the judgment patch for run `{0}`. Everything you may rely on is in this "
        "message: you have no tools and no file access. Return ONE JSON object — the judgment patch "
        "— and nothing else.".format(run_id))
    blocks.append("")

    references = list(references or [])
    if references:
        blocks.append("There are {0} source-of-truth reference document(s) the panel judged the "
                      "artifact against.".format(len(references)))
        blocks.append("")
        for index, (label, text) in enumerate(references, start=1):
            blocks.append("===== BEGIN REFERENCE {0} of {1}: {2} =====".format(index, len(references), label))
            blocks.append((text or "").rstrip("\n"))
            blocks.append("===== END REFERENCE {0}: {1} =====".format(index, label))
            blocks.append("")
    else:
        blocks.append("No reference documents were supplied to this panel. Say so in `method_caveat`.")
        blocks.append("")

    blocks.append("===== BEGIN ARTIFACT UNDER REVIEW: {0} (revision {1}) =====".format(
        artifact_label, artifact_revision or "unpinned"))
    blocks.append((artifact_text or "").rstrip("\n"))
    blocks.append("===== END ARTIFACT: {0} =====".format(artifact_label))
    blocks.append("")

    blocks.append("These are the {0} validated report(s). Every `quote` and every "
                  "`literal_edit.old_text` below has already been checked against the artifact "
                  "bytes above; anything that failed that check is listed in `anchor_drops` and is "
                  "already out of the clusters.".format(len(reports)))
    blocks.append("")
    for reviewer_id in sorted(reports):
        blocks.append("===== BEGIN REPORT: {0} =====".format(reviewer_id))
        blocks.append(_dump(reports[reviewer_id])[:REPORT_CHAR_CAP])
        blocks.append("===== END REPORT: {0} =====".format(reviewer_id))
        blocks.append("")

    blocks.append("===== BEGIN PROVISIONAL CLUSTERS =====")
    blocks.append(_dump(request))
    blocks.append("===== END PROVISIONAL CLUSTERS =====")
    blocks.append("")
    blocks.append(
        "Answer every provisional cluster. `run_id` is `{0}`, `author` is `synthesis`. Cluster "
        "references are the `P-n` ids above, or `P-n.k` for a product of one of your own splits. "
        "Do not emit `rulings`.".format(run_id))
    blocks.append("Return ONLY the JSON object. No prose before or after it. No code fence.")
    return "\n".join(blocks)


def build_repair_prompt(original_user_prompt, raw_output, errors):
    """The one repair re-ask, naming the failing entries. The only prompt that quotes back."""
    return "\n".join([
        original_user_prompt,
        "",
        "===== YOUR PREVIOUS RESPONSE =====",
        (raw_output or "")[:200000],
        "===== END PREVIOUS RESPONSE =====",
        "",
        "That patch was rejected. Each line names the entry that failed and why:",
        "",
        "\n".join("- " + error for error in errors),
        "",
        "Re-emit the WHOLE patch as one corrected JSON object. Keep every judgment you already made "
        "and its reasoning; fix only what is named above. Remember that a cluster whose findings are "
        "all `judgment-call` is always `flag-for-human` from this author, that every post-merge "
        "`singleton` and `corroborated-same-family` cluster needs a label and a reason, that every "
        "cluster needs a disposition, and that you may not emit `rulings`. "
        "Return ONLY the JSON object — no prose, no code fence.",
    ])


# --- the manifest's record of the call ------------------------------------------------------------

def judge_record(reviewer_id, family, tier, model, connector, provider, effort, cap, attempts,
                 elapsed_s, status, errors=None, tier_source=None, family_source=None):
    """The seat-shaped record of the judgment call, for `manifest.judge`.

    **It is not in `manifest.seats`, and that is load-bearing.** `seats` is the `unanimous`
    denominator and `reconcile_core.load_reports` reports every seat in it with no report file as a
    **missing seat** — so a synthesis entry there would make every autonomous run reconcile as
    under-seated, name the judge as a reviewer that failed to report, and raise the bar for the one
    tier that counts expected seats. The record carries the same fields a seat record does, in the
    same names, so a reader gets the same accounting from a different key.
    """
    costs = [a.get("cost_usd") for a in attempts if a.get("cost_usd") is not None]
    return {
        "reviewer_id": reviewer_id,
        "role": "judge",
        "family": family,
        "tier": tier,
        # Which level of each order decided this seat, the way a reviewer seat records
        # `tier_source`. A judge on a family nobody expected is a question a reader should be able
        # to answer from the manifest rather than by re-deriving the config.
        "tier_source": tier_source,
        "family_source": family_source,
        "model": model,
        "connector": connector,
        "provider": provider,
        "leg": "openrouter",
        "input_delivery": "inlined",
        "effort": effort,
        "max_tokens": cap,
        "status": status,
        "errors": list(errors or []) or None,
        "attempts": attempts,
        "attempts_count": len(attempts),
        "usage": attempts[-1].get("usage") if attempts else None,
        "reasoning_tokens": sum(int(a.get("reasoning_tokens") or 0) for a in attempts) or None,
        "cost_usd": sum(costs) if costs else None,
        "elapsed_s": round(elapsed_s, 1) if elapsed_s is not None else None,
    }


def _dump(document):
    return json.dumps(document, indent=2, ensure_ascii=False)

"""The Judge stage: who supplies the judgment patch, and what the `synthesis` persona is shown.

Everything here is pure — no network, no disk beyond reading what the caller hands it — so the
decisions can be tested without a call. `reconcile.py` owns the call itself, through `dispatch.py`'s
`prepare_call()` and `attempt_loop()`, and remains the only writer of both reconciliation files in
either mode.

**Who judges.** `reconciler` is `host`, `synthesis`, `harness-judge` or `default`, and `default`
means host when a human is attached and, when nobody is, the **harness judge** where one is
installed and the `synthesis` persona where none is. "Nobody is attached" is the same test the
budget gate already turns on — `--autonomous`, or stdin is not a tty — and it is here rather than in
`run_panel.py` so the panel and the reconciler cannot disagree about whether this run has a human.

**The harness judge** is `agents/judge.md`, installed by `install.sh` into the harness agents
directory as `panel-judge`, pinned to frontier Claude at `high` effort. Owner ruling, 2026-09-19:
the judgment call lands on a family no seat holds, which is the same-family correlation two
adversarial seats found, and it costs the run nothing because it runs on the subscription. A script
cannot spawn a harness agent, so `run_panel.py` prints the spawn instruction and stops at the judge
stage; `reconcile.py` then ingests the patch the agent wrote, exactly as it ingests one a host
wrote, and holds it to the same floor it holds `synthesis` to.

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
import os
import sys

from . import registry as registry_lib
from . import report as report_lib

RECONCILERS = ("host", "synthesis", "harness-judge", "default")

SYNTHESIS_PERSONA = "synthesis"
SYNTHESIS_AUTHOR = "synthesis"

# The harness judge: the shipped agent definition, the name it installs under, and the `author`
# string its patch carries. The author is not a label — `reconcile_core` keys the mechanical floor
# on it — so it is named once here and read everywhere else.
HARNESS_JUDGE_AGENT_FILE = "judge.md"
HARNESS_JUDGE_AGENT = "panel-judge"
HARNESS_JUDGE_AUTHOR = "harness-judge"

# Every author a judgment patch may claim, and the two that are held to the mechanical floor: no
# `rulings`, and an all-`judgment-call` cluster is always `flag-for-human`. `host` is the one author
# with an owner to answer to, which is the whole of the asymmetry.
AUTHORS = ("host", SYNTHESIS_AUTHOR, HARNESS_JUDGE_AUTHOR)
UNATTENDED_AUTHORS = (SYNTHESIS_AUTHOR, HARNESS_JUDGE_AUTHOR)

# Where a harness keeps its agent definitions. The environment variable exists so the tests can
# answer "is a harness present" from a directory they control rather than from the machine the
# suite happens to run on — a test whose result depends on whether the developer has run
# `install.sh` is not a test.
HARNESS_AGENTS_DIR_ENV = "PANEL_REVIEW_HARNESS_AGENTS_DIR"
DEFAULT_HARNESS_AGENTS_DIR = os.path.join("~", ".claude", "agents")

# How much of one report is inlined. A report is the reviewer's whole argument and the persona is
# arbitrating it, so the cap is high enough never to bite on a real report and low enough that one
# pathological seat cannot push the prompt past every model's context limit on its own.
REPORT_CHAR_CAP = 120000

# How a capped report is cut: head and tail with the middle elided, the same shape
# `report.quote_for_repair` gives the repair quote. A bare head slice drops the method notes and the
# last findings without saying so, and the judge cannot arbitrate what it cannot see it is missing.
REPORT_HEAD_FRACTION = 0.7


class JudgeError(Exception):
    """The judgment call cannot be composed or seated. The caller turns this into exit 1."""


class JudgePromptTooLong(JudgeError):
    """The composed judgment prompt does not fit the judge model's context window.

    A judge-stage failure, not a seat failure: nothing is written, the reason is recorded on
    `manifest.judge`, and the caller exits 3. The per-report cap bounds one seat's share of the
    prompt and not the composed whole — four capped reports plus the artifact, every reference and
    the provisional clusters can still exceed the window, and until this existed nothing checked.
    """


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


def harness_agents_dir():
    """The directory a harness keeps its agent definitions in, expanded."""
    return os.path.expanduser(os.environ.get(HARNESS_AGENTS_DIR_ENV) or DEFAULT_HARNESS_AGENTS_DIR)


def harness_judge_path(agents_dir=None):
    """Where the installed harness judge lives, whether or not it is there."""
    return os.path.join(agents_dir or harness_agents_dir(), HARNESS_JUDGE_AGENT + ".md")


def harness_present(agents_dir=None):
    """Whether this machine has the harness judge installed.

    The question "is a harness present" has no general answer from inside a Python process, and
    guessing at one from environment variables a host may or may not set would make the default
    reconciler depend on how the script was launched. What is checkable is narrower and is the
    thing that actually matters: whether the agent the judge stage would ask for exists, which
    `install.sh` is what puts there.
    """
    return os.path.isfile(harness_judge_path(agents_dir))


def resolve_reconciler(requested=None, declared=None, autonomous=False, harness=False):
    """Who supplies the judgment patch, by the spec's order: CLI, then the run's, then `default`.

    `declared` is what the panel template said and the manifest recorded. `default` resolves here
    and nowhere else: host when a human is attached, and when nobody is, the harness judge where
    one is installed and the `synthesis` persona where none is (owner ruling, 2026-09-19).

    `harness` is passed by the caller rather than probed here, so that a decision about the machine
    is made once, at Resolve, and recorded in the manifest — `reconcile.py` reading the manifest
    back must not re-derive it from a home directory that may since have changed.
    """
    for value in (requested, declared):
        if value in AUTHORS:
            return value
        if value is not None and value not in RECONCILERS:
            raise JudgeError(
                "unknown reconciler {0!r}; it is one of {1}".format(value, ", ".join(RECONCILERS)))
    if not autonomous:
        return "host"
    return HARNESS_JUDGE_AUTHOR if harness else SYNTHESIS_AUTHOR


# --- where the judgment call is seated ---------------------------------------------------------

DEFAULT_TIER = "frontier"

# What decided the judge's tier and family, recorded beside them the way a seat records
# `tier_source`. Exhaustive, and asserted so by `synthesis_seat` before it returns: a source
# string the manifest carries and this tuple does not name is a value a reader cannot look up.
# `"override"` is the caller passing `tier=` or `family=` outright, which only a test does today.
TIER_SOURCES = ("synthesis", "--tier", "panel", "config", "persona", "default", "override")
FAMILY_SOURCES = ("synthesis", "panel", "config", "override", "--synthesis-model")

# What decided the fallback judge's abstract effort level. The harness judge has no entry here
# because it has no such resolution: its effort is a line in its own installed agent file.
# `synthesis` is the panel template's own `synthesis` block, the same name `TIER_SOURCES` gives it.
EFFORT_SOURCES = ("synthesis", "run", "config", "persona", "default")


def synthesis_effort(manifest, config_entry, frontmatter=None, panel=None):
    """`(level, source)` for the fallback judgment call, resolved the way a seat's effort is.

    The judge is a seat for this purpose and for the same reason it is seated like one: a run
    dispatched shallow should not pay for a judgment nobody asked to be deep. The order is the
    seat's with its top two rungs replaced, because `reconcile.py` has no `--effort` of its own and
    a per-seat knob is not about the judge:

    1. **the panel template's `synthesis.effort`**, which is where a template that already pins the
       judgment's family and tier says how deep it should think. It is first for the same reason
       `synthesis.tier` beats the run's tier: a template naming it is naming it *about the judge*,
       and every level under this one is about the panel;
    2. **the run's own level**, read back from the manifest — the level its seats ran at when they
       agreed on one. A panel whose seats ran at different levels gives no answer here and falls
       through, rather than picking one seat's level to arbitrate the others by;
    3. the config's `default_effort`;
    4. the `synthesis` persona's frontmatter;
    5. `standard`.

    **The harness judge is untouched by all of it.** Its model and its effort are pinned in its
    installed agent file, `manifest.judge_seat` is null on such a run, and nothing here is reached.
    """
    levels = {seat.get("effort_level") for seat in (manifest or {}).get("seats") or []
              if seat.get("effort_level")}
    run_level = levels.pop() if len(levels) == 1 else None
    block = (panel or {}).get("synthesis")
    pinned = block.get("effort") if isinstance(block, dict) else None
    for name, value in (("synthesis", pinned),
                        ("run", run_level),
                        ("config", (config_entry or {}).get("default_effort")),
                        ("persona", (frontmatter or {}).get("effort"))):
        if value:
            return value, name
    return registry_lib.DEFAULT_EFFORT_LEVEL, "default"


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

def elide_report(text, limit=None):
    """One report cut to the cap, head and tail, with the middle marked. Returns (text, record).

    `limit` defaults to `REPORT_CHAR_CAP` and is read at call time rather than bound at import, so
    the cap is one constant a test can move.

    `record` is None when nothing was cut. It used to be a bare head slice with no marker at all,
    which is the one thing a cap must not be: the judge would arbitrate a report whose last
    findings and method notes had silently gone, with nothing in the prompt or the manifest saying
    so. The repair quote at 60,000 characters has elided the middle and noted it since stage 2b;
    this is the same behaviour at the judge's own cap.
    """
    limit = REPORT_CHAR_CAP if limit is None else limit
    text = text or ""
    if len(text) <= limit:
        return text, None
    head = int(limit * REPORT_HEAD_FRACTION)
    tail = limit - head
    dropped = len(text) - limit
    marker = "\n…[{0} characters elided from the middle of this report]…\n".format(dropped)
    record = {"chars": len(text), "cap": limit, "elided_chars": dropped,
              "head_chars": head, "tail_chars": tail}
    return text[:head] + marker + text[-tail:], record


def build_user_message(run_id, request, reports, artifact_text, artifact_label, artifact_revision,
                       references=None, elisions=None):
    """The synthesis persona's user message: references, artifact, reports, provisional clusters.

    Order is deliberate. The references come first because they are what a severity argued with a
    citation is argued against; the artifact next, because steps 4 to 7 check quoted text against
    it; the reports next, in full, because arbitration is over their reasoning and not over their
    headline severities; and the provisional clusters last, because they are the question.

    `elisions` is an optional list the caller owns, the way `attempt_loop` takes `attempts`: any
    report the per-report cap cut appends a record to it, and `reconcile.py` puts the list on
    `manifest.judge`, so a reader can see that the judge did not read one seat's whole argument.
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
        body, elision = elide_report(_dump(reports[reviewer_id]))
        if elision is not None and elisions is not None:
            elisions.append(dict(elision, reviewer_id=reviewer_id))
        blocks.append("===== BEGIN REPORT: {0} =====".format(reviewer_id))
        blocks.append(body)
        if elision is not None:
            blocks.append("(This report was {0} characters and is capped at {1}; {2} were elided "
                          "from the middle, as marked above.)".format(
                              elision["chars"], elision["cap"], elision["elided_chars"]))
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
    """The one repair re-ask, naming the failing entries. The only prompt that quotes back.

    The quote is capped by `report.quote_for_repair`, the same ceiling the seats' repair prompt uses.
    It matters more here: the judgment call's own prompt already runs to six figures of tokens — the
    first unattended run's was 127,624 — so a re-ask that also quoted 200,000 characters of rejected
    patch would be the dearest call in the run by a distance.
    """
    quoted, _note = report_lib.quote_for_repair(raw_output)
    return "\n".join([
        original_user_prompt,
        "",
        "===== YOUR PREVIOUS RESPONSE =====",
        quoted,
        "===== END PREVIOUS RESPONSE =====",
        "",
        "That patch was rejected. Each line names the entry that failed and why:",
        "",
        "\n".join("- " + error for error in errors),
        "",
        "Re-emit the WHOLE patch as one corrected JSON object. Keep every judgment you already made "
        "and its reasoning; fix only what is named above. Remember that a cluster whose findings are "
        "all `judgment-call` is always `flag-for-human` from this author, that every post-merge "
        "cluster whose members are all one family — `singleton`, `same-family` and "
        "`corroborated-same-family` — needs a label and a reason, that every "
        "cluster needs a disposition, and that you may not emit `rulings`. "
        "Return ONLY the JSON object — no prose, no code fence.",
    ])


# --- does the composed prompt fit? -----------------------------------------------------------------

def check_prompt_fits(system_prompt, user_prompt, context_limit, model, approx_tokens):
    """Raise `JudgePromptTooLong` when the composed judgment prompt exceeds the model's window.

    The seat pre-flight measures every seat's prompt against its model's `context_limit` and the
    judgment call was never in that check (run 5, SF-11). The per-report cap is not the same thing:
    it bounds one seat's share, and four capped reports plus the artifact, every reference and the
    provisional clusters can still overflow together. Measured here, where the prompt actually
    exists, rather than estimated from the seats' sizes.

    `approx_tokens` is passed in — `lib/budget.py`'s four-characters-per-token approximation — so
    this module stays free of the budget import and the projection and the check agree by
    construction. An entry with no `context_limit` means the registry has not learned one, which is
    not the same as a limit of zero: unknown never refuses, and the size is still measured and
    returned, because `manifest.judge.prompt_tokens` is worth recording either way.
    """
    tokens = approx_tokens(len(system_prompt or "") + len(user_prompt or ""))
    try:
        limit = int(context_limit)
    except (TypeError, ValueError):
        return tokens
    if limit <= 0 or tokens <= limit:
        return tokens
    raise JudgePromptTooLong(
        "the composed judgment prompt is about {0:,} tokens against {1}'s context limit of "
        "{2:,}.\n"
        "  The per-report cap bounds one seat's share of the prompt and not the whole of it: the "
        "artifact,\n"
        "  every reference and the provisional clusters are in there too. Nothing was written and "
        "no paid\n"
        "  call was made. Seat the judge on a model with more room (--synthesis-model), or "
        "reconcile\n"
        "  interactively with --reconciler host.".format(tokens, model, limit))


# --- the harness judge -------------------------------------------------------------------------

def spawn_instruction(run_dir, staging_path, skill_dir, agent=HARNESS_JUDGE_AGENT,
                      request_path=None):
    """The exact instruction the orchestrating session needs to spawn the judge. Returns lines.

    A script cannot spawn a harness agent, so this is where the judge stage stops on a run whose
    reconciler is the harness judge. Everything the agent is told to read is named absolutely,
    because the agent's own body says to read those paths and nothing else, and a relative path
    resolved against a session's working directory is not the same promise.

    **`request_path` is the worksheet, and it has to exist before this prints.** The provisional
    clusters are what the agent answers: `reconcile.py`'s first pass computes them and writes
    `judgment-request.json`, and the agent is told to stop rather than invent them if it is not
    there. An instruction printed over a run directory that has no worksheet in it spawns an agent
    that halts, which is the one failure this stage cannot report on its own — so both callers
    print this only after that pass has run.
    """
    request_path = request_path or os.path.join(run_dir, "judgment-request.json")
    return [
        "Judge stage: this run's judgment comes from the harness judge, which a script cannot spawn.",
        "The provisional clusters are computed and written; what is left is the judgment over them.",
        "",
        "  Spawn one subagent, and only one:",
        "    agent:        {0}   (installed by install.sh; pinned to frontier Claude at high effort)".format(agent),
        "    run dir:      {0}".format(run_dir),
        "    package:      {0}".format(skill_dir),
        "    worksheet:    {0}".format(request_path),
        "    write to:     {0}".format(staging_path),
        "",
        "  Its brief is the paths above and nothing else: the agent definition already carries",
        "  what to read, in what order, and what it may not do. It reads the worksheet's `P-n`",
        "  clusters and every validated report, writes the judgment patch to the staging path —",
        "  outside the run directory, private to it — and returns a digest.",
        "",
        "  Then ingest the patch, which is where the reconciliation is written:",
    ]


def harness_judge_record(status="ok", staging_path=None, judgment_path=None, errors=None,
                         agent=HARNESS_JUDGE_AGENT, elisions=None):
    """The seat-shaped record of a harness judgment, for `manifest.judge`.

    The same shape `judge_record` writes for a paid call, with the money fields null because there
    is no bill: the harness judge runs on the subscription. `cost_usd: null` is "no charge", which
    is what `cost_usd_total` should read it as, rather than a zero that would look like a call that
    was made and priced at nothing.
    """
    return {
        "reviewer_id": agent,
        "role": "judge",
        "family": "claude",
        "tier": None,
        "tier_source": None,
        "family_source": "harness-judge",
        "model": None,
        "connector": None,
        "provider": "harness",
        "leg": "harness",
        "input_delivery": "materialized-paths",
        # The harness judge's effort is a line in its own installed agent file — there is no
        # per-spawn effort parameter — so the run neither chose a level nor sent one.
        "effort_level": None,
        "effort_source": "harness-judge-agent-file",
        "effort": None,
        "effort_tokens": None,
        # Nothing was billed, so no spend gate stood in front of this and nothing answered one.
        "spend_approval_source": None,
        "max_tokens": None,
        "status": status,
        "errors": list(errors or []) or None,
        "attempts": None,
        "attempts_count": None,
        "usage": None,
        "reasoning_tokens": None,
        "cost_usd": None,
        "cost_sources": [],
        "cost_estimated": False,
        "upstream_unbilled_usd": None,
        "elapsed_s": None,
        "staging_path": staging_path,
        "judgment_path": judgment_path,
        "report_elisions": list(elisions or []) or None,
    }


# --- the manifest's record of the call ------------------------------------------------------------

def judge_record(reviewer_id, family, tier, model, connector, provider, effort, cap, attempts,
                 elapsed_s, status, errors=None, tier_source=None, family_source=None,
                 elisions=None, prompt_tokens=None, effort_level=None, effort_source=None,
                 effort_tokens=None, spend_approval_source=None):
    """The seat-shaped record of the judgment call, for `manifest.judge`.

    **It is not in `manifest.seats`, and that is load-bearing.** `seats` is the `unanimous`
    denominator and `reconcile_core.load_reports` reports every seat in it with no report file as a
    **missing seat** — so a synthesis entry there would make every autonomous run reconcile as
    under-seated, name the judge as a reviewer that failed to report, and raise the bar for the one
    tier that counts expected seats. The record carries the same fields a seat record does, in the
    same names, so a reader gets the same accounting from a different key.
    """
    costs = [a.get("cost_usd") for a in attempts if a.get("cost_usd") is not None]
    # The same two accounting fields a seat record carries, derived the same way `dispatch.build_meta`
    # derives them. They were missing here, which made `reconcile_core._upstream_unbilled_total` sum
    # a key the producer never emitted: the judge's unbilled inference would silently have read as
    # zero, and `cost_sources` had no answer at all for the one call the panel makes outside `seats`.
    cost_sources = sorted({a.get("cost_source") for a in attempts if a.get("cost_source")})
    unbilled = [a.get("upstream_unbilled_usd") for a in attempts if a.get("upstream_unbilled_usd")]
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
        "effort_level": effort_level,
        "effort_source": effort_source,
        "effort": effort,
        "effort_tokens": effort_tokens,
        # Which yes let this paid call happen on a gated endpoint: `--approve-spend` on the
        # reconcile invocation, or `manifest` — the run's own recorded `spend_approval.granted`.
        # Null when the endpoint needed no approval. The seats' yes is in `manifest.spend_approval`
        # and this is the judgment call's, because they can be two different answers: a panel
        # approved interactively and a reconcile run later from a script are not the same consent.
        "spend_approval_source": spend_approval_source,
        "max_tokens": cap,
        "status": status,
        "errors": list(errors or []) or None,
        "attempts": attempts,
        "attempts_count": len(attempts),
        "usage": attempts[-1].get("usage") if attempts else None,
        "reasoning_tokens": sum(int(a.get("reasoning_tokens") or 0) for a in attempts) or None,
        "cost_usd": sum(costs) if costs else None,
        "cost_sources": cost_sources,
        "cost_estimated": "estimated" in cost_sources,
        "upstream_unbilled_usd": sum(unbilled) if unbilled else None,
        "elapsed_s": round(elapsed_s, 1) if elapsed_s is not None else None,
        # What the judge was actually shown. `prompt_tokens` is the composed prompt measured
        # against the model's window, and `report_elisions` names every report the per-report cap
        # cut — a judge that arbitrated a report it saw three quarters of is a fact about this
        # run's judgment, and it used to be visible nowhere at all.
        "prompt_tokens": prompt_tokens,
        "report_elisions": list(elisions or []) or None,
    }


def _dump(document):
    return json.dumps(document, indent=2, ensure_ascii=False)

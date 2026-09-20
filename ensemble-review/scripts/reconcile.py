#!/usr/bin/env python3
"""Reconcile a run directory into `reconciliation.json` and `reconciliation.md`.

This script is the **only** writer of both files, in every mode. It computes the mechanical half of
the algorithm — normalized location equality, quote overlap, agreement tiers, the run-level verdict,
the final BK/SF/NH ids — and takes the semantic half from a judgment patch supplied by whichever mind
is available: the host session interactively, the `synthesis` persona autonomously.

    reconcile.py --run-dir <dir>                        # writes judgment-request.json, exits 3
    reconcile.py --run-dir <dir> --judgment judgment.json
    reconcile.py --run-dir <dir> --reconciler synthesis # dispatches the persona for the patch
    reconcile.py --run-dir <dir> --render-only          # re-render the Markdown from the JSON

Run it once with no patch to get the provisional clusters and the questions they raise, write the
patch against `schemas/judgment-patch.schema.json`, then run it again. Nothing is written when the
patch fails validation: the failing entries are named and the supplier is asked again.

**The Judge stage.** `--reconciler` says who supplies that patch: `host` leaves it to the session,
`synthesis` dispatches the persona, `harness-judge` ingests what the shipped `ensemble-judge`
harness agent wrote, and `default` — the panel's setting, recorded in the manifest — means host when
a human is attached and, when nobody is, the harness judge where one is installed and `synthesis`
where none is. In `synthesis` mode with no `judgment.json` on disk, this script composes the
persona's user message from every validated report, the provisional clusters, the references and the
pinned artifact, and makes the call through `dispatch.py`'s own machinery: the same driver, the same
doubled-cap length retry, the same registry gate, and the same per-call cost record, which lands in
the manifest as `judge`. The patch is validated — shape, then references, then a full trial merge —
**before** `judgment.json` is written, so an invalid patch never reaches disk. One repair re-ask
names the failing entries; a second failure is exit 3 with nothing written. A patch from an
unattended author carrying `rulings` is a hard error and earns no re-ask: stating a ruling on a
design fork is the one thing an unattended judge may not do.

**In `harness-judge` mode this script makes no call at all.** A script cannot spawn a harness agent,
so with no patch on disk it prints the spawn instruction — the agent, the run directory, the package
path and the seat-private staging path the agent writes to — and exits 3. The patch that comes back
is held to its author exactly as a dispatched `synthesis` patch is: one claiming `host` on a run with
no host is refused, exit 3, nothing written.

**A direct judgment call is gated.** `reconcile.py --reconciler synthesis` makes a paid call and the
cost pre-flight lives in `run_panel.py`, so this refuses with exit 4 unless the run's own manifest
carries a projection that priced a synthesis call — or `--approve-budget` says the operator has
weighed it. **And the composed prompt is measured before it is sent**: a judgment prompt over the
judge model's context limit is a judge-stage failure that writes nothing, records the reason in
`manifest.judge` and exits 3.

The **pinned artifact is mandatory** in every mode but one: every `quote` and every
`literal_edit.old_text` is verified against it and any member whose anchor is not real text is
dropped from its cluster. It resolves in three steps, in this order — `--artifact` first, then the
run's own read-only `inputs/` copy, then the manifest's `artifact` path — and none of them
resolving is a usage error. Only `--render-only`, which re-renders from an already-checked
`reconciliation.json`, runs without it.

Exit codes: 0 success, meaning both files were written and every expected seat validated; 1 usage;
3 a judgment patch is required or could not be validated, the composed judgment prompt does not fit
the judge model, or the run reconciled with a missing seat — which is emitted at wrap-up, after both
files are written with the missing seat recorded in them; 4 a judgment call nothing has priced.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import dispatch  # noqa: E402
from backends import AuthFailure  # noqa: E402
from lib import budget as budget_lib  # noqa: E402
from lib import connectors as connectors_lib  # noqa: E402
from lib import judge as judge_lib  # noqa: E402
from lib import panels as panels_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402
from lib import runs as runs_lib  # noqa: E402
from lib import schema as schema_lib  # noqa: E402

SEVERITY_ORDER = core.SEVERITY_ORDER

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_TERMINAL = 2
EXIT_PATCH = 3
EXIT_BUDGET = 4


class SpendNotApproved(Exception):
    """The judgment call would bill a metered endpoint nobody has approved. The caller exits 4."""


class HardPatchError(Exception):
    """A patch defect no re-ask can be asked to repair. Nothing is written and nobody is re-asked."""


# --- validation of the two contracts ---------------------------------------------------------------

def validate_patch_document(patch, paths):
    return schema_lib.validate(patch, schema_lib.load(paths.schema("judgment-patch.schema")))


def validate_reconciliation_document(document, paths):
    """The whole document, clusters included — the cluster contract lives in the schema file itself.

    It used to be parked in `definitions.cluster` and applied here in a second pass, which left any
    other consumer validating `reconciliation.json` with no cluster checking at all.
    """
    return schema_lib.validate(document, schema_lib.load(paths.schema("reconciliation.schema")))


# --- rendering ---------------------------------------------------------------------------------

TIER_NOTE = {
    "unanimous": "every expected seat, across families",
    "consensus": "cross-family corroboration",
    "same-family": "several lenses, one family, uncorroborated",
    "corroborated-same-family": "two or more seats, one family",
    "singleton": "one seat",
}


def render_markdown(document, reports=None):
    """`reconciliation.md`, rendered from the JSON and from nothing else but the seat verdicts."""
    reports = reports or {}
    lines = []
    clusters = document.get("clusters") or []
    by_severity = {"blocker": [], "should-fix": [], "nice-to-have": []}
    for cluster in clusters:
        by_severity.setdefault(cluster["severity"], []).append(cluster)

    # Three groups, not two. A should-fix whose members are all one family is not corroborated and
    # is not a single seat either, and it used to render under "Single-seat should-fixes" — which on
    # a draft pass, where every seat is one family, is where every multi-lens agreement landed. Each
    # group gets a heading that says what it is.
    corroborated = [c for c in by_severity["should-fix"] if c["n_families"] >= 2]
    same_family = [c for c in by_severity["should-fix"]
                   if c["n_families"] < 2 and c["n_reviewers"] >= 2]
    single_seat = [c for c in by_severity["should-fix"] if c["n_reviewers"] < 2]

    lines.append("# Reconciliation — {0}, run {1}".format(document["artifact"].get("path") or "artifact", document["run_id"]))
    lines.append("")
    lines.append("> **Verdict: {0}.** {1} clusters from {2} reporting seat(s) across {3} famil{4}. Panel `{5}`; judgment supplied by `{6}`.".format(
        document["verdict"],
        document["counts"]["clusters"],
        len(document["seats_reporting"]),
        len(document["families_reporting"]),
        "y" if len(document["families_reporting"]) == 1 else "ies",
        document["panel"],
        document["reconciler"],
    ))
    lines.append(">")
    lines.append("> Reconciled by `reconcile.py` from `{0}`, generated {1}.".format(document["judgment"]["path"], document["generated_at"]))
    lines.append("")

    if document["missing_seats"]:
        lines.append("**Missing seats.** Agreement counts below are read against them:")
        lines.append("")
        for seat in document["missing_seats"]:
            line = "- `{0}` ({1}) — {2}: {3}".format(
                seat["reviewer_id"], seat.get("family") or "?", seat["stage"], seat["reason"])
            # Where the reason is the run's own `failure_reason` it is a terse code — `no-references`,
            # `context-overflow`, `stale-lease` — and the prose that explains it is in `error`.
            # Appended only in that case: a validation reason is already a sentence, and the `error`
            # beside it is a tail of stderr that belongs in the manifest and not in the product.
            detail = (seat.get("error") or "").strip().replace("\n", " ")
            if seat.get("failure_reason") and detail:
                line += " — {0}".format(detail[:300] + ("…" if len(detail) > 300 else ""))
            lines.append(line)
        lines.append("")

    if document.get("anchor_drops"):
        lines.append("**Dropped anchors.** These members quoted text that is not in the pinned artifact and were dropped from their clusters before tiering:")
        lines.append("")
        for drop in document["anchor_drops"]:
            lines.append("- `{0}` {1} (from `{2}`) — {3}".format(
                drop["reviewer_id"], drop["finding_id"], drop.get("cluster") or "?", drop["reason"]))
        lines.append("")

    lines.append("## Verdict matrix")
    lines.append("")
    lines.append("| Seat | Lens | Family | Verdict | Findings |")
    lines.append("| --- | --- | --- | --- | --- |")
    for reviewer_id in document["seats_expected"]:
        data = reports.get(reviewer_id)
        if data is None:
            lines.append("| `{0}` | — | — | *missing* | — |".format(reviewer_id))
            continue
        counts = {"blocker": 0, "should-fix": 0, "nice-to-have": 0}
        for finding in data.get("findings") or []:
            if finding.get("severity") in counts:
                counts[finding["severity"]] += 1
        lines.append("| `{0}` | {1} | {2} | {3} | {4} / {5} / {6} |".format(
            reviewer_id, data.get("lens", "?"), data.get("family", "?"), data.get("verdict", "?"),
            counts["blocker"], counts["should-fix"], counts["nice-to-have"]))
    lines.append("")
    lines.append("Findings columns are blocker / should-fix / nice-to-have.")
    lines.append("")

    lines.append("## Blockers")
    lines.append("")
    if not by_severity["blocker"]:
        lines.append("None.")
        lines.append("")
    else:
        for cluster in by_severity["blocker"]:
            lines.extend(_render_cluster_block(cluster))

    lines.append("## Corroborated should-fixes (cross-family)")
    lines.append("")
    lines.extend(_render_cluster_table(corroborated, families=True))

    lines.append("## Same-family should-fixes — one family, uncorroborated")
    lines.append("")
    lines.append("Two or more seats agreed and every one of them is the same family, so this is one mind agreeing with itself under several lens prompts and not corroboration. Every one carries a label — blind-spot catch or family-specific false positive — and the reason for it, exactly as a single-seat cluster does. An unlabelled same-family cluster is not an allowed output.")
    lines.append("")
    lines.extend(_render_labelled_table(same_family))

    lines.append("## Single-seat should-fixes")
    lines.append("")
    lines.append("Every one carries a label — blind-spot catch or family-specific false positive — and the reason for it. An unlabelled single-seat cluster is not an allowed output.")
    lines.append("")
    lines.extend(_render_labelled_table(single_seat))

    lines.append("## Nice-to-haves")
    lines.append("")
    lines.extend(_render_cluster_table(by_severity["nice-to-have"], families=True))

    lines.append("## Disagreements")
    lines.append("")
    disagreements = document["disagreements"]
    if not disagreements["contradictions"]:
        lines.append("**Direct contradictions: none.** No reviewer explicitly approved a passage another flagged.")
    else:
        lines.append("**Direct contradictions.** Each of these is `flag-for-human` regardless of counts.")
        lines.append("")
        for entry in disagreements["contradictions"]:
            lines.append("- **{0}** — flagged by {1}, contradicted by {2}. {3}".format(
                entry["cluster"],
                ", ".join("`{0}`".format(r) for r in entry.get("flagged_by") or []),
                ", ".join("`{0}` {1}".format(r["reviewer_id"], r["finding_id"]) for r in entry["contradicted_by"]),
                entry.get("ruling") or ""))
    lines.append("")

    spreads = disagreements["severity_spreads"]
    lines.append("**Severity spreads of two or more steps: {0}.**".format(len(spreads)))
    lines.append("")
    if spreads:
        lines.append("| Cluster | Steps | High side | Low side | Ruling |")
        lines.append("| --- | --- | --- | --- | --- |")
        for entry in spreads:
            lines.append("| {0} | {1} | {2} | {3} | {4} |".format(
                entry["cluster"], entry["steps"],
                ", ".join("`{0}`".format(r) for r in entry["high_side"]),
                ", ".join("`{0}`".format(r) for r in entry["low_side"]),
                _cell(entry.get("ruling"))))
        lines.append("")

    for entry in disagreements["altitude_splits"]:
        lines.append("**Altitude split — `{0}`, on {1}.** This is not a conflict and is not reported as one. {2}".format(
            entry["reviewer_id"], ", ".join(entry["clusters"]), entry["note"]))
        lines.append("")

    lines.append("## Action plan")
    lines.append("")
    for disposition, heading in (
        ("fix-now", "**Fix now.**"),
        ("flag-for-human", "**Flag for the owner.**"),
        ("defer", "**Deferred** (recorded, not actioned)."),
    ):
        picked = [c for c in clusters if c["disposition"] == disposition]
        if not picked:
            continue
        lines.append("{0} {1}".format(heading, ", ".join(c["id"] for c in picked)))
        lines.append("")
        for cluster in picked:
            if cluster.get("ruling"):
                lines.append("- **{0} — stated ruling{1}.** {2}".format(
                    cluster["id"],
                    ", owner to confirm" if cluster["ruling"]["owner_to_confirm"] else "",
                    cluster["ruling"]["text"]))
        if any(c.get("ruling") for c in picked):
            lines.append("")

    lines.append("## Method caveat")
    lines.append("")
    lines.append(document["method_caveat"].strip())
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _cell(value):
    return (value or "").replace("|", "\\|").replace("\n", " ")


def _members_cell(cluster):
    bits = []
    for member in cluster["members"]:
        ids = [member["finding_id"]] + list(member.get("collapsed_finding_ids") or [])
        bits.append("`{0}` {1}".format(member["reviewer_id"], ", ".join(ids)))
    return ", ".join(bits)


def _render_cluster_table(clusters, families=False):
    if not clusters:
        return ["None.", ""]
    lines = ["| ID | Claim | Seats | Families | Tier | Severity spread | Disposition |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for cluster in clusters:
        lines.append("| {0} | {1} | {2} | {3} | {4} | {5} | **{6}** — {7} |".format(
            cluster["id"],
            _cell(cluster["claim"]),
            _members_cell(cluster),
            ", ".join(cluster["families"]) if families else str(cluster["n_families"]),
            cluster["tier"],
            ", ".join("{0}: {1}".format(s["reviewer_id"].split("-")[0], s["severity"]) for s in cluster["severity_spread"]),
            cluster["disposition"],
            _cell(cluster["disposition_reason"]),
        ))
    lines.append("")
    return lines


def _render_labelled_table(clusters):
    if not clusters:
        return ["None.", ""]
    lines = ["| ID | Claim | Seat | Label and reason | Disposition |", "| --- | --- | --- | --- | --- |"]
    for cluster in clusters:
        lines.append("| {0} | {1} | {2} | **{3}.** {4} | **{5}** — {6} |".format(
            cluster["id"],
            _cell(cluster["claim"]),
            _members_cell(cluster),
            cluster.get("singleton_label") or "unlabelled",
            _cell(cluster.get("singleton_reason")),
            cluster["disposition"],
            _cell(cluster["disposition_reason"]),
        ))
    lines.append("")
    return lines


def _render_cluster_block(cluster):
    lines = []
    lines.append("### {0} — {1}".format(cluster["id"], cluster["claim"]))
    lines.append("")
    lines.append("**{0}** · {1} · {2} · seats: {3}".format(
        cluster["tier"], TIER_NOTE.get(cluster["tier"], ""), cluster["location"], _members_cell(cluster)))
    if cluster.get("singleton_label"):
        lines.append("")
        lines.append("*{0}.* {1}".format(cluster["singleton_label"], cluster.get("singleton_reason") or ""))
    if cluster.get("quote"):
        lines.append("")
        lines.append("> {0}".format(cluster["quote"].strip().replace("\n", "\n> ")))
    lines.append("")
    lines.append("**Severity.** {0} — {1}".format(cluster["severity"], cluster["arbitration_reason"]))
    lines.append("")
    lines.append("**Disposition.** {0} — {1}".format(cluster["disposition"], cluster["disposition_reason"]))
    lines.append("")
    return lines


# --- the judge stage -----------------------------------------------------------------------------

def _panel_for(paths, manifest):
    """The run's panel template, when the cascade can still find it. `None` is not an error.

    Only one field on it matters here — an optional `synthesis` block naming the family or tier the
    judgment call should take — so a run whose panel was composed ad hoc, or whose template has
    since moved, falls through to the declaration-order default rather than refusing.

    **It is held to the same strict vocabulary `run_panel.py` holds it to**, and a template that
    fails it is treated as a template that is not there: warned about, and fallen back from. The
    two loaders disagreeing would mean a template `run_panel.py` refuses to dispatch could still
    steer the judgment call's seat here, which is the one field this reads. Refusing outright
    would be worse than the fallback — this path is only reached for a run with no recorded
    `judge_seat`, and a judgment that cannot be made is a worse outcome than a judge seated by the
    declaration-order default — but honouring it silently is not an option either.
    """
    name = manifest.get("panel")
    if not name or name == "ad-hoc":
        return None
    try:
        found = paths.find("panel", name)
    except KeyError:
        return None
    if not found:
        return None
    try:
        with open(found["path"], "r", encoding="utf-8") as handle:
            panel = json.load(handle)
    except (ValueError, OSError):
        return None
    try:
        return panels_lib.validate(panel, found["path"])
    except panels_lib.TemplateError as failure:
        sys.stderr.write(
            "warning: the panel template for this run does not load, so its `synthesis` block was "
            "ignored and the judgment call is seated by the declaration-order default.\n  "
            "{0}\n".format(str(failure).replace("\n", "\n  ")))
        return None


def _materialized_references(run_dir, manifest):
    """`(label, text)` for every reference, read from the run's own read-only `inputs/` copy.

    The pinned bytes, for the same reason the artifact is: a reference edited since the panel ran is
    not the document the seats judged against, and the judge is arbitrating their citations.
    """
    references = []
    for record in manifest.get("reference_revisions") or []:
        materialized = record.get("materialized")
        if not materialized:
            continue
        path = os.path.join(run_dir, materialized)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            references.append((record.get("path") or materialized, handle.read()))
    return references


def _spend_approval_source(manifest, args):
    """Which yes lets this judgment call bill the endpoint, or None when nobody has said one.

    Two sources, and the second is what makes the printed host-run line work. `--approve-spend` on
    this invocation is the direct answer. Failing that, **the run's own recorded
    `spend_approval.granted`** for the run directory this process was handed: a person already said
    yes to paying on this endpoint for this run, the manifest records who and where the yes came
    from, and re-asking would mean a run that approved its own seats cannot finish its own judgment.
    The recorded yes is scoped to one run directory, which is the whole of what it claims — it is
    not a standing approval for the endpoint, and a reconcile pointed at a different run reads that
    run's own record or none.

    A run that refused, or one that was never gated, records `granted` false or null and answers
    nothing here.
    """
    if args.approve_spend:
        return "--approve-spend"
    recorded = (manifest or {}).get("spend_approval")
    if isinstance(recorded, dict) and recorded.get("granted"):
        return "manifest"
    return None


def dispatch_synthesis(paths, run_dir, manifest, context, artifact_text, args):
    """Make the judgment call. Returns `(patch, record)`; `patch` is None when nothing survived.

    The patch is validated in three widening passes before it is believed — the schema, then the
    references and enums `validate_patch_shape` checks, then a **full trial merge** through
    `core.reconcile`, which is the only thing that can catch a label on a cluster the patch's own
    splits dissolved. All three feed the one repair re-ask.
    """
    persona_name = judge_lib.SYNTHESIS_PERSONA
    persona_path = paths.persona(persona_name)
    frontmatter, body = report_lib.parse_agent_file(persona_path)
    # The same resolver the seat leg uses, so a workspace persona's declared `context` means the
    # same thing on both. It was two implementations, and the seat one silently dropped everything
    # but the finding schema.
    reference_paths = dispatch.persona_context_paths(paths, frontmatter)

    try:
        config_entry, _config_path, connector, _registry = dispatch.resolve_entry(
            paths, args.config, args.models)
    except dispatch.CompositionError as failure:
        raise judge_lib.JudgeError(str(failure))

    # **The judgment call is a paid call, so it is behind the same spend gate the seats are.** A
    # reconcile typed on its own, hours after the panel, is the case this exists for: the run's own
    # spend approval covered the seats it dispatched, and this is a new call on the same metered
    # endpoint.
    #
    # **It refuses rather than prompting, which is the shape `_budget_gate` already has here.**
    # `run_panel.py` is the only place in this skill that reads a tty, and it records what it read;
    # a reconcile typed into a pipe or a CI step has a stdin that says nothing about whether a
    # person is waiting, and a paid call must not turn on that. So the answer to "may this spend"
    # is the flag or the run's own record, and never a prompt.
    spend_source = _spend_approval_source(manifest, args) if connectors_lib.requires_approval(connector) else None
    if connectors_lib.requires_approval(connector) and spend_source is None:
        raise SpendNotApproved(
            connectors_lib.refusal(connector, [{"reviewer_id": judge_lib.SYNTHESIS_PERSONA}],
                                   flag="--approve-spend")
            + "\n  This run's own manifest records no granted `spend_approval` either, so there is "
              "no yes anywhere to honour.\n  No judgment call was made.")

    # **The seat Resolve worked out, model included, when the run recorded one.** The projection
    # that gated this run was computed against that seat, so re-deriving it here would price one
    # model and call another the moment a config cell was edited between the panel and the
    # judgment — the budget gate would have weighed a call nobody makes. The recorded `model` is
    # therefore passed to `prepare_call` as a **pin**, exactly as `--synthesis-model` is, rather
    # than left to the tier map to resolve a second time.
    #
    # A run made before `judge_seat` existed, or one reconciled by hand, falls back to resolving it
    # from the same inputs Resolve had — and that fallback is the only thing the template is still
    # loaded for, because Resolve now records the effort level too.
    panel = _panel_for(paths, manifest)
    recorded = manifest.get("judge_seat")
    if isinstance(recorded, dict) and recorded.get("family") and recorded.get("tier"):
        seat = dict(recorded)
    else:
        seat = judge_lib.synthesis_seat(
            config_entry, panel,
            cli_tier=manifest.get("tier_default"),
            seated_families=[s.get("family") for s in manifest.get("seats") or [] if s.get("family")],
            persona_tier=frontmatter.get("model"))
    family, tier = seat["family"], seat["tier"]

    # `--synthesis-model` on this invocation beats the recorded pin: an operator naming a model here
    # is overriding the run's own choice on purpose, and says so in `family_source`.
    pinned = args.synthesis_model or seat.get("model")
    if args.synthesis_model:
        seat["family_source"] = "--synthesis-model"
        seat["model"] = args.synthesis_model

    cap = args.max_tokens
    if cap is None:
        cap = manifest.get("max_tokens_requested") or registry_lib.DEFAULT_MAX_TOKENS

    # **The effort level is read back from the record, exactly as the model is.** Resolve worked it
    # out on the judge's own order — the panel's `synthesis.effort` first, then the level the run's
    # seats dispatched at, then the config's `default_effort`, then the `synthesis` persona's own
    # frontmatter — and Resolve is the stage that holds the template. This one re-finds a template
    # only by name, so re-deriving here dropped a `synthesis.effort` pin on every panel invoked by
    # path. It is re-derived **only for a manifest written before `judge_seat.effort_level`
    # existed**, which is the same fallback the seat itself has and reaches the same answer for
    # every run whose panel the cascade can still find by name. The harness judge is untouched by
    # any of this — its model and its effort are pinned in its installed agent file and it is not
    # seated from a tier map at all.
    effort_level = seat.get("effort_level")
    effort_source = seat.get("effort_source")
    if not effort_level:
        effort_level, effort_source = judge_lib.synthesis_effort(
            manifest, config_entry, frontmatter, panel=panel)
    seat["effort_level"] = effort_level
    seat["effort_source"] = effort_source

    call = dispatch.prepare_call(
        paths, family, tier=tier, model=pinned, max_tokens=cap,
        config_override=args.config, models_override=args.models, label="synthesis",
        effort_level=effort_level)

    request = core.judgment_request(context)
    system_prompt = dispatch.build_system_prompt(body, reference_paths)
    elisions = []
    user_prompt = judge_lib.build_user_message(
        run_id=manifest.get("run_id"),
        request=request,
        reports=context["reports"],
        artifact_text=artifact_text,
        artifact_label=manifest.get("artifact") or "the artifact",
        artifact_revision=manifest.get("artifact_revision"),
        references=_materialized_references(run_dir, manifest),
        elisions=elisions)

    # **Does it fit?** Measured here, against the real composed prompt, because this is the first
    # point at which the prompt exists. The seat pre-flight has always measured every seat against
    # its model's window and the judgment call was in no such check (run 5, SF-11): the per-report
    # cap bounds one seat's share and the judge's prompt is every report *plus* the artifact *plus*
    # every reference *plus* the clusters. An overflow is a judge-stage failure — nothing written,
    # the reason on `manifest.judge`, exit 3 — and not a paid call that would be refused by the
    # provider after being charged for.
    try:
        prompt_tokens = judge_lib.check_prompt_fits(
            system_prompt, user_prompt, (call.registry_entry or {}).get("context_limit"),
            call.model, budget_lib.approx_tokens)
    except judge_lib.JudgePromptTooLong as failure:
        _record_judge_call(run_dir, judge_lib.judge_record(
            reviewer_id=judge_lib.SYNTHESIS_PERSONA, family=family, tier=tier, model=call.model,
            connector=call.connector, provider=call.provider, effort=call.effort, cap=call.cap,
            attempts=[], elapsed_s=None, status="refused", errors=[str(failure)],
            tier_source=seat.get("tier_source"), family_source=seat.get("family_source"),
            effort_level=call.effort_level, effort_source=effort_source,
            effort_tokens=call.effort_tokens, elisions=elisions))
        raise

    for elision in elisions:
        print("judge: {0}'s report is {1:,} characters against a {2:,} cap; {3:,} elided from the "
              "middle".format(elision["reviewer_id"], elision["chars"], elision["cap"],
                              elision["elided_chars"]))

    def check(raw_text):
        try:
            patch = json.loads(report_lib.strip_fence(raw_text))
        except ValueError as exc:
            return None, ["the response was not parseable JSON: {0}".format(exc)]
        if not isinstance(patch, dict):
            return None, ["the response is a {0}, expected a JSON object".format(type(patch).__name__)]
        # **`author` is assigned, never defaulted.** Both halves of the asymmetry that makes an
        # unattended judge safe key on this one field — `reconcile_core` rejects `rulings` from
        # `synthesis` and forces an all-`judgment-call` cluster to `flag-for-human` for that author
        # alone — so a patch that wrote `"author": "host"` would escape both and settle a design
        # fork with nobody's name on it. What is authoritative is who this script dispatched, not
        # who the response says it is; the whole point of the audit fields on a report is the same
        # rule one level up.
        claimed = patch.get("author")
        patch["author"] = judge_lib.SYNTHESIS_AUTHOR
        disowned = []
        if claimed is not None and claimed != judge_lib.SYNTHESIS_AUTHOR:
            # Named, not quietly corrected. The field is overwritten either way — the enforcement
            # below runs against `synthesis` whatever the response said — but a patch that disowns
            # its own author is a patch reaching for the latitude the other author has, and that is
            # exactly the thing the reader of this run needs told.
            disowned = [
                "judgment: the patch claims `author: {0!r}` and was written by the `synthesis` "
                "persona this script dispatched. The author decides which half of the "
                "judgment-call rule applies and whether `rulings` is allowed; it is not the "
                "supplier's to choose. The field has been set to `synthesis`.".format(claimed)]

        # The one defect a re-ask may not be asked to repair. An unattended persona that states a
        # ruling on a design fork has recorded a human decision as settled by nobody, and asking it
        # again would be asking it to say the same thing more quietly.
        if patch.get("rulings"):
            raise HardPatchError(
                "the judgment patch carries {0} `rulings` entr(y/ies). `rulings` is the interactive "
                "host's alone: it is a decision of record on a design fork, and an unattended judge "
                "has no owner to answer to. Nothing was written and no re-ask was made.".format(
                    len(patch["rulings"])))
        errors = validate_patch_document(patch, paths)
        if not errors:
            errors = core.validate_patch_shape(patch, manifest, context["reports"])
        if not errors:
            _document, merge_errors, _context = core.reconcile(run_dir, patch, artifact_text)
            errors = merge_errors
        return patch, disowned + errors

    print("judge: dispatching the `synthesis` persona on {0} ({1}, tier {2}) for the judgment patch".format(
        call.model, family, tier))
    attempts = []
    started = time.time()
    status = "ok"
    errors = []
    patch = None
    try:
        outcome = dispatch.attempt_loop(
            call, system_prompt, user_prompt, check, judge_lib.build_repair_prompt, attempts=attempts)
        patch, errors = outcome.parsed, outcome.errors
        if errors:
            _print_errors("the judgment patch from `synthesis` still does not hold after the repair "
                          "re-ask; nothing was written", errors)
            status = "failed"
            patch = None
    except HardPatchError as refusal:
        status, errors = "refused", [str(refusal)]
        raise
    except (AuthFailure, dispatch.DispatchFailed) as failure:
        status, errors = "failed", [str(failure)]
        raise
    finally:
        # The record is written in a `finally` on purpose: a judgment call that failed, or that was
        # refused outright, still spent money, and a manifest that reports it as free is the same
        # under-accounting the failed-seat record was built to close.
        record = judge_lib.judge_record(
            reviewer_id=judge_lib.SYNTHESIS_PERSONA, family=family, tier=tier, model=call.model,
            connector=call.connector, provider=call.provider, effort=call.effort, cap=call.cap,
            attempts=attempts, elapsed_s=time.time() - started, status=status, errors=errors,
            tier_source=seat.get("tier_source"), family_source=seat.get("family_source"),
            effort_level=call.effort_level, effort_source=effort_source,
            effort_tokens=call.effort_tokens, elisions=elisions, prompt_tokens=prompt_tokens,
            spend_approval_source=spend_source)
        _record_judge_call(run_dir, record)

    return patch, record


def _harness_staging_patch(run_dir):
    """Where the harness judge writes its patch: seat-private, outside the run directory."""
    return os.path.join(runs_lib.staging_dir(run_dir, judge_lib.HARNESS_JUDGE_AGENT), "judgment.json")


def _author_holds_for_this_run(patch, patch_path, run_dir, reconciler, args):
    """Why this on-disk patch may not be merged as it stands, or None. A patch error, exit 3.

    The dispatched path assigns `author` from what this script called, so it cannot be disowned.
    The **on-disk** path had no such rule, and the two enforcement paths that make an unattended
    judge safe — `reconcile_core` rejecting `rulings` from an unattended author, and forcing an
    all-`judgment-call` cluster to `flag-for-human` for those authors alone — both key on that
    field. So a `judgment.json` a host session left in a directory, picked up by a resumed
    autonomous run, took the host's latitude on a run that has no host: a design fork disposed
    `fix-now`, with auto-apply's first condition open on it.

    The check is deliberately narrow. It fires only when the run's own record says an **unattended**
    author judges — `synthesis` or `harness-judge` — and the patch was found at one of that run's
    own canonical paths. `--reconciler host` is how an operator says a human wrote this one, and it
    is named in the message rather than left to be guessed at.

    **The harness judge's staging path is a canonical path, and that is why the `--judgment`
    exemption does not cover it.** An explicit `--judgment <somewhere>` normally means an operator
    pointing at a file they are vouching for, which is the second way of saying "a human wrote
    it". For the harness judge it means nothing of the kind: the agent writes outside the run
    directory by design, so `--judgment <staging>` is the *ordinary* ingest and is exactly the
    patch that has to be held to its author.
    """
    if reconciler not in judge_lib.UNATTENDED_AUTHORS:
        return None
    where = os.path.abspath(patch_path)
    staging = os.path.abspath(_harness_staging_patch(run_dir))
    canonical = (os.path.join(os.path.abspath(run_dir), "judgment.json"), staging)
    if args.judgment is not None and where != staging:
        return None
    if where not in canonical:
        return None
    author = patch.get("author") if isinstance(patch, dict) else None
    if author == reconciler:
        return None
    return (
        "the judgment patch at {0} says `author: {1!r}`, and this run's judgment is `{2}`.\n"
        "  The author is not a label: it decides whether an all-`judgment-call` cluster may be\n"
        "  disposed anything but `flag-for-human`, and whether `rulings` is allowed at all. A patch\n"
        "  claiming `host` on a run with no host takes latitude nobody is answering for.\n"
        "  Nothing was written. If a human did write this patch, say so — re-run with\n"
        "  `--reconciler host`, or point at it explicitly with `--judgment {0}`.".format(
            patch_path, author, reconciler))


def _budget_gate(manifest, args):
    """Why this direct judgment call may not be made, or None. The caller exits 4.

    **A paid call with nothing in front of it.** The pre-flight lives in `run_panel.py`, so a run
    that reaches the judge stage through the panel is projected and gated; one that reaches it by
    invoking `reconcile.py` directly is neither. An interactive host reconciling a finished run
    never trips this, because a host writes the patch itself and makes no call. An unattended
    pipeline resuming into the judge stage does.

    The gate is the simplest correct shape rather than a second projection: **this run's own
    manifest must already carry a projection that priced a synthesis call.** That is exactly the
    condition under which the call has been weighed against a budget — by `run_panel.py`, at
    Project, on the judge's own model — and it is true of every run the panel dispatched with
    `synthesis` as its reconciler. When it is not true, `--approve-budget` is the operator saying
    so. Nothing here re-derives a price: a second projection computed from a different set of
    inputs than the one that gated the run would be a second gate disagreeing with the first.
    """
    if args.approve_budget:
        return None
    projection = manifest.get("projection")
    if isinstance(projection, dict) and projection.get("synthesis_allowance_usd") is not None:
        return None
    return (
        "refusing to make the judgment call: nothing has priced it.\n"
        "  `reconcile.py --reconciler synthesis` makes a paid call, and the cost pre-flight lives\n"
        "  in `run_panel.py`. This run's manifest carries {0}, so no budget gate has ever weighed\n"
        "  this call. The judgment call is the dearest single call most runs make — 45% of run 4's\n"
        "  entire spend.\n"
        "  Either re-run the panel so the projection covers it, or pass --approve-budget to say\n"
        "  you have weighed it yourself.\n".format(
            "no projection at all" if not isinstance(projection, dict)
            else "a projection that charged nothing for a synthesis call"))


def _record_judge_call(run_dir, record):
    """Put the judgment call in the manifest as `judge`, and its cost in `cost_usd_total`.

    Not in `seats`: that array is the `unanimous` denominator and every entry in it with no report
    file is reported as a missing seat, so a judge seated there would make every autonomous run
    reconcile as under-seated by exactly one.
    """
    try:
        manifest = runs_lib.read_manifest(run_dir)
    except runs_lib.RunError:
        return
    manifest["judge"] = record
    total = 0.0
    known = False
    for seat in manifest.get("seats") or []:
        if seat.get("cost_usd") is not None:
            total += float(seat["cost_usd"])
            known = True
    if record.get("cost_usd") is not None:
        total += float(record["cost_usd"])
        known = True
    manifest["cost_usd_total"] = round(total, 6) if known else None
    runs_lib.write_manifest(run_dir, manifest)


# --- cli -----------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Reconcile a panel run directory into reconciliation.json and reconciliation.md.")
    parser.add_argument("--run-dir", required=True, help="The run directory holding manifest.json and one <reviewer-id>.json per seat")
    parser.add_argument("--judgment", help="The judgment patch. Defaults to <run-dir>/judgment.json when that file exists")
    parser.add_argument("--artifact", help="The pinned artifact. Defaults to the manifest's `artifact` path; required when that does not resolve, because the anchor check is not optional")
    parser.add_argument("--render-only", action="store_true", help="Re-render reconciliation.md from an existing reconciliation.json and write nothing else")
    parser.add_argument("--workspace", default=None,
                        help="The project holding the artifact. Schemas resolve from <workspace>/.config/ensemble-review/schemas/ first, then the skill package (default: the working directory)")
    parser.add_argument("--reconciler", choices=judge_lib.RECONCILERS, default=None,
                        help="Who supplies the judgment patch (default: the manifest's, else `default` — host when a human is attached, synthesis when nobody is)")
    parser.add_argument("--autonomous", action="store_true",
                        help="Nobody is attached: `--reconciler default` resolves to an unattended judge. Read from the run's own manifest when this is not given; this process's stdin is never consulted")
    parser.add_argument("--approve-budget", action="store_true", dest="approve_budget",
                        help="Make the judgment call even though no projection in this run's manifest priced one. The pre-flight lives in run_panel.py, so a judge stage reached by invoking this script directly has never been weighed against a budget")
    parser.add_argument("--synthesis-model", default=None,
                        help="Pin the judgment call to a concrete model id, overriding the tier map")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="Completion cap for the judgment call; a model's registry floor raises it (default: the run's own cap, else {0})".format(registry_lib.DEFAULT_MAX_TOKENS))
    parser.add_argument("--config", default=None, help="Config JSON, taken as given (default: the workspace-first cascade, deep-merged)")
    parser.add_argument("--models", default=None, help="Directory of model files, taken as given (default: the workspace-first cascade, deep-merged per model)")
    parser.add_argument("--approve-spend", action="store_true", dest="approve_spend",
                        help="Allow the judgment call to be paid for on a `billing: metered` connector. A different question from --approve-budget, which answers \"this run carries no priced allowance for it\"")
    args = parser.parse_args(argv)

    paths = paths_lib.Paths(args.workspace)
    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        sys.stderr.write("no such run directory: {0}\n".format(run_dir))
        return 1

    if args.render_only:
        path = os.path.join(run_dir, "reconciliation.json")
        if not os.path.isfile(path):
            sys.stderr.write("--render-only needs an existing {0}\n".format(path))
            return 1
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        errors = validate_reconciliation_document(document, paths)
        if errors:
            _print_errors("reconciliation.json does not validate", errors)
            return 3
        reports, _missing = _safe_reports(run_dir)
        report_lib.write_text(os.path.join(run_dir, "reconciliation.md"), render_markdown(document, reports))
        print("re-rendered {0}".format(os.path.join(run_dir, "reconciliation.md")))
        return 0

    patch_path = args.judgment
    if patch_path is None:
        # Two canonical places, in order: the run directory, where a host leaves one and where a
        # dispatched `synthesis` patch is written; then the harness judge's staging directory,
        # which is outside the run directory on purpose, so that `reconcile.py --run-dir <dir>`
        # with nothing else still finds what the spawned agent wrote.
        for default in (os.path.join(run_dir, "judgment.json"), _harness_staging_patch(run_dir)):
            if os.path.isfile(default):
                patch_path = default
                break

    artifact_path, why = _resolve_artifact(run_dir, args.artifact)
    if artifact_path is None:
        sys.stderr.write("{0}\n".format(why))
        sys.stderr.write(
            "the anchor check is not optional: every `quote` and every `literal_edit.old_text` is "
            "verified against the pinned artifact. Pass --artifact <path>.\n")
        return 1
    revision_error = _check_revision(run_dir, artifact_path)
    if revision_error:
        sys.stderr.write("{0}\n".format(revision_error))
        return 1
    with open(artifact_path, "r", encoding="utf-8") as handle:
        artifact_text = handle.read()

    # **Who judges is resolved before either branch, because both branches need holding to it.**
    # It used to be resolved inside the dispatch branch alone, which left the on-disk branch with no
    # opinion at all: a `judgment.json` a host session had left in a directory was merged as-is, so
    # an autonomous run resuming over it took a patch claiming `author: host` and applied the host's
    # latitude — the same hole the dispatched path had closed, reached by the other door.
    #
    # **Autonomy is a fact about the run, not about this process's stdin.** `run_panel.py` reads the
    # tty because it is the process the operator launched; a `reconcile.py` typed into a pipeline, a
    # CI step or an editor has a stdin that says nothing about whether a human is waiting, and
    # resolving `default` from it would make a paid judgment call on no evidence. So this side reads
    # what the run recorded, and `--autonomous` is the explicit override.
    try:
        run_manifest = core.load_manifest(run_dir)
    except core.ReconcileError:
        run_manifest = {}
    autonomous = args.autonomous or bool(run_manifest.get("autonomous"))
    try:
        # `harness=` is asked of this machine, exactly as `run_panel.py` asks it of the machine the
        # panel ran on, so the two call sites answer one question the same way. It only decides an
        # unresolved `default`: every run the panel dispatched records a concrete reconciler, which
        # is read from `declared` and settles the question before this is consulted. What it covers
        # is a hand-built manifest, and a run whose panel and whose reconciliation happen on
        # different machines — where the honest answer is the judge this machine can actually spawn.
        reconciler = judge_lib.resolve_reconciler(
            args.reconciler, run_manifest.get("reconciler"), autonomous,
            harness=judge_lib.harness_present())
    except judge_lib.JudgeError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_USAGE

    if patch_path is None:
        try:
            _document, _errors, context = core.reconcile(run_dir, None, artifact_text)
        except core.ReconcileError as failure:
            sys.stderr.write("{0}\n".format(failure))
            return 1
        request = core.judgment_request(context)
        request_path = os.path.join(run_dir, "judgment-request.json")
        report_lib.write_json(request_path, request)
        print("{0} provisional cluster(s) from {1} reporting seat(s); wrote {2}".format(
            len(request["clusters"]), len(context["seats_reporting"]), request_path))
        for seat in context["missing_seats"]:
            print("  missing seat {0}: {1} — {2}".format(seat["reviewer_id"], seat["stage"], seat["reason"]))
        for drop in context["anchor_drops"]:
            print("  dropped {0} {1} from {2}: {3}".format(
                drop["reviewer_id"], drop["finding_id"], drop.get("cluster") or "?", drop["reason"]))

        manifest = context["manifest"]

        # **Zero seats stops everything, before any judge is asked for or spawned.** A
        # reconciliation over zero reports is a lie whoever writes it, so this guard sits above
        # both judges rather than only in front of the paid one: a spawn instruction printed over
        # an empty run directory costs a session's turn to discover the same thing, and the agent
        # would have nothing to read but a worksheet with no clusters in it.
        if not context["seats_reporting"]:
            sys.stderr.write("zero seats reported: there is nothing for the judge to judge.\n")
            return EXIT_TERMINAL

        if reconciler == judge_lib.HARNESS_JUDGE_AUTHOR:
            # A script cannot spawn a harness agent, so this stops and says who to spawn — **after**
            # the worksheet above is on disk, which is the thing the agent answers. The instruction
            # is `lib/judge.py`'s, the same text `run_panel.py` surfaces by running this very pass,
            # because two spawn instructions for one agent is two contracts.
            staging = _harness_staging_patch(run_dir)
            runs_lib.make_staging_dir(run_dir, judge_lib.HARNESS_JUDGE_AGENT)
            sys.stderr.write("a judgment patch is required before anything can be written.\n")
            for line in judge_lib.spawn_instruction(run_dir, staging, paths_lib.SKILL_DIR,
                                                    request_path=request_path):
                sys.stderr.write(line + "\n")
            sys.stderr.write("    python3 scripts/reconcile.py --run-dir {0} --judgment {1}\n".format(
                run_dir, staging))
            return EXIT_PATCH

        if reconciler != judge_lib.SYNTHESIS_AUTHOR:
            sys.stderr.write(
                "a judgment patch is required before anything can be written: answer every cluster in "
                "{0} against schemas/judgment-patch.schema.json, save it as {1}, and re-run with --judgment.\n".format(
                    request_path, os.path.join(run_dir, "judgment.json")))
            return 3

        refusal = _budget_gate(manifest, args)
        if refusal:
            sys.stderr.write(refusal)
            return EXIT_BUDGET

        try:
            patch, _record = dispatch_synthesis(paths, run_dir, manifest, context, artifact_text, args)
        except SpendNotApproved as failure:
            sys.stderr.write("{0}\n".format(failure))
            return EXIT_BUDGET
        except HardPatchError as failure:
            sys.stderr.write("the judgment patch was refused: {0}\n".format(failure))
            return EXIT_PATCH
        except AuthFailure as failure:
            sys.stderr.write("provider auth failure on the judgment call: {0}\n".format(failure))
            sys.stderr.write("  auth failures are not transient; no further paid calls.\n")
            return EXIT_TERMINAL
        except dispatch.DispatchFailed as failure:
            sys.stderr.write("the judgment call failed: {0}\n".format(failure.cause))
            return EXIT_PATCH
        except judge_lib.JudgePromptTooLong as failure:
            # A judge-stage failure, not a usage error: the run is fine, the panel is fine, and the
            # one thing that cannot be done is the judgment as composed. The reason is already on
            # `manifest.judge`; nothing was written and no call was made.
            sys.stderr.write("the judgment call does not fit: {0}\n".format(failure))
            return EXIT_PATCH
        except (paths_lib.PathError, dispatch.CompositionError, judge_lib.JudgeError) as failure:
            sys.stderr.write("the judgment call could not be composed: {0}\n".format(failure))
            return EXIT_USAGE

        if patch is None:
            sys.stderr.write(
                "the `synthesis` persona did not produce a valid judgment patch after one repair "
                "re-ask; nothing was written.\n")
            return EXIT_PATCH

        patch_path = os.path.join(run_dir, "judgment.json")
        report_lib.write_json(patch_path, patch)
        print("wrote {0}".format(patch_path))

    with open(patch_path, "r", encoding="utf-8") as handle:
        patch = json.load(handle)

    author_error = _author_holds_for_this_run(patch, patch_path, run_dir, reconciler, args)
    if author_error:
        sys.stderr.write("{0}\n".format(author_error))
        return EXIT_PATCH

    shape_errors = validate_patch_document(patch, paths)
    if shape_errors:
        _print_errors("the judgment patch does not validate against judgment-patch.schema.json", shape_errors)
        return 3

    try:
        document, errors, context = core.reconcile(run_dir, patch, artifact_text)
    except core.ReconcileError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return 1
    if errors:
        _print_errors("the judgment patch does not hold against the post-merge state; nothing was written", errors)
        return 3

    document["judgment"]["path"] = os.path.relpath(patch_path, run_dir)
    document["generated_at"] = datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()

    schema_errors = validate_reconciliation_document(document, paths)
    if schema_errors:
        _print_errors("reconcile.py produced a document that does not validate; nothing was written", schema_errors)
        return 3

    report_lib.write_json(os.path.join(run_dir, "reconciliation.json"), document)
    report_lib.write_text(os.path.join(run_dir, "reconciliation.md"), render_markdown(document, context["reports"]))

    if patch.get("author") == judge_lib.HARNESS_JUDGE_AUTHOR:
        # The harness judge gets the same `manifest.judge` record a dispatched judgment gets, with
        # the money fields null because there is no bill: it runs on the subscription. Written
        # after both files, so a patch that did not hold leaves no record of a judgment that did
        # not happen.
        _record_judge_call(run_dir, judge_lib.harness_judge_record(
            status="ok", judgment_path=patch_path,
            staging_path=_harness_staging_patch(run_dir)))

    print("verdict: {0}".format(document["verdict"]))
    print("{0} clusters — {1}".format(
        document["counts"]["clusters"],
        ", ".join("{0} {1}".format(count, tier) for tier, count in sorted(document["counts"]["by_tier"].items()))))
    for drop in document.get("anchor_drops") or []:
        print("  dropped {0} {1} from {2}: {3}".format(
            drop["reviewer_id"], drop["finding_id"], drop.get("cluster") or "?", drop["reason"]))
    print("wrote {0} and {1}".format(
        os.path.join(run_dir, "reconciliation.json"), os.path.join(run_dir, "reconciliation.md")))

    if document["missing_seats"]:
        # Both files are written first: the reconciliation of an under-seated run is still the
        # product, and the exit code is how the caller learns not every expected seat validated.
        for seat in document["missing_seats"]:
            sys.stderr.write("missing seat {0}: {1} — {2}\n".format(seat["reviewer_id"], seat["stage"], seat["reason"]))
        sys.stderr.write("{0} of {1} expected seat(s) did not validate; the run is not a clean success.\n".format(
            len(document["missing_seats"]), len(document["seats_expected"])))
        return 3
    return 0


def _check_revision(run_dir, artifact_path):
    """Refuse when the resolved artifact is not the bytes the seats read. Returns a message or None.

    The manifest's `artifact_revision` is the SHA-256 of the bytes materialized into `inputs/`, so
    this is the check that stops a reconciliation being computed against a document that has moved on
    since the panel ran — the anchors would be checked against text no reviewer ever saw.

    `artifact_revision: null` is **unpinned, not mismatched**: every report written before revisions
    existed reads that way, including the replay fixture. It warns and proceeds.
    """
    try:
        manifest = core.load_manifest(run_dir)
    except core.ReconcileError:
        return None
    recorded = manifest.get("artifact_revision")
    if not recorded:
        sys.stderr.write(
            "warning: this run's manifest records no `artifact_revision`, so the artifact at {0} is "
            "unpinned — the anchor check runs against whatever is on disk today.\n".format(artifact_path))
        return None
    actual = _sha256(artifact_path)
    if actual == recorded:
        return None
    return (
        "refusing to reconcile: {0} is not the artifact this run reviewed.\n"
        "  manifest artifact_revision: {1}\n"
        "  the file on disk hashes to: {2}\n"
        "Reconcile against the run's own `inputs/` copy, or re-run the panel against the current "
        "document. `--render-only` re-renders the existing reconciliation without this check.".format(
            artifact_path, recorded, actual))


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _resolve_artifact(run_dir, override):
    """The pinned artifact: `--artifact` wins, then the run's own `inputs/` copy, then the manifest path.

    Returns (path, None) or (None, why). `inputs/` is preferred because it is the bytes the seats
    actually read, and it does not move when the working-tree document does. The manifest records the
    path the panel was dispatched with, which is repo-relative, so it is tried against the working
    directory and then against the ancestors of the run directory — enough to find it from anywhere
    inside the repo, and honest about failing rather than reconciling with the check silently off.
    """
    if override:
        if os.path.isfile(override):
            return os.path.abspath(override), None
        return None, "no such artifact: {0}".format(override)

    try:
        manifest = core.load_manifest(run_dir)
    except core.ReconcileError as failure:
        return None, str(failure)

    materialized = manifest.get("artifact_input")
    if materialized:
        candidate = os.path.join(run_dir, materialized)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate), None

    recorded = manifest.get("artifact")
    if not recorded:
        return None, "the manifest for this run records no `artifact` path"
    if os.path.isabs(recorded):
        return (os.path.abspath(recorded), None) if os.path.isfile(recorded) else (None, "the manifest's artifact {0} is not a readable file".format(recorded))

    candidates = [os.path.abspath(recorded)]
    directory = run_dir
    while True:
        candidates.append(os.path.join(directory, recorded))
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    for candidate in candidates:
        if os.path.isfile(candidate):
            return os.path.abspath(candidate), None
    return None, "the manifest's artifact {0!r} was not found from the working directory or above {1}".format(recorded, run_dir)


def _safe_reports(run_dir):
    """Reports for the rendering, or nothing. Used by --render-only, which re-renders an already
    validated document and must not die on a run directory the reconcile path would refuse."""
    try:
        manifest = core.load_manifest(run_dir)
        return core.load_reports(run_dir, manifest)
    except (core.ReconcileError, ValueError, OSError):
        return {}, []


def _print_errors(headline, errors):
    sys.stderr.write("{0}:\n".format(headline))
    for error in errors:
        sys.stderr.write("  - {0}\n".format(error))


if __name__ == "__main__":
    sys.exit(main())

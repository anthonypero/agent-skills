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
`synthesis` dispatches the persona, and `default` — the panel's setting, recorded in the manifest —
means host when a human is attached and `synthesis` when nobody is. In `synthesis` mode with no
`judgment.json` on disk, this script composes the persona's user message from every validated
report, the provisional clusters, the references and the pinned artifact, and makes the call through
`dispatch.py`'s own machinery: the same driver, the same doubled-cap length retry, the same registry
gate, and the same per-call cost record, which lands in the manifest as `judge`. The patch is
validated — shape, then references, then a full trial merge — **before** `judgment.json` is written,
so an invalid patch never reaches disk. One repair re-ask names the failing entries; a second failure
is exit 3 with nothing written. A patch from `synthesis` carrying `rulings` is a hard error and earns
no re-ask: stating a ruling on a design fork is the one thing an unattended judge may not do.

The **pinned artifact is mandatory** in both of those modes: every `quote` and every
`literal_edit.old_text` is verified against it and any member whose anchor is not real text is
dropped from its cluster. It is resolved from the manifest's `artifact` path, and `--artifact`
overrides that; neither resolving is a usage error. Only `--render-only`, which re-renders from an
already-checked `reconciliation.json`, runs without it.

Exit codes: 0 success, meaning both files were written and every expected seat validated; 1 usage;
3 a judgment patch is required or could not be validated, or the run reconciled with a missing seat
— which is emitted at wrap-up, after both files are written with the missing seat recorded in them.
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
from lib import judge as judge_lib  # noqa: E402
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
    "majority": "several lenses, one family",
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

    corroborated = [c for c in by_severity["should-fix"] if c["n_families"] >= 2]
    single_seat = [c for c in by_severity["should-fix"] if c["n_families"] < 2]

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
            lines.append("- `{0}` ({1}) — {2}: {3}".format(seat["reviewer_id"], seat.get("family") or "?", seat["stage"], seat["reason"]))
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
            return json.load(handle)
    except (ValueError, OSError):
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

    config, _config_path = paths.config(args.config)
    config_entry = config.get(dispatch.PROVIDER) or {}
    if not config_entry:
        raise judge_lib.JudgeError("the config has no `{0}` provider entry".format(dispatch.PROVIDER))

    # **The seat Resolve worked out, model included, when the run recorded one.** The projection
    # that gated this run was computed against that seat, so re-deriving it here would price one
    # model and call another the moment a config cell was edited between the panel and the
    # judgment — the budget gate would have weighed a call nobody makes. The recorded `model` is
    # therefore passed to `prepare_call` as a **pin**, exactly as `--synthesis-model` is, rather
    # than left to the tier map to resolve a second time.
    #
    # A run made before `judge_seat` existed, or one reconciled by hand, falls back to resolving it
    # from the same inputs Resolve had.
    recorded = manifest.get("judge_seat")
    if isinstance(recorded, dict) and recorded.get("family") and recorded.get("tier"):
        seat = dict(recorded)
    else:
        panel = _panel_for(paths, manifest)
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

    call = dispatch.prepare_call(
        paths, family, tier=tier, model=pinned, max_tokens=cap,
        config_override=args.config, models_override=args.models, label="synthesis")

    request = core.judgment_request(context)
    system_prompt = dispatch.build_system_prompt(body, reference_paths)
    user_prompt = judge_lib.build_user_message(
        run_id=manifest.get("run_id"),
        request=request,
        reports=context["reports"],
        artifact_text=artifact_text,
        artifact_label=manifest.get("artifact") or "the artifact",
        artifact_revision=manifest.get("artifact_revision"),
        references=_materialized_references(run_dir, manifest))

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
            tier_source=seat.get("tier_source"), family_source=seat.get("family_source"))
        _record_judge_call(run_dir, record)

    return patch, record


def _author_holds_for_this_run(patch, patch_path, run_dir, reconciler, args):
    """Why this on-disk patch may not be merged as it stands, or None. A patch error, exit 3.

    The dispatched path assigns `author` from what this script called, so it cannot be disowned.
    The **on-disk** path had no such rule, and the two enforcement paths that make an unattended
    judge safe — `reconcile_core` rejecting `rulings` from `synthesis`, and forcing an
    all-`judgment-call` cluster to `flag-for-human` for that author alone — both key on that field.
    So a `judgment.json` a host session left in a directory, picked up by a resumed autonomous run,
    took the host's latitude on a run that has no host: a design fork disposed `fix-now`, with
    auto-apply's first condition open on it.

    The check is deliberately narrow. It fires only when the run's own record says `synthesis`
    judges **and** the patch was found at the default path, so the two ways of saying "a human wrote
    this one" — `--reconciler host` and an explicit `--judgment <path>` — both still take the host
    route, and both are named in the message rather than left to be guessed at.

    **The third guard is defence in depth and is unreachable today.** With `args.judgment` unset,
    `main` only ever binds `patch_path` to `<run-dir>/judgment.json` — either finding it there or
    writing it there after a dispatch — so the path comparison cannot fail. It is kept because this
    function is what stands between an unattended run and a patch claiming the host's latitude, and
    a security check that silently widens when somebody adds a third way to locate the patch is
    worse than one redundant comparison.
    """
    if reconciler != judge_lib.SYNTHESIS_AUTHOR:
        return None
    if args.judgment is not None:
        return None
    if os.path.abspath(patch_path) != os.path.join(os.path.abspath(run_dir), "judgment.json"):
        return None
    author = patch.get("author") if isinstance(patch, dict) else None
    if author == judge_lib.SYNTHESIS_AUTHOR:
        return None
    return (
        "the judgment patch at {0} says `author: {1!r}`, and this run's judgment is `synthesis`.\n"
        "  The author is not a label: it decides whether an all-`judgment-call` cluster may be\n"
        "  disposed anything but `flag-for-human`, and whether `rulings` is allowed at all. A patch\n"
        "  claiming `host` on a run with no host takes latitude nobody is answering for.\n"
        "  Nothing was written. If a human did write this patch, say so — re-run with\n"
        "  `--reconciler host`, or point at it explicitly with `--judgment {0}`.".format(
            patch_path, author))


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
                        help="The project holding the artifact. Schemas resolve from <workspace>/.agents/ensemble-review/schemas/ first, then the skill package (default: the working directory)")
    parser.add_argument("--reconciler", choices=judge_lib.RECONCILERS, default=None,
                        help="Who supplies the judgment patch (default: the manifest's, else `default` — host when a human is attached, synthesis when nobody is)")
    parser.add_argument("--autonomous", action="store_true",
                        help="Nobody is attached: `--reconciler default` resolves to synthesis. Also inferred when stdin is not a tty")
    parser.add_argument("--synthesis-model", default=None,
                        help="Pin the judgment call to a concrete model id, overriding the tier map")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="Completion cap for the judgment call; a model's registry floor raises it (default: the run's own cap, else {0})".format(registry_lib.DEFAULT_MAX_TOKENS))
    parser.add_argument("--config", default=None, help="Config JSON, taken as given (default: the workspace-first cascade, deep-merged)")
    parser.add_argument("--models", default=None, help="Model registry, taken as given (default: the workspace-first cascade)")
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
        default = os.path.join(run_dir, "judgment.json")
        patch_path = default if os.path.isfile(default) else None

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
        reconciler = judge_lib.resolve_reconciler(
            args.reconciler, run_manifest.get("reconciler"), autonomous)
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
        if reconciler != judge_lib.SYNTHESIS_AUTHOR:
            sys.stderr.write(
                "a judgment patch is required before anything can be written: answer every cluster in "
                "{0} against schemas/judgment-patch.schema.json, save it as {1}, and re-run with --judgment.\n".format(
                    request_path, os.path.join(run_dir, "judgment.json")))
            return 3

        if not context["seats_reporting"]:
            sys.stderr.write("zero seats reported: there is nothing for the judge to judge.\n")
            return EXIT_TERMINAL

        try:
            patch, _record = dispatch_synthesis(paths, run_dir, manifest, context, artifact_text, args)
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

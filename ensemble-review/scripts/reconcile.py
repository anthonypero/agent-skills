#!/usr/bin/env python3
"""Reconcile a run directory into `reconciliation.json` and `reconciliation.md`.

This script is the **only** writer of both files, in every mode. It computes the mechanical half of
the algorithm — normalized location equality, quote overlap, agreement tiers, the run-level verdict,
the final BK/SF/NH ids — and takes the semantic half from a judgment patch supplied by whichever mind
is available: the host session interactively, the `synthesis` persona autonomously.

    reconcile.py --run-dir <dir>                        # writes judgment-request.json, exits 3
    reconcile.py --run-dir <dir> --judgment judgment.json
    reconcile.py --run-dir <dir> --render-only          # re-render the Markdown from the JSON

Run it once with no patch to get the provisional clusters and the questions they raise, write the
patch against `schemas/judgment-patch.schema.json`, then run it again. Nothing is written when the
patch fails validation: the failing entries are named and the supplier is asked again.

Exit codes: 0 written; 1 usage; 3 a judgment patch is required or could not be validated.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import reconcile_core as core  # noqa: E402
from lib import report as report_lib  # noqa: E402
from lib import schema as schema_lib  # noqa: E402

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_DIR = os.path.join(SKILL_DIR, "schemas")

SEVERITY_ORDER = core.SEVERITY_ORDER


# --- validation of the two contracts ---------------------------------------------------------------

def validate_patch_document(patch):
    return schema_lib.validate(patch, schema_lib.load(os.path.join(SCHEMA_DIR, "judgment-patch.schema.json")))


def validate_reconciliation_document(document):
    """The top level against the schema, then every cluster against `definitions.cluster`."""
    loaded = schema_lib.load(os.path.join(SCHEMA_DIR, "reconciliation.schema.json"))
    errors = schema_lib.validate(document, loaded)
    cluster_schema = loaded["definitions"]["cluster"]
    for index, cluster in enumerate(document.get("clusters") or []):
        errors.extend(schema_lib.validate(cluster, cluster_schema, "$.clusters[{0}]".format(index)))
    return errors


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


# --- cli -----------------------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Reconcile a panel run directory into reconciliation.json and reconciliation.md.")
    parser.add_argument("--run-dir", required=True, help="The run directory holding manifest.json and one <reviewer-id>.json per seat")
    parser.add_argument("--judgment", help="The judgment patch. Defaults to <run-dir>/judgment.json when that file exists")
    parser.add_argument("--artifact", help="The pinned artifact, so every quote and old_text can be checked against real text")
    parser.add_argument("--render-only", action="store_true", help="Re-render reconciliation.md from an existing reconciliation.json and write nothing else")
    args = parser.parse_args(argv)

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
        errors = validate_reconciliation_document(document)
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

    artifact_text = None
    if args.artifact:
        with open(args.artifact, "r", encoding="utf-8") as handle:
            artifact_text = handle.read()

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
        if context["missing_seats"]:
            for seat in context["missing_seats"]:
                print("  missing seat {0}: {1} — {2}".format(seat["reviewer_id"], seat["stage"], seat["reason"]))
        sys.stderr.write(
            "a judgment patch is required before anything can be written: answer every cluster in "
            "{0} against schemas/judgment-patch.schema.json, save it as {1}, and re-run with --judgment.\n".format(
                request_path, os.path.join(run_dir, "judgment.json")))
        return 3

    with open(patch_path, "r", encoding="utf-8") as handle:
        patch = json.load(handle)

    shape_errors = validate_patch_document(patch)
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
    drops = document.pop("_anchor_drops", [])

    schema_errors = validate_reconciliation_document(document)
    if schema_errors:
        _print_errors("reconcile.py produced a document that does not validate; nothing was written", schema_errors)
        return 3

    report_lib.write_json(os.path.join(run_dir, "reconciliation.json"), document)
    report_lib.write_text(os.path.join(run_dir, "reconciliation.md"), render_markdown(document, context["reports"]))

    print("verdict: {0}".format(document["verdict"]))
    print("{0} clusters — {1}".format(
        document["counts"]["clusters"],
        ", ".join("{0} {1}".format(count, tier) for tier, count in sorted(document["counts"]["by_tier"].items()))))
    for drop in drops:
        print("  dropped {0} {1}: {2}".format(drop["reviewer_id"], drop["finding_id"], drop["reason"]))
    print("wrote {0} and {1}".format(
        os.path.join(run_dir, "reconciliation.json"), os.path.join(run_dir, "reconciliation.md")))
    return 0


def _safe_reports(run_dir):
    try:
        manifest = core.load_manifest(run_dir)
    except core.ReconcileError:
        return {}, []
    return core.load_reports(run_dir, manifest)


def _print_errors(headline, errors):
    sys.stderr.write("{0}:\n".format(headline))
    for error in errors:
        sys.stderr.write("  - {0}\n".format(error))


if __name__ == "__main__":
    sys.exit(main())

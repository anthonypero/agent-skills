#!/usr/bin/env python3
"""Validate and render a report a script did not write, so both legs leave the same artifacts.

    render_harness_report.py .agents/reviews/v1-spec/2026-09-18-1/fidelity-claude.json
    render_harness_report.py .../adversarial-glm.json --leg openrouter --tier frontier

The harness leg is the one seat that writes its own report: a subagent cannot return a full report
through a message that truncates, and cannot write to the session scratchpad, so it writes the JSON
into the run directory itself. This script closes the gap — it validates that file against the same
schema the OpenRouter leg is held to, stamps the leg and a `_meta` block, and writes the `.md`
rendering beside it. Exits non-zero, printing every validation error, when the report is invalid.

The other use is salvage: a hand-repaired OpenRouter report rendered through here used to come out
labelled `leg: harness, tier: standard`, which is a false audit record. `--leg` and `--tier` say what
the seat actually was; a `leg` or `_meta.tier` already in the JSON is believed over either flag,
since whatever wrote it knew more than this script does.
"""

import argparse
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from lib import report as report_lib  # noqa: E402


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Validate and render a harness-leg review report.")
    parser.add_argument("report", help="Path to the <lens>-claude.json a harness subagent wrote")
    parser.add_argument("--model", default=None, help="Concrete model the subagent ran on, for the audit record")
    parser.add_argument("--leg", choices=report_lib.LEGS, default="harness", help="Leg the report came from; a `leg` already in the JSON wins (default: harness)")
    parser.add_argument("--tier", default=None, help="Tier the seat ran at; a `_meta.tier` already in the JSON wins")
    parser.add_argument("--no-stamp", action="store_true", help="Validate and render without touching the JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if not os.path.isfile(args.report):
        sys.exit("Not a file: {0}".format(args.report))

    with open(args.report, "r", encoding="utf-8") as handle:
        text = handle.read()
    try:
        parsed = json.loads(report_lib.strip_fence(text))
    except ValueError as exc:
        sys.exit("{0} is not parseable JSON: {1}".format(args.report, exc))

    basename = os.path.basename(args.report)
    reviewer_id = basename[:-5] if basename.endswith(".json") else basename
    lens = reviewer_id.rsplit("-", 1)[0] if "-" in reviewer_id else reviewer_id

    existing_leg = parsed.get("leg") if parsed.get("leg") in report_lib.LEGS else None
    leg = existing_leg or args.leg
    if existing_leg and existing_leg != args.leg:
        sys.stderr.write("note: the report already says `leg: {0}`; keeping it over --leg {1}\n".format(existing_leg, args.leg))

    if not args.no_stamp:
        # The filename is authoritative for the audit fields, exactly as dispatch.py's own values are
        # on the OpenRouter leg: a subagent that mislabelled its report cannot corrupt the record. The
        # leg is the exception — only the caller knows it, and the file itself knows better still.
        parsed["schema_version"] = "1"
        parsed["reviewer_id"] = reviewer_id
        parsed["lens"] = lens
        parsed["family"] = reviewer_id.rsplit("-", 1)[-1] if "-" in reviewer_id else "claude"
        parsed["leg"] = leg
        if args.model:
            parsed["model"] = args.model
        parsed.setdefault("model", "harness-subagent" if leg == "harness" else "unknown")

    errors = report_lib.validate_report(parsed, lens=parsed.get("lens"), ingest=True)
    if errors:
        sys.stderr.write("{0} failed validation:\n".format(args.report))
        for error in errors:
            sys.stderr.write("  - {0}\n".format(error))
        sys.stderr.write("Send these back to the subagent as a repair re-ask, or retire the seat and record it "
                         "as missing in the manifest and the reconciliation.\n")
        return 3

    meta = parsed.get("_meta") or {}
    if meta.get("tier") and args.tier and meta["tier"] != args.tier:
        sys.stderr.write("note: the report already says `_meta.tier: {0}`; keeping it over --tier {1}\n".format(meta["tier"], args.tier))
    meta.setdefault("provider", "harness" if leg == "harness" else "openrouter")
    # `standard` is the harness leg's own tier — the subscription model the subagent runs on. It is
    # not a safe guess for any other leg, so an unknown tier stays unset rather than being invented.
    tier = meta.get("tier") or args.tier or ("standard" if leg == "harness" else None)
    if tier:
        meta["tier"] = tier
    parsed["_meta"] = meta

    if not args.no_stamp:
        report_lib.write_json(args.report, parsed)

    md_path = args.report[:-5] + ".md" if args.report.endswith(".json") else args.report + ".md"
    report_lib.write_text(md_path, report_lib.render_markdown(parsed))

    print(report_lib.make_digest(parsed, args.report))
    print("")
    print("Rendered: {0}".format(md_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())

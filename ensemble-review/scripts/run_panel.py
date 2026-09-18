#!/usr/bin/env python3
"""Run every seat of a panel in parallel and write the run manifest.

    run_panel.py --panel spec-review \
                 --artifact pm/technical-requirements.md \
                 --ref pm/prd.md \
                 --out .agents/reviews/technical-requirements/2026-09-18-1

Each seat is one `dispatch.py` subprocess, so a seat that fails takes only its own seat down:
the panel flags it and advances, and the manifest records the failure alongside the reports.

`--skip-claude` leaves the claude seats to the host session, which spawns them as harness
subagents with the same persona body. Those seats are recorded in the manifest as pending on the
harness leg. It is off by default: without it every seat, claude included, runs through OpenRouter.
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from lib import report as report_lib  # noqa: E402

DEFAULT_CONFIG = os.path.join(SKILL_DIR, "templates", "config.json")
PANELS_DIR = os.path.join(SKILL_DIR, "templates", "panels")
DISPATCH = os.path.join(SCRIPTS_DIR, "dispatch.py")


def load_panel(name_or_path):
    if os.path.isfile(name_or_path):
        path = name_or_path
    else:
        candidate = name_or_path if name_or_path.endswith(".json") else name_or_path + ".json"
        path = os.path.join(PANELS_DIR, candidate)
    if not os.path.isfile(path):
        available = sorted(n[:-5] for n in os.listdir(PANELS_DIR) if n.endswith(".json"))
        sys.exit("No panel `{0}`. Available: {1}".format(name_or_path, ", ".join(available)))
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle), path


def resolve_seat_models(panel, config_path, default_tier):
    """Resolve each seat's tier and concrete model up front, so the manifest is written even on failure."""
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    entry = config.get("openrouter") or {}
    tiers = entry.get("tiers") or {}
    resolved = []
    for seat in panel.get("seats", []):
        # Precedence: --tier on the command line, then the seat's own tier, then the panel's, then
        # the config default. The CLI wins so an operator can force a whole panel cheap for a dry run.
        tier = default_tier or seat.get("tier") or panel.get("tier") or entry.get("default_tier") or "frontier"
        model = seat.get("model") or (tiers.get(tier) or {}).get(seat.get("family"))
        resolved.append({
            "lens": seat.get("lens"),
            "family": seat.get("family"),
            "tier": tier,
            "model": model,
            "reviewer_id": "{0}-{1}".format(seat.get("lens"), seat.get("family")),
        })
    return resolved


def run_seat(seat, args):
    command = [
        sys.executable, DISPATCH,
        "--persona", "lens-" + seat["lens"],
        "--family", seat["family"],
        "--artifact", args.artifact,
        "--out", args.out,
        "--config", args.config,
        "--tier", seat["tier"],
    ]
    for ref in args.refs:
        command += ["--ref", ref]
    if seat.get("model"):
        command += ["--model", seat["model"]]
    if args.max_tokens:
        command += ["--max-tokens", str(args.max_tokens)]

    started = time.time()
    result = subprocess.run(command, capture_output=True, text=True)
    return {
        "seat": seat,
        "returncode": result.returncode,
        "digest": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "elapsed_s": round(time.time() - started, 1),
    }


def attempt_summary(meta):
    """(attempt count, first attempt's outcome) as the manifest reports them, from a `_meta` block."""
    attempts = meta.get("attempts") or []
    if not attempts:
        return None, None
    return len(attempts), attempts[0].get("errors")


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run a review panel: one dispatch per seat, in parallel.")
    parser.add_argument("--panel", default="spec-review", help="Panel template name in templates/panels/, or a path to one")
    parser.add_argument("--artifact", required=True, help="Path to the document under review")
    parser.add_argument("--ref", action="append", default=[], dest="refs", help="Path to a source-of-truth reference; repeat for several")
    parser.add_argument("--out", required=True, help="Run directory")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config JSON (default: the skill's templates/config.json)")
    parser.add_argument("--tier", default=None, help="Tier for every seat that does not set its own (default: the panel's, then the config's)")
    parser.add_argument("--max-tokens", type=int, default=None, help="Completion cap passed to every seat")
    parser.add_argument("--skip-claude", action="store_true", help="Leave claude seats to the host session's harness subagents")
    parser.add_argument("--run-id", default=None, help="Recorded in the manifest; defaults to the run directory's basename")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    panel, panel_path = load_panel(args.panel)
    if panel.get("requires_references") and not args.refs:
        sys.stderr.write("warning: panel `{0}` expects source-of-truth references and none were supplied; "
                         "a fidelity seat cannot do its job without them\n".format(panel.get("name", args.panel)))

    if not os.path.isfile(args.artifact):
        sys.exit("Not a file: {0}".format(args.artifact))
    for ref in args.refs:
        if not os.path.isfile(ref):
            sys.exit("Not a file: {0}".format(ref))
    if not os.path.isdir(args.out):
        os.makedirs(args.out)

    seats = resolve_seat_models(panel, args.config, args.tier)
    dispatched = [s for s in seats if not (args.skip_claude and s["family"] == "claude")]
    harness = [s for s in seats if args.skip_claude and s["family"] == "claude"]

    started = time.time()
    results = []
    if dispatched:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(dispatched)) as pool:
            futures = [pool.submit(run_seat, seat, args) for seat in dispatched]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    elapsed = time.time() - started

    manifest_seats = []
    failures = []
    total_cost = 0.0
    cost_known = False

    for seat in harness:
        manifest_seats.append({
            "reviewer_id": seat["reviewer_id"],
            "lens": seat["lens"],
            "family": seat["family"],
            "tier": seat["tier"],
            "model": None,
            "leg": "harness",
            "status": "pending",
            "report": os.path.join(args.out, seat["reviewer_id"] + ".json"),
            "note": "dispatched by the host session as a harness subagent; render with render_harness_report.py",
        })

    by_id = {}
    for result in results:
        seat = result["seat"]
        report_path = os.path.join(args.out, seat["reviewer_id"] + ".json")
        record = {
            "reviewer_id": seat["reviewer_id"],
            "lens": seat["lens"],
            "family": seat["family"],
            "tier": seat["tier"],
            "model": seat["model"],
            "leg": "openrouter",
            "status": "ok" if result["returncode"] == 0 else "failed",
            "report": report_path if result["returncode"] == 0 else None,
            "elapsed_s": result["elapsed_s"],
        }
        if result["returncode"] == 0 and os.path.isfile(report_path):
            with open(report_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            meta = data.get("_meta") or {}
            record["verdict"] = data.get("verdict")
            record["findings"] = len(data.get("findings") or [])
            record["repairs"] = meta.get("repairs")
            record["attempts"], record["first_attempt_errors"] = attempt_summary(meta)
            record["truncated"] = meta.get("truncated")
            record["usage"] = meta.get("usage")
            record["reasoning_tokens"] = meta.get("reasoning_tokens")
            record["cost_usd"] = meta.get("cost_usd")
            if meta.get("cost_usd") is not None:
                total_cost += float(meta["cost_usd"])
                cost_known = True
        else:
            record["error"] = result["stderr"][-2000:]
            # A failed seat still spent money and still has a story. dispatch.py leaves its attempt
            # record beside the raw response, so the manifest carries the cost rather than showing $0.
            failed_path = os.path.join(args.out, seat["reviewer_id"] + ".failed.json")
            if os.path.isfile(failed_path):
                with open(failed_path, "r", encoding="utf-8") as handle:
                    failed = json.load(handle)
                meta = failed.get("_meta") or {}
                record["validation_errors"] = failed.get("errors")
                record["raw_response"] = failed.get("raw_response")
                record["attempt_record"] = failed_path
                record["repairs"] = meta.get("repairs")
                record["attempts"], record["first_attempt_errors"] = attempt_summary(meta)
                record["usage"] = meta.get("usage")
                record["reasoning_tokens"] = meta.get("reasoning_tokens")
                record["cost_usd"] = meta.get("cost_usd")
                if meta.get("cost_usd") is not None:
                    total_cost += float(meta["cost_usd"])
                    cost_known = True
            failures.append(seat["reviewer_id"])
        manifest_seats.append(record)
        by_id[seat["reviewer_id"]] = result

    manifest = {
        "run_id": args.run_id or os.path.basename(os.path.abspath(args.out)),
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "panel": panel.get("name", args.panel),
        "panel_path": panel_path,
        "artifact": args.artifact,
        "references": args.refs,
        "config": args.config,
        "tier_default": args.tier or panel.get("tier"),
        "skip_claude": args.skip_claude,
        "seats": manifest_seats,
        "families_dispatched": sorted({s["family"] for s in dispatched}),
        "min_families_target": panel.get("min_families"),
        "elapsed_s": round(elapsed, 1),
        "cost_usd_total": round(total_cost, 6) if cost_known else None,
        "failures": failures,
    }
    records_by_id = {record["reviewer_id"]: record for record in manifest_seats}
    manifest_path = os.path.join(args.out, "manifest.json")
    report_lib.write_json(manifest_path, manifest)

    print("Panel `{0}` on {1}".format(manifest["panel"], args.artifact))
    print("Run directory: {0}".format(os.path.abspath(args.out)))
    print("Seats dispatched: {0} · harness pending: {1} · failed: {2} · {3:.0f}s{4}".format(
        len(dispatched), len(harness), len(failures), elapsed,
        " · ${0:.4f}".format(total_cost) if cost_known else ""))
    print("")
    for seat in dispatched:
        result = by_id.get(seat["reviewer_id"])
        print("-" * 72)
        if result and result["returncode"] == 0:
            print(result["digest"])
        else:
            record = records_by_id.get(seat["reviewer_id"]) or {}
            spent = " · spent ${0:.4f} anyway across {1} attempt(s)".format(record["cost_usd"], record.get("attempts") or 0) if record.get("cost_usd") is not None else ""
            print("{0} [{1}] FAILED{2}".format(seat["reviewer_id"], seat["model"], spent))
            if result:
                print(result["stderr"][-600:])
    for seat in harness:
        print("-" * 72)
        print("{0} — pending on the harness leg; spawn the subagent, then render it".format(seat["reviewer_id"]))
    print("-" * 72)
    print("Manifest: {0}".format(manifest_path))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

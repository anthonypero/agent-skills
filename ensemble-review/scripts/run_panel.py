#!/usr/bin/env python3
"""Run every seat of a panel in parallel and write the run manifest.

    run_panel.py --panel spec-review \
                 --artifact pm/technical-requirements.md \
                 --ref pm/prd.md \
                 --out .agents/reviews/technical-requirements/2026-09-18-1

Each seat is one `dispatch.py` subprocess, so a seat that fails takes only its own seat down:
the panel flags it and advances, and the manifest records the failure alongside the reports.

The run lifecycle, in the order the stages run:

- **Claim** — the run directory is created with an atomic exclusive `mkdir`. On collision the run
  id's sequence number increments and the claim retries, so two concurrent runs on one artifact can
  never write into the same directory.
- **Materialize** — the artifact and every reference are copied into `<run-dir>/inputs/`, read-only,
  and each one's revision is the SHA-256 of the bytes written there. Seats read those bytes, never
  the working tree: a mid-run edit must not give two seats two different documents.
- **Resolve** — seats resolve to families, tiers, models and connectors, and the registry is checked
  to cover every one of them. **The manifest is written here, before the first dispatch**, with every
  seat `pending`, so a crash after this point leaves a manifest that knows how many seats there were.
- **Project** — the cost and token pre-flight, always printed and always labelled an estimate. Over
  budget: refuse and exit 4 when nobody can be asked, otherwise ask on stdin.
- **Dispatch** — each seat's record moves `pending -> dispatching` by compare-and-set before its
  first paid call, and is updated as it returns.

Every file the run loads — the panel, the config, the registry, the personas, the finding schema, the
backend driver — resolves through `lib/paths.py`'s two-root cascade: `<workspace>/.agents/
ensemble-review/` first, then the skill package, which is read-only and is never written to. Files
replace whole; `config.json` deep-merges. The root each one came from lands in the manifest's `roots`.

Seats resolve through `lib/seating.py`: the tier order (`--model`, `--tier`, the seat, the panel, the
config, the persona frontmatter) and the three constraint passes (named, `non-claude`, `distinct`).
A family with no cell at the resolved tier, and a family the provider refuses at dispatch time with a
404 or 400 naming the model, are the same condition — unreachable — and both re-seat that lens onto
the next available family **once**, recording a `substitution` the reconciliation's method caveat
then names.

Re-running against an existing run directory **resumes** it: only seats whose reports are absent or
invalid are re-dispatched, a seat already `dispatching` is left alone and reported as held, and a run
whose inputs no longer hash to the manifest's fingerprint is refused rather than mixed. `--fresh`
forces a new claim instead.

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
import threading
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from lib import budget as budget_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402
from lib import runs as runs_lib  # noqa: E402
from lib import seating as seating_lib  # noqa: E402

DISPATCH = os.path.join(SCRIPTS_DIR, "dispatch.py")
FINDING_SCHEMA = "finding-schema.md"
PROVIDER = "openrouter"

DEFAULT_BUDGET_USD = 5.00

EXIT_OK = 0
EXIT_COMPOSITION = 1
EXIT_TERMINAL = 2
EXIT_UNDER_SEATED = 3
EXIT_BUDGET = 4

# dispatch.py's own codes, read back from the subprocess.
DISPATCH_AUTH = 2
DISPATCH_MODEL_UNAVAILABLE = 5


def load_panel(name_or_path, paths):
    """A panel template: an operator path when `--panel` names a file, else the workspace cascade."""
    if os.path.isfile(name_or_path):
        path = os.path.abspath(name_or_path)
        paths.note_operator_path("panel", path)
    else:
        name = name_or_path[:-5] if name_or_path.endswith(".json") else name_or_path
        try:
            path = paths.panel(name)
        except paths_lib.PathError as failure:
            sys.exit("{0}\n  Panels available: {1}".format(failure, ", ".join(available_panels(paths)) or "none"))
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle), path


def available_panels(paths):
    """Every panel name either root offers, workspace first, each name listed once."""
    names = []
    for root in paths.roots:
        for template in paths_lib.CANDIDATES["panel"]:
            directory = os.path.dirname(os.path.join(root, template))
            if not os.path.isdir(directory):
                continue
            for entry in sorted(os.listdir(directory)):
                if entry.endswith(".json") and entry[:-5] not in names:
                    names.append(entry[:-5])
    return names


def persona_chars(lens, paths):
    """Characters in this lens's system message: the persona body plus the finding schema."""
    total = 0
    persona = paths.find("persona", "lens-" + lens)
    if persona:
        _frontmatter, body = report_lib.parse_agent_file(persona["path"])
        total += len(body)
    reference = paths.find("reference", FINDING_SCHEMA)
    if reference:
        total += os.path.getsize(reference["path"])
    return total


def persona_frontmatter(paths):
    """`frontmatter_fn` for `seating.resolve`: the lowest-precedence tier source, when it names one."""
    def read(lens):
        found = paths.require("persona", "lens-" + lens)
        frontmatter, _body = report_lib.parse_agent_file(found["path"])
        return frontmatter
    return read


def parse_model_pins(values):
    """`--model <seat-id>=<model-id>`, repeatable, into `{seat id: model id}`."""
    pinned = {}
    for value in values or []:
        seat_id, sep, model = value.partition("=")
        if not sep or not seat_id.strip() or not model.strip():
            raise seating_lib.SeatingError(
                "--model takes <seat-id>=<model-id>, e.g. --model fidelity-openai=openai/gpt-6-astra; "
                "got {0!r}".format(value))
        pinned[seat_id.strip()] = model.strip()
    return pinned


def run_seat(seat, args, run_dir, inputs, halt):
    """One `dispatch.py` subprocess, claimed in the manifest before it can spend anything."""
    if halt.is_set():
        return {"seat": seat, "returncode": None, "skipped": "halted", "digest": "", "stderr": "", "elapsed_s": 0.0}

    claimed, seen = runs_lib.claim_seat(run_dir, seat["reviewer_id"])
    if not claimed:
        # The only status that legitimately blocks a claim between partition and dispatch is
        # `dispatching`: another process took the seat in the interval. Anything else is a manifest
        # this code does not understand, and saying "another process owns this seat" about it would
        # be a false reason on the console — the failure mode the partition step exists to close.
        why = ("already `dispatching` — another process owns this seat" if seen == "dispatching"
               else "not claimable: the manifest moved it to `{0}` after this run selected it".format(seen))
        return {"seat": seat, "returncode": None, "skipped": "held", "held_status": seen, "held_reason": why,
                "digest": "", "stderr": "", "elapsed_s": 0.0}

    command = [
        sys.executable, DISPATCH,
        "--persona", "lens-" + seat["lens"],
        "--family", seat["family"],
        "--reviewer-id", seat["reviewer_id"],
        "--artifact", inputs["artifact"]["materialized_abs"],
        "--artifact-name", inputs["artifact"]["path"],
        "--artifact-revision", inputs["artifact"]["revision"],
        "--out", run_dir,
        "--workspace", args.workspace or os.getcwd(),
        "--tier", seat["tier"],
        "--max-tokens", str(args.max_tokens),
    ]
    if args.config:
        command += ["--config", args.config]
    if args.models:
        command += ["--models", args.models]
    for record in inputs["references"]:
        command += ["--ref", record["materialized_abs"], "--ref-name", record["path"], "--ref-revision", record["revision"]]
    if seat.get("model"):
        command += ["--model", seat["model"]]

    started = time.time()
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == DISPATCH_AUTH:
        # Framework §20: auth failures are not transient. No seat that has not started may start.
        halt.set()
    return {
        "seat": seat,
        "returncode": result.returncode,
        "digest": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "elapsed_s": round(time.time() - started, 1),
    }


def seat_record_from_report(record, report_path):
    """Fold a landed report's `_meta` into the manifest seat record."""
    with open(report_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    meta = data.get("_meta") or {}
    record["status"] = "ok"
    record["report"] = report_path
    # A seat that has now reported is not carrying last dispatch's failure any more. The substitution
    # stays — it is what this seat ran on — but the reason the earlier dispatch failed does not.
    record.pop("failure_reason", None)
    record.pop("error", None)
    record["verdict"] = data.get("verdict")
    record["findings"] = len(data.get("findings") or [])
    _fold_meta(record, meta)
    return record


def _fold_meta(record, meta):
    """Fold one dispatch's `_meta` into the seat record, **accumulating** across dispatches.

    A seat can be dispatched more than once: resume re-dispatches one whose report went missing or
    stopped validating, and that first dispatch was paid for. Overwriting the record would drop that
    spend out of the seat and out of `cost_usd_total` — a $1.00 seat re-dispatched at $1.00 would
    read as $1.00 against $2.00 spent, which is the same under-reporting the failed-seat accounting
    was built to close, one level up.

    So the seat's `attempts` is the whole history across dispatches, each entry tagged with the
    `dispatch` that made it; `cost_usd`, `reasoning_tokens`, `repairs` and `truncations` are sums
    over all of them. `usage`, `max_tokens`, `effort` and `model` describe the latest dispatch,
    because they are facts about one call rather than totals.
    """
    prior = list(record.get("attempts") or [])
    dispatch_n = max([attempt.get("dispatch", 1) for attempt in prior] or [0]) + 1
    landed = [dict(attempt, dispatch=dispatch_n) for attempt in (meta.get("attempts") or [])]

    record["model"] = meta.get("model") or record.get("model")
    record["connector"] = meta.get("connector") or record.get("connector")
    record["provider"] = meta.get("provider") or record.get("provider")
    record["effort"] = meta.get("effort")
    record["max_tokens"] = meta.get("max_tokens")
    record["dispatches"] = dispatch_n
    record["attempts"] = prior + landed
    record["repairs"] = _accumulate(record.get("repairs"), meta.get("repairs"))
    record["truncations"] = _accumulate(record.get("truncations"), meta.get("length_truncations"))
    record["string_truncations"] = _accumulate(record.get("string_truncations"), len(meta.get("truncated") or []))
    record["usage"] = meta.get("usage")
    record["reasoning_tokens"] = _accumulate(record.get("reasoning_tokens"), meta.get("reasoning_tokens"))
    record["cost_usd"] = _accumulate(record.get("cost_usd"), meta.get("cost_usd"))


def _accumulate(before, now):
    """Sum two optional numbers. None only when neither dispatch reported one."""
    values = [value for value in (before, now) if value is not None]
    return sum(values) if values else None


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run a review panel: one dispatch per seat, in parallel.")
    parser.add_argument("--panel", default="spec-review", help="Panel template name in templates/panels/, or a path to one")
    parser.add_argument("--artifact", required=True, help="Path to the document under review")
    parser.add_argument("--ref", action="append", default=[], dest="refs", help="Path to a source-of-truth reference; repeat for several")
    parser.add_argument("--out", required=True, help="Run directory")
    parser.add_argument("--workspace", default=None,
                        help="The project holding the artifact. Every file resolves from <workspace>/.agents/ensemble-review/ first and the skill package second; config.json deep-merges rather than replacing (default: the working directory)")
    parser.add_argument("--config", default=None, help="Config JSON, taken as given (default: the workspace-first cascade, deep-merged)")
    parser.add_argument("--models", default=None, help="Model registry, taken as given (default: the workspace-first cascade)")
    parser.add_argument("--tier", default=None, help="Tier for every seat that does not set its own (default: the panel's, then the config's)")
    parser.add_argument("--model", action="append", default=[], dest="models_pinned", metavar="SEAT=MODEL",
                        help="Pin one seat to a concrete model id, e.g. --model fidelity-openai=openai/gpt-6-astra. Repeatable; beats every tier source")
    parser.add_argument("--max-tokens", type=int, default=registry_lib.DEFAULT_MAX_TOKENS,
                        help="Completion cap sent on every call; a model's registry floor raises it for that seat (default: {0})".format(registry_lib.DEFAULT_MAX_TOKENS))
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
                        help="Pre-flight budget (default: {0:.2f})".format(DEFAULT_BUDGET_USD))
    parser.add_argument("--approve-budget", action="store_true", help="Dispatch even when the projection exceeds the budget")
    parser.add_argument("--autonomous", action="store_true", help="Nobody to ask: an over-budget projection refuses and exits 4 instead of prompting")
    parser.add_argument("--reconciler", choices=("host", "synthesis", "default"), default=None,
                        help="Who supplies the judgment patch (default: the panel's, else `default` — host interactive, synthesis autonomous). Decides whether the projection charges for a synthesis call")
    parser.add_argument("--fresh", action="store_true", help="Claim a new run directory instead of resuming an existing one")
    parser.add_argument("--skip-claude", action="store_true", help="Leave claude seats to the host session's harness subagents")
    parser.add_argument("--run-id", default=None, help="Recorded in the manifest; defaults to the run directory's basename")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    paths = paths_lib.Paths(args.workspace)
    panel, panel_path = load_panel(args.panel, paths)
    if panel.get("requires_references") and not args.refs:
        sys.stderr.write("warning: panel `{0}` expects source-of-truth references and none were supplied; "
                         "a fidelity seat cannot do its job without them\n".format(panel.get("name", args.panel)))

    if not os.path.isfile(args.artifact):
        sys.exit("Not a file: {0}".format(args.artifact))
    for ref in args.refs:
        if not os.path.isfile(ref):
            sys.exit("Not a file: {0}".format(ref))

    # --- Claim, or resume ------------------------------------------------------------------------
    resuming = False
    if os.path.isdir(args.out) and os.path.isfile(runs_lib.manifest_path(args.out)) and not args.fresh:
        run_dir = os.path.abspath(args.out)
        resuming = True
    else:
        try:
            run_dir = runs_lib.claim_run_dir(args.out)
        except runs_lib.RunError as failure:
            sys.stderr.write("{0}\n".format(failure))
            return EXIT_COMPOSITION
        if os.path.abspath(run_dir) != os.path.abspath(args.out):
            print("run directory {0} was already claimed; this run is {1}".format(args.out, run_dir))

    # --- Resolve, part one: the cascade, the config and the seats ---------------------------------
    # Seats resolve before anything is materialized, so a composition error costs nothing and leaves
    # the run directory empty rather than half-pinned.
    try:
        config, config_path = paths.config(args.config)
        config_entry = config.get(PROVIDER) or {}
        if not config_entry:
            sys.stderr.write("config {0} has no `{1}` provider entry\n".format(config_path, PROVIDER))
            return EXIT_COMPOSITION
        seats = seating_lib.resolve(
            panel, config_entry,
            cli_tier=args.tier,
            pinned=parse_model_pins(args.models_pinned),
            frontmatter_fn=persona_frontmatter(paths))
        paths.driver_ref(config_entry.get("type", "openai_compat"))
        # Every seat's system message carries it, so it is required, and its root belongs in the
        # audit record: a project that overrides the finding schema has changed what every reviewer
        # was asked for, which is the last thing `roots` should be silent about.
        paths.require("reference", FINDING_SCHEMA)
    except (paths_lib.PathError, seating_lib.SeatingError) as failure:
        sys.stderr.write("composition error: {0}\n".format(failure))
        return EXIT_COMPOSITION

    if resuming:
        _adopt_recorded_substitutions(runs_lib.read_manifest(run_dir), seats)

    for seat in seats:
        if seat.get("substitution"):
            print("re-seated {0}: {1}".format(seat["reviewer_id"], seat["substitution"]["reason"]))

    dispatched = [s for s in seats if not (args.skip_claude and s["family"] == "claude")]
    harness = [s for s in seats if args.skip_claude and s["family"] == "claude"]
    tier_default = args.tier or panel.get("tier")

    # --- Materialize -----------------------------------------------------------------------------
    # On a resume the fingerprint is checked BEFORE anything is written: materializing first would
    # overwrite the pinned copies of the very run the mismatch is about to refuse.
    sources = [args.artifact] + args.refs
    try:
        prospective = runs_lib.preview(sources, label_fn=_repo_relative)
    except runs_lib.RunError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_COMPOSITION
    fingerprint = runs_lib.input_fingerprint(prospective[0], prospective[1:], panel.get("name", args.panel), tier_default)

    if resuming:
        previous = runs_lib.read_manifest(run_dir).get("input_fingerprint")
        if previous and previous != fingerprint:
            sys.stderr.write(
                "resume refused: the inputs no longer match this run.\n"
                "  manifest fingerprint: {0}\n"
                "  inputs now hash to:   {1}\n"
                "Two documents' reviews must not share a run directory. Start a new run id.\n".format(previous, fingerprint))
            return EXIT_COMPOSITION

    try:
        records = runs_lib.materialize(run_dir, sources, label_fn=_repo_relative)
    except runs_lib.RunError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_COMPOSITION
    artifact_record, reference_records = records[0], records[1:]
    for record in records:
        record["materialized_abs"] = os.path.join(run_dir, record["materialized"])
    inputs = {"artifact": artifact_record, "references": reference_records}

    # --- Resolve, part two: the registry gate -----------------------------------------------------
    try:
        registry = registry_lib.load(paths.registry(args.models))
    except (paths_lib.PathError, registry_lib.RegistryError) as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_COMPOSITION
    uncovered = registry.covers([s["model"] for s in dispatched])
    if uncovered:
        sys.stderr.write("composition error: the model registry cannot price {0} seat(s).\n".format(len(uncovered)))
        for model, reason in uncovered:
            seat = next(s for s in dispatched if s["model"] == model)
            sys.stderr.write("  {0} resolves to {1}, which {2} at {3}\n".format(
                seat["reviewer_id"], model, reason, registry.path))
        sys.stderr.write("Every resolved seat must be priced before dispatch: a seat left out of the "
                         "projection is a budget gate that does not gate. Fix:\n")
        for model, _reason in uncovered:
            sys.stderr.write("  python3 scripts/refresh_models.py --add {0}\n".format(model))
        return EXIT_COMPOSITION

    # The effort a seat is dispatched at is a property of the comparison, not a preference: a value
    # the model does not accept is refused here rather than dropped on the way to the provider.
    effort_problems = seating_lib.effort_errors(dispatched, registry)
    if effort_problems:
        sys.stderr.write("composition error: the config asks for an effort {0} model(s) do not accept.\n".format(
            len(effort_problems)))
        for problem in effort_problems:
            sys.stderr.write("  {0}\n".format(problem))
        sys.stderr.write("Fix the config's `effort` map, or refresh the vocabularies:\n"
                         "  python3 scripts/refresh_models.py\n")
        return EXIT_COMPOSITION

    manifest = _build_manifest(args, panel, panel_path, config_path, paths, run_dir, seats,
                               dispatched, harness, inputs, fingerprint, tier_default, registry)
    if resuming:
        manifest = _merge_resume(manifest, runs_lib.read_manifest(run_dir))
    runs_lib.write_manifest(run_dir, manifest)

    # --- Project ---------------------------------------------------------------------------------
    document_chars = artifact_record["bytes"] + sum(r["bytes"] for r in reference_records)
    projection_seats = [{
        "reviewer_id": seat["reviewer_id"],
        "model": seat["model"],
        "prompt_tokens": budget_lib.approx_tokens(budget_lib.prompt_chars(persona_chars(seat["lens"], paths), document_chars)),
    } for seat in dispatched]
    # `default` means host interactive, synthesis autonomous — so whether this run pays for a
    # synthesis call is decided by the same two facts the budget gate itself turns on.
    autonomous = args.autonomous or not sys.stdin.isatty()
    reconciler = args.reconciler or panel.get("reconciler") or "default"
    with_synthesis = reconciler == "synthesis" or (reconciler == "default" and autonomous)

    projection = budget_lib.project(projection_seats, registry, args.budget_usd, with_synthesis=with_synthesis)
    print(budget_lib.render(projection))
    print("")

    overflowed = set(projection["context_overflows"])
    if overflowed:
        for reviewer_id in sorted(overflowed):
            runs_lib.update_seat(run_dir, reviewer_id, lambda seat: seat.update({
                "status": "failed",
                "failure_reason": "context-overflow",
                "error": "the composed prompt exceeds this model's context limit; never re-seated, the run is under-seated",
            }))
        dispatched = [s for s in dispatched if s["reviewer_id"] not in overflowed]

    if budget_lib.over_budget(projection) and not args.approve_budget:
        if autonomous:
            refusal_path = os.path.join(run_dir, "budget-refusal.json")
            runs_lib.write_json_atomic(refusal_path, budget_lib.refusal_document(projection))
            sys.stderr.write(
                "autonomous run over budget: projected ${0:.2f} against ${1:.2f}, short by ${2:.2f}. "
                "No paid call was made.\n  wrote {3}\n"
                "  raise --budget-usd, pass --approve-budget, or compose a cheaper panel.\n".format(
                    projection["projection_usd"], projection["budget_usd"], projection["shortfall_usd"], refusal_path))
            return EXIT_BUDGET
        answer = input("This projection exceeds the budget. Dispatch anyway? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            sys.stderr.write("not dispatched.\n")
            return EXIT_BUDGET

    # --- Dispatch --------------------------------------------------------------------------------
    to_dispatch, kept, held = _partition_for_dispatch(run_dir, dispatched, resuming)
    for reviewer_id, why in kept:
        print("keeping {0}: {1}".format(reviewer_id, why))
    for reviewer_id, why in held:
        print("holding {0}: {1}".format(reviewer_id, why))

    halt = threading.Event()
    started = time.time()
    results = _fan_out(to_dispatch, args, run_dir, inputs, halt)

    # A family the provider will not serve is unreachable for this run, which is the same condition
    # as a family with no cell at this tier — so it takes the same treatment: re-seat that lens onto
    # the next available family, **once**, and record the substitution. Done after the fan-out rather
    # than inside it, so the seats that did land are already holding their families when the
    # replacement is chosen.
    replacements = _reseat_unavailable(results, seats, config_entry, registry, run_dir, args)
    if replacements:
        landed = _fan_out(replacements, args, run_dir, inputs, halt)
        by_id = {r["seat"]["reviewer_id"]: r for r in results}
        by_id.update({r["seat"]["reviewer_id"]: r for r in landed})
        results = list(by_id.values())
    elapsed = time.time() - started

    return _wrap_up(args, run_dir, results, harness, dispatched, elapsed, halt, held)


def _fan_out(seats, args, run_dir, inputs, halt):
    """One `dispatch.py` subprocess per seat, in parallel. Returns one result record per seat."""
    results = []
    if not seats:
        return results
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(seats)) as pool:
        futures = [pool.submit(run_seat, seat, args, run_dir, inputs, halt) for seat in seats]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())
    return results


def _reseat_unavailable(results, seats, config_entry, registry, run_dir, args):
    """Move every seat the provider refused onto another family, once each. Returns those seats.

    Each seat's first, failed dispatch is folded into the manifest **before** the move, so its spend
    and its reason survive the re-seat: a seat that cost money on a model the provider would not
    serve is still a seat that cost money.
    """
    moved = []
    for result in results:
        if result.get("returncode") != DISPATCH_MODEL_UNAVAILABLE:
            continue
        seat = result["seat"]
        refused_model = seat["model"]
        runs_lib.update_seat(run_dir, seat["reviewer_id"], _unavailable(result, refused_model))
        replacement = seating_lib.reseat(
            seat, seats, config_entry, kind="model_unavailable",
            reason="the provider would not serve {0}; re-seated once onto the next available "
                   "family in declaration order".format(refused_model),
            usable=lambda _family, model: bool(model) and not registry.covers([model]))
        if replacement is None:
            print("{0}: no family left to re-seat onto; the run is under-seated".format(seat["reviewer_id"]))
            continue
        entry = registry.get(seat["model"]) or {}
        runs_lib.update_seat(run_dir, seat["reviewer_id"], _reseated(seat, entry, args.max_tokens))
        print("re-seated {0}: {1}".format(seat["reviewer_id"], seat["substitution"]["reason"]))
        moved.append(seat)
    return moved


def _unavailable(result, model):
    def mutate(record):
        record["status"] = "failed"
        record["report"] = None
        record["failure_reason"] = "model-unavailable"
        record["error"] = (result.get("stderr") or "")[-2000:]
        record["elapsed_s"] = result.get("elapsed_s")
        record["unavailable_model"] = model
    return mutate


def _reseated(seat, entry, run_cap):
    def mutate(record):
        record["status"] = "failed"
        record["family"] = seat["family"]
        record["model"] = seat["model"]
        record["effort"] = seat.get("effort")
        record["max_tokens"] = registry_lib.cap_for(entry, run_cap)
        record["substitution"] = seat["substitution"]
    return mutate


# --- manifest ------------------------------------------------------------------------------------

def _build_manifest(args, panel, panel_path, config_path, paths, run_dir, seats, dispatched, harness,
                    inputs, fingerprint, tier_default, registry):
    """The manifest as it stands at Resolve: every seat pending, every input pinned."""
    artifact = inputs["artifact"]
    references = inputs["references"]
    dispatched_ids = {s["reviewer_id"] for s in dispatched}

    seat_records = []
    for seat in seats:
        harness_seat = seat["reviewer_id"] not in dispatched_ids
        entry = registry.get(seat["model"]) or {}
        seat_records.append({
            "reviewer_id": seat["reviewer_id"],
            "lens": seat["lens"],
            "requested_family": seat["requested"],
            "family": seat["family"],
            "tier": seat["tier"],
            "tier_source": seat["tier_source"],
            "model": None if harness_seat else seat["model"],
            "connector": None if harness_seat else seat["connector"],
            "provider": None,
            "leg": "harness" if harness_seat else "openrouter",
            "input_delivery": "materialized-paths" if harness_seat else "inlined",
            "effort": None if harness_seat else seat.get("effort"),
            "max_tokens": None if harness_seat else registry_lib.cap_for(entry, args.max_tokens),
            "status": "pending",
            "report": None,
            "verdict": None,
            "findings": None,
            "dispatches": None,
            "attempts": None,
            "repairs": None,
            "truncations": None,
            "usage": None,
            "reasoning_tokens": None,
            "cost_usd": None,
            "elapsed_s": None,
            "substitution": seat.get("substitution"),
        })
        if harness_seat:
            seat_records[-1]["note"] = ("dispatched by the host session as a harness subagent; "
                                        "render with render_harness_report.py")

    return {
        "run_id": args.run_id or os.path.basename(os.path.abspath(run_dir)),
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "panel": panel.get("name", args.panel),
        "panel_path": panel_path,
        "artifact": artifact["path"],
        "artifact_revision": artifact["revision"],
        "artifact_commit": artifact["commit"],
        "artifact_input": artifact["materialized"],
        "references": [record["path"] for record in references],
        "reference_revisions": [
            {"path": r["path"], "revision": r["revision"], "commit": r["commit"], "materialized": r["materialized"]}
            for r in references
        ],
        "input_fingerprint": fingerprint,
        "config": config_path,
        "models_registry": registry.path,
        "workspace": paths.workspace,
        # The cascade's audit record: the search order, the root each loaded file actually came
        # from, and the ones a workspace override supplied. `config-fragment` appears only when a
        # workspace `config.json` deep-merged over the packaged one.
        "roots": paths.roots_block(),
        "tier_default": tier_default,
        "max_tokens_requested": args.max_tokens,
        "budget_usd": args.budget_usd,
        "skip_claude": args.skip_claude,
        "seats": seat_records,
        "families_dispatched": sorted({s["family"] for s in dispatched}),
        "min_families_target": panel.get("min_families"),
        "elapsed_s": None,
        "cost_usd_total": None,
        "failures": [],
    }


def _adopt_recorded_substitutions(existing, seats):
    """Carry a runtime re-seat forward onto a resumed run's freshly resolved seats.

    Seats are re-resolved from the panel on every run, and the panel still names the family the
    provider refused. Without this, a resume re-derives that family, pays for the refusal again, and
    re-seats again — one wasted call per resume, and a `substitution` the manifest already recorded
    silently redone. Only `model_unavailable` is adopted: a missing cell and an unsatisfiable
    constraint are properties of the config and the panel, so re-resolving them reaches the same
    answer, and adopting them would freeze a stale config decision into the run.
    """
    by_id = {seat.get("reviewer_id"): seat for seat in existing.get("seats") or []}
    for seat in seats:
        record = by_id.get(seat["reviewer_id"]) or {}
        substitution = record.get("substitution")
        if not isinstance(substitution, dict) or substitution.get("kind") != "model_unavailable":
            continue
        if not record.get("family") or not record.get("model"):
            continue
        seat["family"] = record["family"]
        seat["model"] = record["model"]
        seat["effort"] = record.get("effort")
        seat["substitution"] = substitution


def _merge_resume(manifest, existing):
    """Carry every seat's landed state forward onto the freshly resolved manifest.

    Resolution can legitimately re-run — the config may have moved — but a seat that already reported
    keeps its status, its report and its accounting, because resume must not re-dispatch it.
    """
    previous = {seat.get("reviewer_id"): seat for seat in existing.get("seats") or []}
    for seat in manifest["seats"]:
        before = previous.get(seat["reviewer_id"])
        if not before:
            continue
        if before.get("status") in ("ok", "dispatching", "failed", "timeout", "skipped"):
            for field in ("status", "report", "verdict", "findings", "attempts", "repairs", "truncations",
                          "usage", "reasoning_tokens", "cost_usd", "elapsed_s", "provider", "effort",
                          "max_tokens", "model", "connector", "substitution", "failure_reason", "error",
                          "claimed_at", "string_truncations", "dispatches", "unavailable_model"):
                if field in before:
                    seat[field] = before[field]
    manifest["resumed_from"] = existing.get("generated_at")
    return manifest


def _partition_for_dispatch(run_dir, dispatched, resuming):
    """(seats to dispatch, seats kept as already valid, seats held by another process).

    **The report on disk is the authority, not the manifest status.** A seat the manifest calls `ok`
    whose report has been deleted, truncated or hand-edited into invalidity is a seat that did not
    report, and resume exists to repair exactly that. It is demoted back to `failed` here, with the
    reason recorded, so the claim that follows is an honest `failed -> dispatching` transition and the
    manifest stops asserting something the directory contradicts.

    The one status this step will not touch is `dispatching`: that seat belongs to another process.
    """
    manifest = runs_lib.read_manifest(run_dir)
    by_id = {seat.get("reviewer_id"): seat for seat in manifest.get("seats") or []}

    to_dispatch, kept, held = [], [], []
    for seat in dispatched:
        reviewer_id = seat["reviewer_id"]
        record = by_id.get(reviewer_id) or {}
        status = record.get("status")
        if status == "dispatching":
            held.append((reviewer_id, "already `dispatching` — another process owns this seat"))
            continue
        if status == "failed" and record.get("failure_reason") == "context-overflow":
            continue

        valid, why = runs_lib.report_is_valid(run_dir, reviewer_id, _validate)
        if valid:
            if resuming:
                kept.append((reviewer_id, "a validated report is already on disk; not re-dispatched"))
                continue
            to_dispatch.append(seat)
            continue

        if status == "ok":
            runs_lib.update_seat(run_dir, reviewer_id, _demotion(why))
        to_dispatch.append(seat)
    return to_dispatch, kept, held


def _validate(data):
    return report_lib.validate_report(data, lens=data.get("lens"))


def _demotion(why):
    """Move a seat the manifest called `ok` back to `failed`, saying what the directory showed."""
    def mutate(seat):
        seat["status"] = "failed"
        seat["report"] = None
        seat["failure_reason"] = "report-missing-or-invalid"
        seat["error"] = ("the manifest recorded this seat as `ok`, but {0}. Resume re-dispatched "
                         "it.".format(why))
    return mutate


def _wrap_up(args, run_dir, results, harness, dispatched, elapsed, halt, held):
    """Fold every seat's outcome into the manifest, print the digests, choose the exit code."""
    failures = []
    held = list(held)
    for result in results:
        seat = result["seat"]
        reviewer_id = seat["reviewer_id"]
        if result.get("skipped"):
            # A seat skipped inside the dispatch fan-out is still a seat this run did not produce.
            # It has to reach the held list, or the console reports `held: 0` for a run that held one.
            if result["skipped"] == "held":
                held.append((reviewer_id, result.get("held_reason") or "held"))
            continue
        report_path = os.path.join(run_dir, reviewer_id + ".json")
        failed_path = os.path.join(run_dir, reviewer_id + ".failed.json")

        def mutate(record, result=result, report_path=report_path, failed_path=failed_path, reviewer_id=reviewer_id):
            record["elapsed_s"] = result["elapsed_s"]
            if result["returncode"] == 0 and os.path.isfile(report_path):
                seat_record_from_report(record, report_path)
                return
            record["status"] = "failed"
            record["report"] = None
            record["error"] = (result["stderr"] or "")[-2000:]
            if os.path.isfile(failed_path):
                with open(failed_path, "r", encoding="utf-8") as handle:
                    failed = json.load(handle)
                record["validation_errors"] = failed.get("errors")
                record["raw_response"] = failed.get("raw_response")
                record["attempt_record"] = failed_path
                _fold_meta(record, failed.get("_meta") or {})

        runs_lib.update_seat(run_dir, reviewer_id, mutate)
        if result["returncode"] != 0:
            failures.append(reviewer_id)

    manifest = runs_lib.read_manifest(run_dir)
    total_cost = 0.0
    cost_known = False
    for seat in manifest["seats"]:
        if seat.get("cost_usd") is not None:
            total_cost += float(seat["cost_usd"])
            cost_known = True
    manifest["elapsed_s"] = round(elapsed, 1)
    manifest["cost_usd_total"] = round(total_cost, 6) if cost_known else None
    manifest["failures"] = sorted({s["reviewer_id"] for s in manifest["seats"] if s.get("status") == "failed"})
    runs_lib.write_manifest(run_dir, manifest)

    # Harness seats are expected to sit `pending` until the host spawns them, so they are not what
    # "under-seated" means here; only the OpenRouter leg's own seats are counted against exit 3.
    #
    # **The count comes from the reports on disk, not from the manifest's status.** A manifest that
    # says `ok` for a seat whose report is gone would otherwise let the run print "2 of 2" and exit 0
    # over a directory holding one report — the exact lie resume exists to prevent.
    openrouter_seats = [s for s in manifest["seats"] if s.get("leg") != "harness"]
    reporting = [s for s in openrouter_seats
                 if runs_lib.report_is_valid(run_dir, s["reviewer_id"], _validate)[0]]
    print("Panel `{0}` on {1}".format(manifest["panel"], manifest["artifact"]))
    print("Run directory: {0}".format(run_dir))
    print("Seats reporting: {0} of {1} · harness pending: {2} · failed: {3} · held: {4} · {5:.0f}s{6}".format(
        len(reporting), len(openrouter_seats), len(harness), len(manifest["failures"]), len(held), elapsed,
        " · ${0:.4f}".format(total_cost) if cost_known else ""))
    print("")

    by_id = {r["seat"]["reviewer_id"]: r for r in results}
    records_by_id = {record["reviewer_id"]: record for record in manifest["seats"]}
    for seat in dispatched:
        result = by_id.get(seat["reviewer_id"])
        print("-" * 72)
        if result and result.get("skipped"):
            print("{0} — {1}".format(seat["reviewer_id"], result.get("held_reason") or result["skipped"]))
        elif result and result["returncode"] == 0:
            print(result["digest"])
        elif result:
            record = records_by_id.get(seat["reviewer_id"]) or {}
            spent = " · spent ${0:.4f} anyway across {1} attempt(s)".format(
                record["cost_usd"], len(record.get("attempts") or [])) if record.get("cost_usd") is not None else ""
            print("{0} [{1}] FAILED{2}".format(seat["reviewer_id"], seat["model"], spent))
            print(result["stderr"][-600:])
        else:
            record = records_by_id.get(seat["reviewer_id"]) or {}
            print("{0} — {1}".format(seat["reviewer_id"], record.get("failure_reason") or record.get("status") or "not dispatched"))
    for seat in harness:
        print("-" * 72)
        print("{0} — pending on the harness leg; spawn the subagent, then render it".format(seat["reviewer_id"]))
    print("-" * 72)
    print("Manifest: {0}".format(runs_lib.manifest_path(run_dir)))

    if halt.is_set():
        sys.stderr.write("provider auth failure: the run halted and no further seat was dispatched.\n")
        return EXIT_TERMINAL
    if not reporting and not harness:
        sys.stderr.write("zero seats reported: no reconciliation can be written over zero reports.\n")
        return EXIT_TERMINAL
    if len(reporting) < len(openrouter_seats):
        return EXIT_UNDER_SEATED
    return EXIT_OK


def _repo_relative(path):
    absolute = os.path.abspath(path)
    directory = os.path.dirname(absolute)
    try:
        root = subprocess.run(["git", "-C", directory, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=30)
        if root.returncode == 0 and root.stdout.strip():
            return os.path.relpath(absolute, root.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.relpath(absolute, os.getcwd())


if __name__ == "__main__":
    sys.exit(main())

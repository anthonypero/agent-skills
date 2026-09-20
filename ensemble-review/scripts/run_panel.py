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
  never write into the same directory. The two **panel-shape** refusals are raised before this line
  and leave nothing on disk — the deferred stub, and a `fidelity` or `source-credibility` seat with
  no references — because both are decidable from the panel, the config and the seats alone. Every
  later refusal fires after the claim and leaves the directory holding whatever it had written by
  then: the registry gate, the effort gate, a resume fingerprint mismatch, a materialize failure,
  and the budget refusal, which writes `budget-refusal.json` there on purpose.
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

Every file the run loads — the panel, the config, the connector file, the model files, the personas,
the finding schema, the backend driver — resolves through `lib/paths.py`'s three-root cascade:
`<workspace>/.agents/ensemble-review/` first, then `~/.config/ensemble-review/`, then the skill
package, which is read-only and is never written to. Files replace whole; `config.json` and the
model files deep-merge. The root each one came from lands in the manifest's `roots`, with the
project's overrides and this machine's kept in separate lists.

**The endpoint is a connector file.** `config.json` names one by `default_connector` and the file
carries the driver, the base URL, the key source and the **billing posture**. A `metered` connector
refuses to dispatch without `--approve-spend`, which is a different question from `--approve-budget`:
one answers "you may spend on this endpoint at all" and the other "this projection is over the limit
you set". The answer lands in the manifest as `spend_approval`.

**Effort is abstract.** `--effort`, a seat's `effort`, a panel's `effort`, the config's
`default_effort` and the persona's frontmatter all name `light`, `standard` or `deep`; each model
file binds the level onto that model's own rung. The manifest records the level, the level that
chose it and the parameter actually sent, per seat.

With no `--panel`, the template is **inferred**: a cheap heuristic over the artifact's own filename
picks `research-report` or `design-decision` when the name says so and `spec-review` otherwise, and a
run with **no references never lands on a template whose `requires_references` is true** — it falls
back to `design-decision` and records the substitution in the manifest, on the console, and in the
reconciliation's method caveat. `code-review` ships deferred and refuses with exit 1, naming
`/code-review`. A `fidelity` or `source-credibility` seat with no references is a composition error,
also exit 1: those two lenses cite on every finding and cannot do their job against nothing.

`min_families` is a **target, not a precondition**. The panel's value (default 2) is overridable with
`--min-families`; distinct families are counted twice, over the expected seats at Resolve and over
the reporting seats at wrap-up, and both counts land in the manifest as
`min_families: {target, seated, reporting}`. A run that misses the target still runs, and
`reconcile_core.method_caveat` says so — "lens-diverse only" when one family reported.

Seats resolve through `lib/seating.py`: the tier order (`--model`, `--tier`, the seat, the panel, the
config, the persona frontmatter) and the three constraint passes (named, `non-claude`, `distinct`).
A family with no cell at the resolved tier, and a family the provider refuses at dispatch time with a
404 or 400 naming the model, are the same condition — unreachable — and both re-seat that lens onto
the next available family **once**, recording a `substitution` the reconciliation's method caveat
then names.

Re-running against an existing run directory **resumes** it: only seats whose reports are absent or
invalid are re-dispatched, a seat already `dispatching` under a **live** lease is left alone and
reported as held, and a run whose inputs no longer hash to the manifest's fingerprint is refused
rather than mixed. A seat left `dispatching` by a process that is gone, or under a claim older than
any dispatch could be, is a stale lease: it is demoted to `failed` with `failure_reason:
"stale-lease"` and re-claimed, so a run killed mid-dispatch is resumable instead of wedged.
`--fresh` forces a new claim instead.

`--skip-claude` leaves the claude seats to the host session, which spawns them as harness
subagents with the same persona body. Those seats are recorded in the manifest as pending on the
harness leg. It is off by default: without it every seat, claude included, runs through OpenRouter.

**The judge stage** is the last thing this script does, and what it does there depends on who
judges. An autonomous run judged by `synthesis` runs Judge and Reconcile itself, because there is
nobody to type the second command. A run judged by the **harness judge** — the default unattended
judge wherever `install.sh` has put `ensemble-judge` in the harness agents directory — runs
`reconcile.py`'s first pass, which writes the worksheet of provisional clusters the agent answers
and prints the spawn instruction over it, and then stops: a script cannot spawn a harness agent.
Every other run stops after Collect and prints the `reconcile.py` line, because the judgment is
the host's to write.

`--smoke-test <model>` pins **every** seat to one model, drops the family target to 1, marks the
manifest `smoke_test: true` and makes the reconciliation open by saying the run is not evidence. It
exists to prove the pipeline end to end for cents, and it is still priced and still gated by the
budget — a flag that turned off the one control in front of the money would be the opposite of
what it is for.
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

import dispatch  # noqa: E402
from lib import budget as budget_lib  # noqa: E402
from lib import connectors as connectors_lib  # noqa: E402
from lib import drafts as drafts_lib  # noqa: E402
from lib import judge as judge_lib  # noqa: E402
from lib import panels as panels_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402
from lib import runs as runs_lib  # noqa: E402
from lib import seating as seating_lib  # noqa: E402

DISPATCH = os.path.join(SCRIPTS_DIR, "dispatch.py")
RECONCILE = os.path.join(SCRIPTS_DIR, "reconcile.py")
APPLY_FIXES = os.path.join(SCRIPTS_DIR, "apply_fixes.py")
FINDING_SCHEMA = "finding-schema.md"
PROVIDER = "openrouter"

DEFAULT_BUDGET_USD = 5.00

# Panel inference. `DEFAULT_PANEL` is where a run with no signal lands; `FALLBACK_PANEL` is where a
# run with no references lands, and it is the one shipped template whose `requires_references` is
# false. Both are names, not paths, so a workspace copy of either wins through the cascade.
DEFAULT_PANEL = "spec-review"
FALLBACK_PANEL = "design-decision"

# The cheap heuristic, and it is deliberately cheap: the artifact's own filename, split into words,
# against two word lists. Nothing reads the document — a heuristic that opened the artifact would be
# a second, unreviewed judgement about it before any reviewer has seen it, and the operator can
# always name the panel. First list that matches wins; no match is `spec-review`.
PANEL_HEURISTIC = (
    ("research-report", ("research", "survey", "landscape", "findings", "report")),
    ("design-decision", ("decision", "adr", "rfc", "proposal", "options", "tradeoff", "fork", "choice")),
)

# The two lenses that cite on every finding. A seat carrying one with no references is a composition
# error rather than a wasted seat: the reviewer would be asked for a citation it has no document to
# take one from, and every finding it returned would fail validation.
REFERENCE_REQUIRED_LENSES = ("fidelity", "source-credibility")

# `min_families` is a target. A panel that does not set one is held to this.
DEFAULT_MIN_FAMILIES = 2

EXIT_OK = 0
EXIT_COMPOSITION = 1
EXIT_TERMINAL = 2
EXIT_UNDER_SEATED = 3
EXIT_BUDGET = 4

# dispatch.py's own codes, read back from the subprocess.
DISPATCH_AUTH = 2
DISPATCH_MODEL_UNAVAILABLE = 5


def load_panel(name_or_path, paths):
    """A panel template: an operator path when `--panel` names a file, else the workspace cascade.

    **Every key is checked before the template is used for anything.** `lib/panels.py` holds the
    vocabulary and raises `TemplateError` on the first unrecognized key, which `main` turns into a
    composition error, exit 1 — before the run directory is claimed, before a seat is priced, and
    before a call is made. Owner ruling, 2026-09-19: a template is written by an agent, so a
    silent `min_famalies` would run the panel at the default and spend the money before anybody
    read the file.
    """
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
        panel = json.load(handle)
    panels_lib.validate(panel, path)
    return panel, path


def panel_from_artifact(artifact):
    """The template name the artifact's own filename suggests, by the word lists above."""
    slug = os.path.basename(artifact)
    slug = slug.rsplit(".", 1)[0] if "." in slug else slug
    words = {word for word in re.split(r"[^a-z0-9]+", slug.lower()) if word}
    for name, markers in PANEL_HEURISTIC:
        if words & set(markers):
            return name
    return DEFAULT_PANEL


def infer_panel(artifact, refs, paths):
    """(panel, path, inference record) when the operator named no `--panel`.

    Two steps, and the second overrides the first. The heuristic reads the artifact's filename and
    nothing else. Then the **references rule**: a run with no references never infers a template
    whose `requires_references` is true, because the fidelity-bearing seats on it would be refused a
    line later as a composition error. It lands on `design-decision`, which is reference-free by
    design, and the substitution is recorded so the reconciliation can say the panel that ran was not
    the panel the artifact suggested.

    An explicit `--panel` skips all of this: an operator who names a reference-hungry template and
    supplies nothing gets the composition error rather than a quietly different panel.
    """
    heuristic = panel_from_artifact(artifact)
    panel, path = load_panel(heuristic, paths)
    # `requested` is null on this path by construction: `infer_panel` is only reached when the
    # operator named no `--panel`. The explicit path in `main` fills it with the name they gave.
    record = {"requested": None, "heuristic": heuristic, "resolved": heuristic, "reason": None}
    if panel.get("requires_references") and not refs:
        panel, path = load_panel(FALLBACK_PANEL, paths)
        record["resolved"] = FALLBACK_PANEL
        record["reason"] = (
            "panel {0!r} was inferred from the artifact name and requires source-of-truth references; "
            "none were supplied, so the run fell back to {1!r}, whose seats judge the artifact on its "
            "own argument".format(heuristic, FALLBACK_PANEL))
    return panel, path, record


def reference_required_seats(seats):
    """Every resolved seat whose lens cites on every finding. Empty unless one is seated."""
    return [seat for seat in seats if seat["lens"] in REFERENCE_REQUIRED_LENSES]


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
    """Characters in this lens's system message: the persona body plus every reference it declares.

    It used to sum the body plus the finding schema, which is the same thing for a shipped lens and
    is not the same thing for a workspace one. A project that gives a lens a second reference — a
    house style, a glossary — makes every one of that seat's prompts bigger, and a projection that
    could not see it would gate a run against a prompt size the run does not have. The resolver is
    `dispatch.py`'s own, so the projection and the dispatch agree by construction.

    Silently: `dispatch.py` prints the missing-schema note once per seat when it actually composes
    the prompt, and printing it again here would say it twice for every seat before anything ran.
    """
    persona = paths.find("persona", "lens-" + lens)
    if not persona:
        return 0
    frontmatter, body = report_lib.parse_agent_file(persona["path"])
    total = len(body)
    try:
        references = dispatch.persona_context_paths(paths, frontmatter, warn=lambda _message: None)
    except paths_lib.PathError:
        # A reference that does not resolve is a composition error `dispatch.py` will raise with a
        # better message than a projection could. Here it is simply a prompt this cannot measure.
        return total
    for path in references:
        total += os.path.getsize(path)
    return total


def _synthesis_persona_frontmatter(paths):
    """The `synthesis` persona's frontmatter, the lowest level of both of the judge's orders.

    Missing persona, unreadable file: both mean "these levels contribute nothing", never an error —
    the levels above them are the ones a run is normally decided by. Returned as an empty dict so
    callers can read a key off it either way.
    """
    found = paths.find("persona", judge_lib.SYNTHESIS_PERSONA)
    if not found:
        return {}
    try:
        frontmatter, _body = report_lib.parse_agent_file(found["path"])
    except OSError:
        return {}
    return frontmatter or {}


def _synthesis_persona_tier(paths):
    """The `synthesis` persona's frontmatter `model`, the lowest level of the judge's tier order.

    Returned raw; `judge_lib.synthesis_seat` is what decides whether it names a tier this config
    offers, exactly as `seating.py` does for a lens. Missing persona, missing key, unreadable file:
    all mean "this level contributes nothing", never an error — the levels above it are the ones a
    run is normally decided by.
    """
    return _synthesis_persona_frontmatter(paths).get("model")


def persona_frontmatter(paths):
    """`frontmatter_fn` for `seating.resolve`: the lowest-precedence tier source, when it names one."""
    def read(lens):
        found = paths.require("persona", "lens-" + lens)
        frontmatter, _body = report_lib.parse_agent_file(found["path"])
        return frontmatter
    return read


def _registry_for_labels(paths, args):
    """The registry, or None, for the family relabel alone — never for the gate.

    The registry **gate** runs after the run directory is claimed, and that ordering is documented:
    a refusal for an unpriceable model leaves the directory holding what it wrote. Seating happens
    before the claim and wants one field from the same file, so it reads it leniently here. A
    registry that will not load is not an error at this point; it is an error a few lines later,
    with a better message and the right consequences.
    """
    try:
        return registry_lib.load(paths, args.models)
    except (paths_lib.PathError, registry_lib.RegistryError):
        return None


def _is_harness_seat(seat, args, harness_leg):
    """Whether this seat is run by the orchestrating session rather than by a `dispatch.py` child.

    One state, two doors. `--skip-claude` puts a `claude` seat on the leg and leaves the rest on the
    endpoint; a `harness` connector puts **every** seat on it, which is what `--draft` selects. The
    second is decided by the connector's driver `type` rather than by its name, because a project
    may call its own copy of the harness connector anything it likes and still mean the harness.
    """
    if harness_leg:
        return True
    return bool(args.skip_claude) and seat.get("family") == "claude"


def _draft_refusal(paths, args, panel, draft_entry, connector):
    """Why this `--draft` run cannot cost nothing, as a message, or None when it can.

    A draft pass is **defined** by its price — harness subagents on the owner's plan, no metered
    call at all — so a seat a metered endpoint would serve is a composition error and not a gate:
    there is no answer an operator could give that would turn it into a draft seat. It is decided
    here, before the run directory is claimed, so the refusal leaves nothing behind.

    Three ways it can go wrong, and all three are named rather than lumped together:

    1. the connector the mode selected is not the harness leg at all — an outer root holding a
       `connectors/harness.json` that points somewhere else, which is a file somebody wrote and a
       run must not quietly reinterpret;
    2. a named family the harness leg has no cell for at that seat's tier, which the ordinary
       endpoint would have served and billed. This is the `--draft --panel spec-review` case, and
       the message has to say so in those terms rather than as "no model at this tier";
    3. a `--model` pin naming a model whose own file binds it to a metered connector.

    The second check runs against the template's seats rather than resolved ones, because seating
    against a map with no cell for `openai` would re-seat or refuse first, with a message about the
    tier map that points the reader at the wrong file entirely.
    """
    if not drafts_lib.is_harness_connector(connector):
        return (
            "composition error: --draft selects the {0!r} connector, and the file that answers to "
            "that name is not the harness leg.\n"
            "  file: {1}\n"
            "  its driver `type` is {2!r}; the harness leg's is {3!r}.\n"
            "  A draft pass spawns its seats as harness subagents and calls no endpoint. A "
            "connector named `harness` that points at one is a file somebody wrote, so this run "
            "refuses rather than reinterpreting it.\n".format(
                drafts_lib.HARNESS_CONNECTOR, connector.get("path"), connector.get("type"),
                drafts_lib.HARNESS_DRIVER))
    if connector.get("billing") == connectors_lib.METERED:
        return (
            "composition error: the {0!r} connector at {1} declares `billing: metered`.\n"
            "  A draft pass costs nothing beyond the plan by definition, so the harness leg cannot "
            "be a metered endpoint. Fix that connector file's `billing`, or drop --draft.\n".format(
                connector.get("name"), connector.get("path")))

    # The ordinary endpoint, resolved for the **diagnosis alone** — it is what would have billed
    # the seats this mode cannot seat, and naming it is the whole value of the refusal.
    try:
        metered_entry, _path, metered_connector, _registry = dispatch.resolve_entry(
            paths, args.config, args.models)
    except (paths_lib.PathError, dispatch.CompositionError, registry_lib.RegistryError):
        metered_entry, metered_connector = None, None
    metered = (metered_connector or {}).get("billing") == connectors_lib.METERED

    blocked = []
    read_frontmatter = persona_frontmatter(paths)
    for raw in panel.get("seats") or []:
        lens, family = raw.get("lens"), raw.get("family")
        if not lens or not family or family in seating_lib.CONSTRAINTS:
            continue
        try:
            frontmatter = read_frontmatter(lens)
        except paths_lib.PathError:
            frontmatter = None
        tier, _source = seating_lib.resolve_tier(raw, panel, draft_entry, args.tier, False, frontmatter)
        if seating_lib.model_at(draft_entry, tier, family):
            continue
        model = seating_lib.model_at(metered_entry, tier, family) if metered_entry else None
        if model and metered:
            blocked.append({
                "kind": "cell",
                "reviewer_id": seating_lib.reviewer_id(lens, family, raw.get("suffix") or ""),
                "family": family, "tier": tier, "model": model,
                "connector": metered_connector.get("name"),
                "billing": metered_connector.get("billing"),
            })

    registry = _registry_for_labels(paths, args)
    try:
        pins = parse_model_pins(args.models_pinned)
    except seating_lib.SeatingError:
        pins = {}                      # a malformed pin is refused a few lines later, with its own message
    for seat_id, model in sorted(pins.items()):
        name = ((registry.get(model) if registry else None) or {}).get("connector")
        if not name:
            continue
        try:
            pinned_connector, _pinned_path = connectors_lib.load(paths, name)
        except connectors_lib.ConnectorError:
            continue
        if pinned_connector.get("billing") == connectors_lib.METERED:
            blocked.append({"kind": "pin", "reviewer_id": seat_id, "model": model,
                            "connector": name, "billing": pinned_connector.get("billing")})

    return drafts_lib.metered_refusal(blocked) if blocked else None


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
        "--effort", seat["effort_level"],
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
    # Inference the provider ran and did not charge for. Summed alongside `cost_usd` and never into
    # it: `cost_usd_total` has to reconcile against the credit ledger, and this does not.
    record["upstream_unbilled_usd"] = _accumulate(
        record.get("upstream_unbilled_usd"), meta.get("upstream_unbilled_usd"))


def _accumulate(before, now):
    """Sum two optional numbers. None only when neither dispatch reported one."""
    values = [value for value in (before, now) if value is not None]
    return sum(values) if values else None


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Run a review panel: one dispatch per seat, in parallel.")
    parser.add_argument("--panel", default=None,
                        help="Panel template name in templates/panels/, or a path to one (default: inferred from the artifact's name, and never a template needing references the run does not have)")
    parser.add_argument("--artifact", required=True, help="Path to the document under review")
    parser.add_argument("--ref", action="append", default=[], dest="refs", help="Path to a source-of-truth reference; repeat for several")
    parser.add_argument("--out", required=True, help="Run directory")
    parser.add_argument("--workspace", default=None,
                        help="The project holding the artifact. Every file resolves from <workspace>/.agents/ensemble-review/ first, ~/.config/ensemble-review/ second and the skill package third; config.json and the model files deep-merge rather than replacing (default: the working directory)")
    parser.add_argument("--config", default=None, help="Config JSON, taken as given (default: the workspace-first cascade, deep-merged)")
    parser.add_argument("--models", default=None, help="Directory of model files, taken as given (default: the workspace-first cascade, deep-merged per model)")
    parser.add_argument("--tier", default=None, help="Tier for every seat that does not set its own (default: the panel's, then the config's)")
    parser.add_argument("--effort", default=None, metavar="LEVEL", choices=registry_lib.EFFORT_LEVELS,
                        help="Abstract effort level for every seat that does not set its own: {0} (default: the seat's, the panel's, the config's default_effort, then the persona's). Each model's own file binds the level to its own rung".format("/".join(registry_lib.EFFORT_LEVELS)))
    parser.add_argument("--min-families", type=int, default=None, dest="min_families",
                        help="Target distinct families, overriding the panel's (default: the panel's, else {0}). A target, not a precondition: a run below it proceeds and the reconciliation's method caveat says so".format(DEFAULT_MIN_FAMILIES))
    parser.add_argument("--model", action="append", default=[], dest="models_pinned", metavar="SEAT=MODEL",
                        help="Pin one seat to a concrete model id, e.g. --model fidelity-openai=openai/gpt-6-astra. Repeatable; beats every tier source. The seat's family label is relabelled from the model")
    parser.add_argument("--smoke-test", default=None, metavar="MODEL", dest="smoke_test",
                        help="Prove the pipeline, not the artifact: pin EVERY seat to this one model, set the family target to 1, mark the manifest `smoke_test` and say in the reconciliation that the run is not evidence. Still priced and still gated — by the budget and by the connector's spend gate, since it also spends")
    parser.add_argument("--draft", action="store_true",
                        help="A draft pass at $0 beyond the plan: compose against the `harness` connector, so every seat is a harness subagent the session spawns rather than a metered call. Drops the family target to 1, marks the manifest `draft`, and opens the reconciliation by saying the run carries no corroboration claim. Refuses a panel a metered endpoint would serve. Mutually exclusive with --smoke-test")
    parser.add_argument("--max-tokens", type=int, default=registry_lib.DEFAULT_MAX_TOKENS,
                        help="Completion cap sent on every call; a model's registry floor raises it for that seat (default: {0})".format(registry_lib.DEFAULT_MAX_TOKENS))
    parser.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
                        help="Pre-flight budget (default: {0:.2f})".format(DEFAULT_BUDGET_USD))
    parser.add_argument("--approve-budget", action="store_true", help="Dispatch even when the projection exceeds the budget")
    parser.add_argument("--approve-spend", action="store_true", dest="approve_spend",
                        help="Allow paid calls on a `billing: metered` connector at all. A different question from --approve-budget, which answers \"this projection is over the limit you set\"; this one answers \"you may spend on this endpoint\"")
    parser.add_argument("--autonomous", action="store_true", help="Nobody to ask: an over-budget projection refuses and exits 4 instead of prompting")
    parser.add_argument("--reconciler", choices=judge_lib.RECONCILERS, default=None,
                        help="Who supplies the judgment patch (default: the panel's, else `default` — host interactive, synthesis autonomous). Decides whether the projection charges for a synthesis call")
    parser.add_argument("--synthesis-model", default=None,
                        help="Pin the judgment call to a concrete model id, overriding the tier map. Priced in the pre-flight and passed to the judge stage")
    parser.add_argument("--reconcile", choices=("auto", "off"), default="auto",
                        help="`auto` runs reconcile.py at the end of an autonomous run whose judgment comes from `synthesis`, so the whole pipeline is one command; `off` always stops after Collect and prints the reconcile command (default: auto)")
    parser.add_argument("--auto-apply", choices=("on", "off"), default="off", dest="auto_apply",
                        help="`on` arms apply_fixes.py's five-condition gate after the reconciliation is written. Off by default, and refused without --i-authored-this")
    parser.add_argument("--i-authored-this", action="store_true", dest="authored",
                        help="Assert that you wrote the document under review. Required by --auto-apply on: the artifact is untrusted input, so nothing is written back to a document the operator did not author")
    parser.add_argument("--fresh", action="store_true", help="Claim a new run directory instead of resuming an existing one")
    parser.add_argument("--skip-claude", action="store_true", help="Leave claude seats to the host session's harness subagents")
    parser.add_argument("--run-id", default=None, help="Recorded in the manifest; defaults to the run directory's basename")
    return parser.parse_args(argv)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    args = parse_args(argv)

    # **The two cheap modes are not one mode.** A smoke test pins every seat to one metered model to
    # prove the pipeline and says nothing about the artifact; a draft pass is a real review by four
    # lenses on the subscription that cannot claim corroboration. Asking for both would have to
    # resolve to one of them — the pin against the harness leg, "not evidence" against
    # "uncorroborated" — and whichever won, the manifest would carry a flag the run did not honour.
    if args.draft and args.smoke_test:
        sys.stderr.write(
            "composition error: --draft and --smoke-test are different runs and cannot be one run.\n"
            "  --smoke-test pins every seat to one metered model to prove the pipeline end to end "
            "for cents; its reconciliation says the run is not evidence about the artifact at all.\n"
            "  --draft seats every lens on a harness subagent on the plan; its reconciliation says "
            "the findings are real and uncorroborated. Pick the one you meant.\n")
        return EXIT_COMPOSITION

    if args.auto_apply == "on" and not args.authored:
        sys.stderr.write(
            "composition error: --auto-apply on needs --i-authored-this.\n"
            "  The artifact is inlined verbatim into every seat's user message, so a document can\n"
            "  instruct its own reviewers to return a consensus-shaped replacement. Nothing is ever\n"
            "  written back to a document the operator has not claimed as their own.\n")
        return EXIT_COMPOSITION

    if not os.path.isfile(args.artifact):
        sys.exit("Not a file: {0}".format(args.artifact))
    for ref in args.refs:
        if not os.path.isfile(ref):
            sys.exit("Not a file: {0}".format(ref))

    paths = paths_lib.Paths(args.workspace)
    try:
        if args.panel:
            panel, panel_path = load_panel(args.panel, paths)
            # An operator who named the panel still gets the record, because `requested` is the
            # field that says so. `heuristic: null` is what distinguishes the two paths: nothing
            # read the artifact's name, so nothing could have been substituted.
            inference = {"requested": args.panel, "heuristic": None,
                         "resolved": panel.get("name") or args.panel, "reason": None}
        else:
            panel, panel_path, inference = infer_panel(args.artifact, args.refs, paths)
    except (paths_lib.PathError, panels_lib.TemplateError) as failure:
        sys.stderr.write("composition error: {0}\n".format(failure))
        return EXIT_COMPOSITION
    panel_name = panel.get("name") or args.panel or DEFAULT_PANEL

    # A deferred template is a named stub carrying a route, not a panel with no seats. It refuses
    # before anything is claimed or materialized, so the run leaves nothing behind.
    if panel.get("deferred"):
        sys.stderr.write(
            "composition error: panel `{0}` ships deferred and seats nobody.\n"
            "  {1}\n"
            "  Use {2} instead — code diffs are not this skill's lane, which is documents judged "
            "against their source-of-truth references.\n".format(
                panel_name, panel.get("description") or "", panel.get("routes_to") or "/code-review"))
        return EXIT_COMPOSITION

    # **A `draft_only` template refuses without `--draft`, at template load.** The flag is what
    # selects the harness endpoint, so without it every seat of such a template resolves against the
    # *metered* map and the run bills for a pass whose whole definition is that it costs nothing —
    # four Claude seats and a projection of real money, one `--approve-spend` from being charged.
    # The template declares what it is and this is what makes the declaration true. Refused here,
    # before anything is claimed or priced, and refused under `--skip-claude` too: that flag leaves
    # `claude` seats to the session on an otherwise metered run and selects no endpoint at all, so
    # it changes nothing about which map these seats resolve against.
    if panel.get("draft_only") and not args.draft:
        sys.stderr.write(
            "composition error: panel `{0}` is a draft-pass template and this run is not a draft "
            "pass.\n"
            "  It exists for the $0 draft pass: every seat is a harness subagent on the owner's "
            "plan. Without --draft the run composes against the metered endpoint instead, so these "
            "seats would be priced, gated and billed like any other panel's.\n"
            "  Run it as a draft pass:\n"
            "    python3 scripts/run_panel.py --draft --panel {0} --artifact {1} --out {2}\n"
            "  --skip-claude is not the same thing: it leaves explicitly seated `claude` seats to "
            "the session on an otherwise metered run and selects no endpoint, so it does not make "
            "this a draft pass either.\n".format(panel_name, args.artifact, args.out))
        return EXIT_COMPOSITION

    if args.smoke_test:
        print("=" * 72)
        print("SMOKE TEST — every seat is pinned to {0}.".format(args.smoke_test))
        print("This run proves the pipeline, not the artifact: one model behind every lens, a")
        print("family target of 1, and no cross-family corroboration available at any tier. The")
        print("manifest carries `smoke_test: true` and the reconciliation says the run is not")
        print("evidence. It is still priced and still gated — by the budget, and by the connector's")
        print("spend gate, which a metered endpoint answers with --approve-spend.")
        print("=" * 72)

    if inference["reason"]:
        print("panel inferred: {0} (the {1} template needs references this run does not have)".format(
            inference["resolved"], inference["heuristic"]))
    elif inference["heuristic"]:
        print("panel inferred: {0} (from the artifact's name; pass --panel to choose another)".format(
            inference["resolved"]))

    # --- Resolve, part one: the cascade, the config and the seats ---------------------------------
    # Seats resolve **before the run directory is claimed**, so every composition error this stage
    # can raise refuses the way the deferred-stub refusal above does: nothing created, nothing to
    # clean up. Only `_adopt_recorded_substitutions` needs the claim, and it waits for it below.
    try:
        # **A draft run composes against the harness connector, not the config's default.** The
        # mode is a statement about which endpoint the run may use — none that bills — so it
        # selects the endpoint, and the tiers map it seats from is built over that endpoint's own
        # model files. Everything downstream is unchanged: the seats resolve normally, and they
        # resolve onto a connector whose driver is never called.
        config_entry, config_path, connector, _registry = dispatch.resolve_entry(
            paths, args.config, args.models,
            connector_override=drafts_lib.HARNESS_CONNECTOR if args.draft else None)
        if args.draft:
            refusal = _draft_refusal(paths, args, panel, config_entry, connector)
            if refusal:
                sys.stderr.write(refusal)
                return EXIT_COMPOSITION
        seats = seating_lib.resolve(
            panel, config_entry,
            cli_tier=args.tier,
            cli_effort=args.effort,
            pinned=parse_model_pins(args.models_pinned),
            pin_all=args.smoke_test,
            # Read only to relabel a pinned seat's family and to bind each seat's effort, and read
            # leniently: the registry **gate** is further down, after the claim, and moving it up
            # here to satisfy a label would change which refusals leave a run directory behind.
            registry=_registry_for_labels(paths, args),
            frontmatter_fn=persona_frontmatter(paths))
        paths.driver_ref(config_entry.get("type", "openai_compat"))
        # Every seat's system message carries it, so it is required, and its root belongs in the
        # audit record: a project that overrides the finding schema has changed what every reviewer
        # was asked for, which is the last thing `roots` should be silent about.
        paths.require("reference", FINDING_SCHEMA)
    except (paths_lib.PathError, seating_lib.SeatingError, dispatch.CompositionError) as failure:
        sys.stderr.write("composition error: {0}\n".format(failure))
        return EXIT_COMPOSITION

    # Who will supply the judgment patch, decided here and recorded in the manifest, so the panel
    # and `reconcile.py` cannot disagree about it later. `default` means host when a human is
    # attached and, when nobody is, the harness judge where one is installed and `synthesis` where
    # none is — the same test the budget gate turns on, which is why both read it from
    # `lib/judge.py` rather than each asking stdin its own question.
    #
    # **Resolved before the claim**, because the two refusals below are decidable from the panel,
    # the flags and the machine alone — the same class as the deferred stub and the starved seat —
    # and a refusal that can leave nothing behind should leave nothing behind: a stray run directory
    # silently moves the retry's sequence number.
    autonomous = judge_lib.is_autonomous(args.autonomous)
    try:
        reconciler = judge_lib.resolve_reconciler(
            args.reconciler, panel.get("reconciler"), autonomous,
            harness=judge_lib.harness_present())
    except judge_lib.JudgeError as failure:
        sys.stderr.write("composition error: {0}\n".format(failure))
        return EXIT_COMPOSITION
    # **A draft run cannot be judged by `synthesis`.** That judgment is a paid call on the metered
    # endpoint and it is the dearest single call most runs make — on a pass whose whole definition
    # is "$0 beyond the plan" it would be the only thing that spent anything, and it would spend
    # more than the panel it was judging usually does. The harness judge costs nothing and is the
    # default wherever `install.sh` has put it; a host reconciles in-session for nothing at all.
    if args.draft and reconciler == judge_lib.SYNTHESIS_AUTHOR:
        sys.stderr.write(
            "composition error: this draft run resolves its judgment to the `synthesis` persona, "
            "which is a paid call on a metered endpoint.\n"
            "  A draft pass costs nothing beyond the plan, and the judgment is the dearest single "
            "call most runs make — it cannot be the one thing a $0 pass pays for.\n"
            "  Install the harness judge, which judges on the subscription:\n"
            "    ./install.sh\n"
            "  or reconcile the run yourself with --reconciler host.\n")
        return EXIT_COMPOSITION

    # **The "$0" banner prints here and not earlier**, because every refusal that can stop a draft
    # run has now passed. A banner promising a run that costs nothing, printed above a composition
    # error that says the run cannot happen, is a claim about a run nobody made.
    if args.draft:
        for line in drafts_lib.banner():
            print(line)

    # Composition error, not a warning: `fidelity` and `source-credibility` require a `citation` on
    # every finding, so a seat carrying either with nothing to cite would have every finding it
    # returned rejected by the validator. Refused here, before the first paid call, naming the seats.
    starved = reference_required_seats(seats) if not args.refs else []
    # **A draft run retires such a seat rather than refusing the run.** The rule's own reason is
    # that the seat would be asked for a citation it has nothing to take one from and every finding
    # it returned would be thrown out — on a metered run that is money spent for nothing and
    # refusing is right. A draft pass spends nothing and is the mode meant for a seed document with
    # no source of truth, so the other lenses run and the citing seat is recorded as a seat that did
    # not report, with the reason, exactly as a context-overflowing seat is. `draft-review` ships
    # `requires_references: false` on the strength of this.
    if starved and args.draft:
        print("retiring {0} seat(s) for this draft pass: no source-of-truth references were "
              "supplied and {1} cites on every finding".format(
                  len(starved), " and ".join(sorted({seat["lens"] for seat in starved}))))
        for seat in starved:
            print("  {0} — retired; supply --ref to seat it".format(seat["reviewer_id"]))
    elif starved:
        sys.stderr.write(
            "composition error: {0} seat(s) require source-of-truth references and none were supplied.\n".format(
                len(starved)))
        for seat in starved:
            sys.stderr.write("  {0} carries the `{1}` lens, which cites on every finding\n".format(
                seat["reviewer_id"], seat["lens"]))
        sys.stderr.write(
            "Supply them with --ref <path>, repeatable, or compose a panel without those lenses — "
            "`{0}` is the shipped reference-free template.\n".format(FALLBACK_PANEL))
        return EXIT_COMPOSITION

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

    if resuming:
        _adopt_recorded_substitutions(runs_lib.read_manifest(run_dir), seats)

    for seat in seats:
        if seat.get("substitution"):
            print("re-seated {0}: {1}".format(seat["reviewer_id"], seat["substitution"]["reason"]))

    # **Which leg each seat sits on.** Two ways onto the harness leg and they are one state: a
    # `claude` seat the operator left to the session with `--skip-claude`, and — new with `--draft`
    # — any seat whose connector's driver `type` is `harness`, which is every seat of a draft run.
    # A seat retired for want of references sits on neither: it is recorded and not run.
    retired = {seat["reviewer_id"] for seat in starved} if args.draft else set()
    harness_leg = drafts_lib.is_harness_connector(connector)
    dispatched = [s for s in seats
                  if s["reviewer_id"] not in retired and not _is_harness_seat(s, args, harness_leg)]
    harness = [s for s in seats
               if s["reviewer_id"] not in retired and _is_harness_seat(s, args, harness_leg)]
    tier_default = args.tier or panel.get("tier")

    # The first of the two family counts. `min_families` is a target: a panel that cannot meet it
    # runs anyway, lens-diverse only, and says so. The second count — over the seats that actually
    # reported — is taken at wrap-up, because a seat can fail after this line.
    min_families = _min_families_target(args, panel)
    seated_families = sorted({seat["family"] for seat in seats if seat.get("family")})
    if len(seated_families) < min_families:
        print("min_families: {0} seated against a target of {1} ({2}) — the run proceeds and the "
              "reconciliation's method caveat will say so".format(
                  len(seated_families), min_families, ", ".join(seated_families) or "no family"))

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

    with_synthesis = reconciler == judge_lib.SYNTHESIS_AUTHOR

    # **The judge's seat is resolved here, not guessed at in the projection.** It is resolved by
    # the same `judge_lib.synthesis_seat()` the judge stage uses — the tier following the run's,
    # the family preferring one the panel seated — and then recorded in the manifest as
    # `judge_seat`, model included. `reconcile.py` reads that record back rather than repeating
    # the derivation, so the model the budget gate priced is the model the judgment call makes
    # even if the config is edited between the panel and the judgment. The projection used to
    # price this call on the dearest *seated* model, which is not what runs the judge.
    synthesis_model = args.synthesis_model
    judge_seat = None
    if with_synthesis:
        try:
            judge_seat = judge_lib.synthesis_seat(
                config_entry, panel,
                cli_tier=args.tier,
                seated_families=[seat["family"] for seat in seats if seat.get("family")],
                persona_tier=_synthesis_persona_tier(paths))
        except judge_lib.JudgeError as failure:
            sys.stderr.write("composition error: {0}\n".format(failure))
            return EXIT_COMPOSITION
        if synthesis_model:
            judge_seat["family_source"] = "--synthesis-model"
        else:
            synthesis_model = seating_lib.model_at(config_entry, judge_seat["tier"], judge_seat["family"])
        judge_seat["model"] = synthesis_model
        # **The judge's effort level is resolved here too, and recorded beside its family and tier.**
        # The top of that order is the template's own `synthesis.effort`, and Resolve is the only
        # stage that certainly holds the template: `reconcile.py` can only re-find it *by name*, so
        # a panel invoked by absolute path left it with nothing to read and the pin was dropped
        # without a word. Resolving it once here, from the template in memory and the seats as they
        # were resolved, makes the level a recorded decision like every other — `run` reads the
        # levels the seats carry, which is what the manifest is about to record them as.
        effort_level, effort_source = judge_lib.synthesis_effort(
            {"seats": seats}, config_entry, _synthesis_persona_frontmatter(paths), panel=panel)
        judge_seat["effort_level"] = effort_level
        judge_seat["effort_source"] = effort_source

    # --- Resolve, part two: the registry gate -----------------------------------------------------
    try:
        registry = registry_lib.load(paths, args.models)
    except (paths_lib.PathError, registry_lib.RegistryError) as failure:
        sys.stderr.write("{0}\n".format(failure))
        return EXIT_COMPOSITION
    # The judge is checked alongside the seats, because it is a paid call this run will make and an
    # unpriced one is the same hole: a seat left out of the projection is a budget gate that does
    # not gate. It is appended only when it is not already a seated model, so it is named once.
    #
    # **A draft run prices its harness seats too, at zero.** They make no call, so the gate is not
    # protecting money here; what it is protecting is the claim. A run that says it costs nothing
    # has to have looked at every seat's price to say so, and a harness model file whose price is
    # *missing* rather than zero is a model nobody has classified — exactly the entry the gate
    # exists to refuse. The shipped `claude-opus-5` carries an explicit 0, so it passes and the
    # projection prints a table of $0.0000 rows that says what the mode promises.
    priced = (dispatched + harness) if args.draft else list(dispatched)
    seat_models = [s["model"] for s in priced]
    to_price = list(seat_models)
    if synthesis_model and synthesis_model not in seat_models:
        to_price.append(synthesis_model)
    uncovered = registry.covers(to_price)
    if uncovered:
        sys.stderr.write("composition error: the model registry cannot price {0} model(s) this run will call.\n".format(len(uncovered)))
        for model, reason in uncovered:
            seat = next((s for s in priced if s["model"] == model), None)
            sys.stderr.write("  {0} resolves to {1}, which {2} at {3}\n".format(
                seat["reviewer_id"] if seat else "the judgment call", model, reason, registry.path))
        sys.stderr.write("Every resolved seat must be priced before dispatch: a seat left out of the "
                         "projection is a budget gate that does not gate. Fix:\n")
        for model, _reason in uncovered:
            sys.stderr.write("  python3 scripts/refresh_models.py --add {0}\n".format(model))
        return EXIT_COMPOSITION

    # The effort a seat is dispatched at is a property of the comparison, not a preference: a value
    # the model does not accept is refused here rather than dropped on the way to the provider.
    effort_problems = seating_lib.effort_errors(seats, registry)
    if effort_problems:
        sys.stderr.write("composition error: {0} seat(s) ask for an effort level their model cannot "
                         "be run at.\n".format(len(effort_problems)))
        for problem in effort_problems:
            sys.stderr.write("  {0}\n".format(problem))
        sys.stderr.write("Fix that model file's `effort` map, or refresh the vocabularies:\n"
                         "  python3 scripts/refresh_models.py\n")
        return EXIT_COMPOSITION

    manifest = _build_manifest(args, panel, panel_path, config_path, paths, run_dir, seats,
                               dispatched, harness, inputs, fingerprint, tier_default, registry,
                               min_families, seated_families, inference, reconciler, autonomous,
                               judge_seat, connector=connector, harness_leg=harness_leg)
    if resuming:
        manifest = _merge_resume(manifest, runs_lib.read_manifest(run_dir))
    runs_lib.write_manifest(run_dir, manifest)

    # A seat retired for want of references is recorded as a seat that did not report, with the
    # reason, the moment the manifest exists to record it on — the same shape the context-overflow
    # drop takes a few lines below, and for the same reason: `reconcile_core` reads the manifest to
    # say which composed seats are absent from every cluster, and a seat that was never going to run
    # must not be left `pending` and mistaken for one the host has yet to spawn.
    for seat in (starved if args.draft else []):
        runs_lib.update_seat(run_dir, seat["reviewer_id"], _retired_for_references(seat))

    # --- Project ---------------------------------------------------------------------------------
    document_chars = artifact_record["bytes"] + sum(r["bytes"] for r in reference_records)
    projection_seats = [{
        "reviewer_id": seat["reviewer_id"],
        "model": seat["model"],
        "prompt_tokens": budget_lib.approx_tokens(budget_lib.prompt_chars(persona_chars(seat["lens"], paths), document_chars)),
    } for seat in priced]
    projection = budget_lib.project(projection_seats, registry, args.budget_usd,
                                    with_synthesis=with_synthesis, synthesis_model=synthesis_model,
                                    synthesis_seat=judge_seat)
    print(budget_lib.render(projection))
    print("")
    # Written before the gate resolves, so even a run that refuses over budget leaves the numbers it
    # refused over in the manifest rather than only on the console. `decision` is filled in at each
    # of the gate's three exits below.
    _record_projection(run_dir, projection, None)

    overflowed = set(projection["context_overflows"])
    if overflowed:
        for reviewer_id in sorted(overflowed):
            runs_lib.update_seat(run_dir, reviewer_id, lambda seat: seat.update({
                "status": "failed",
                "failure_reason": "context-overflow",
                "error": "the composed prompt exceeds this model's context limit; never re-seated, the run is under-seated",
            }))
        dispatched = [s for s in dispatched if s["reviewer_id"] not in overflowed]
        # **Recomputed over the seats that will actually be dispatched.** The first projection is
        # what the operator was shown and it lists every seat, the overflowing one included; the
        # recorded one has to be the projection the run's billed spend is later compared against, or
        # the method caveat measures actual cost for three seats against a projection for four. The
        # synthesis allowance moves too: it is priced off the largest prompt in the panel, which may
        # be the very seat that overflowed. `excluded_seats` keeps the drop visible in the record.
        projection_seats = [row for row in projection_seats if row["reviewer_id"] not in overflowed]
        projection = budget_lib.project(projection_seats, registry, args.budget_usd,
                                        with_synthesis=with_synthesis, synthesis_model=synthesis_model,
                                        synthesis_seat=judge_seat)
        projection["context_overflows"] = sorted(overflowed)
        projection["excluded_seats"] = sorted(overflowed)
        _record_projection(run_dir, projection, None)

    # --- The spend gate, ahead of the budget gate because it is the prior question ----------------
    # "May this run pay for calls on this endpoint at all" comes before "is this particular
    # projection more than you meant to spend". A run can be comfortably under budget and still be
    # the first time anybody said yes to billing an account, which is the case --approve-budget
    # never covered: it approves an overrun, not the spending.
    # **The gate is asked over the seats this run will actually dispatch, not over the composed
    # panel.** `priced` is what the projection covers, and on a draft run it is every seat; what
    # decides whether anybody has to approve spending is the set of calls that will be made —
    # `dispatched`, after the overflow drop, plus the judgment call when this run makes one.
    spend_code = _spend_gate(args, run_dir, connector, dispatched, projection, autonomous,
                             judgment=with_synthesis)
    if spend_code is not None:
        return spend_code

    over = budget_lib.over_budget(projection)
    if over and not args.approve_budget:
        if autonomous:
            _record_projection(run_dir, projection, "refused-over-budget")
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
            _record_projection(run_dir, projection, "declined-over-budget")
            sys.stderr.write("not dispatched.\n")
            return EXIT_BUDGET
        _record_projection(run_dir, projection, "approved-over-budget")
    else:
        _record_projection(run_dir, projection,
                           "approved-over-budget" if over else "within-budget")

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

    code = _wrap_up(args, run_dir, results, harness, dispatched, elapsed, halt, held, resuming)
    if args.draft:
        stop = _draft_spawn_stage(argv, args, paths, run_dir, harness, inputs, code)
        if stop is not None:
            return stop
    return _judge(args, run_dir, reconciler, code, synthesis_model)


def _draft_spawn_stage(argv, args, paths, run_dir, harness, inputs, code):
    """Print the spawn block for every draft seat still owed a report, and stop. Or None to carry on.

    **This is the seat-leg twin of the harness judge stage, deliberately.** A script cannot spawn a
    harness agent; what it can do is compute everything the spawn needs and hand it over. So the run
    prints one exact block per seat, creates each seat's staging directory so the subagent has
    somewhere to write, and stops — and the session resumes this same command afterwards to reach
    the judge stage. One pattern for both legs rather than two.

    **The reports on disk decide who is still owed**, not the manifest's statuses, which is the same
    authority resume uses everywhere else. So the second pass through this asks only for the seats
    whose reports did not land or did not validate, and the pass after the last one asks for nobody
    and falls through to the judge.

    **The stop carries `_wrap_up`'s code, and on a first pass that is 0** — the same code the harness
    judge stage's stop returns. Both mean "stopped for the orchestrating session to act", and a
    caller that cannot tell them apart from the exit code alone is reading them correctly. A resume
    that reaches this block again is the different case: the session was asked once already, so a
    report still absent there is under-seated and `_wrap_up` says 3.
    """
    owed = [seat for seat in harness
            if not runs_lib.report_is_valid(run_dir, seat["reviewer_id"], _validate)[0]]
    if not owed:
        return None

    finding_schema = paths.require("reference", FINDING_SCHEMA)["path"]
    spawns = []
    for seat in owed:
        staging = runs_lib.make_staging_dir(run_dir, seat["reviewer_id"])
        persona = paths.require("persona", "lens-" + seat["lens"])
        frontmatter, _body = report_lib.parse_agent_file(persona["path"])
        try:
            context = dispatch.persona_context_paths(paths, frontmatter, warn=lambda _m: None)
        except paths_lib.PathError:
            context = [finding_schema]
        spawns.append({
            "reviewer_id": seat["reviewer_id"],
            "lens": seat["lens"],
            "model": seat["model"],
            "persona_path": persona["path"],
            "context_paths": list(context),
            "artifact": inputs["artifact"]["materialized_abs"],
            "artifact_label": inputs["artifact"]["path"],
            "artifact_revision": inputs["artifact"]["revision"],
            "references": [(r["materialized_abs"], r["path"]) for r in inputs["references"]],
            "staging_path": drafts_lib.staged_report(staging, seat["reviewer_id"]),
        })

    print("")
    for line in drafts_lib.spawn_instruction(
            run_dir, spawns, drafts_lib.resume_command([os.path.abspath(__file__)] + argv),
            render_script=os.path.join(SCRIPTS_DIR, "render_harness_report.py")):
        print(line)
    return code


def _judge(args, run_dir, reconciler, code, synthesis_model=None):
    """Run the Judge and Reconcile stages, or say how to.

    An autonomous run whose judgment comes from `synthesis` finishes the pipeline here, because
    there is nobody to type the second command: the whole point of the unattended path is that one
    invocation produces the reconciliation. Every other run stops after Collect and prints the
    command, because the judgment is the host's to write and the host is sitting right there.

    **A run whose judgment comes from the harness judge stops here too, and it runs `reconcile.py`'s
    first pass before it does.** A script cannot spawn a harness agent — there is no API for it, and
    inventing one would be inventing a second dispatch path — but the agent cannot judge clusters
    nobody has computed, and the only thing that computes them is that first pass. So the child is
    run for its worksheet, `judgment-request.json`, and it is the child that prints the spawn
    instruction over it. Printing the instruction without the worksheet spawned an agent that halts.

    The panel's own exit code wins over the reconciler's when the panel already failed — a run that
    halted on an auth failure has not become a patch problem — and otherwise the reconciler's code
    is returned, so a run whose reconciliation did not get written does not exit 0.
    """
    if reconciler == judge_lib.HARNESS_JUDGE_AUTHOR and args.reconcile != "off":
        if code == EXIT_TERMINAL:
            return code
        return _harness_judge_stage(args, run_dir, code)

    if args.reconcile == "off" or reconciler != judge_lib.SYNTHESIS_AUTHOR:
        print("")
        print("Next: reconcile. `reconcile.py` is the only writer of both reconciliation files.")
        print("  {0}".format(_printable(_reconcile_argv(args, run_dir, judge=False))))
        return code
    if code == EXIT_TERMINAL:
        return code

    child = _reconcile_argv(args, run_dir, judge=True)
    print("")
    print("-" * 72)
    print("Judge and Reconcile: {0}".format(_printable(child)))
    result = subprocess.run(child, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode:
        return result.returncode
    return _apply(args, run_dir, code)


def _harness_judge_stage(args, run_dir, code):
    """Run `reconcile.py`'s first pass, which writes the worksheet and prints the spawn instruction.

    **Two things have to happen here and only one of them is a print.** The agent answers the
    provisional clusters, and `judgment-request.json` is where they are: it is written by
    `reconcile.py`'s no-patch pass and by nothing else. So this runs that pass rather than
    reproducing either half of it — the clustering would be a second implementation of the
    algorithm, and the instruction would be a second copy of a contract `reconcile.py` also has to
    print when it is invoked directly.

    Exit 3 from the child is the expected stop: "a judgment patch is required", which is the whole
    point of the stage. Anything else is a real failure — an unresolvable artifact, a run
    directory this process cannot read — and is returned, because a run that could not even write
    the worksheet must not exit 0 with a spawn instruction nobody can follow.
    """
    child = _reconcile_argv(args, run_dir, judge=False)
    print("")
    print("-" * 72)
    result = subprocess.run(child, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        sys.stderr.write(result.stderr)
    if result.returncode == EXIT_UNDER_SEATED:
        return code
    return result.returncode or code


def _reconcile_argv(args, run_dir, judge):
    """The `reconcile.py` invocation, whether this run makes it or prints it for the host.

    **One builder for both**, because they had drifted once already: the child carried
    `--synthesis-model` and the printed line did not, so an operator copying the line would have got
    a judgment call the projection never priced. Everything that changes which call is made travels
    on both — the pin above all, then the operator paths, which are read as given and are not
    recoverable from the run directory.

    `judge` adds the flags that say a synthesis judgment is wanted now — the two that select it, and
    `--approve-spend` when this run carried one, because the child is about to make a **paid call on
    the same metered endpoint this run has already been approved for**. Without it the child refuses
    at exit 4 after the panel has been dispatched and billed: one invocation that both records a
    person's yes and then refuses on the grounds that nobody said it. They are deliberately absent
    from the printed form: that line is for a host who is about to write the patch itself, and a
    reconcile typed later reads the run's own recorded `spend_approval` rather than being handed a
    pre-typed approval to paste.

    The **harness-judge** stage runs this same bare invocation, with neither flag, because its
    first pass is what writes the worksheet and prints the spawn instruction. The `--judgment
    <staging>` line an operator runs afterwards is printed by that child, not built here: it names
    a path the child computed.
    """
    argv = [sys.executable, RECONCILE, "--run-dir", run_dir]
    if judge:
        argv += ["--reconciler", "synthesis", "--autonomous"]
        if args.approve_spend:
            argv += ["--approve-spend"]
    if args.synthesis_model:
        argv += ["--synthesis-model", args.synthesis_model]
    if args.workspace:
        argv += ["--workspace", args.workspace]
    if args.config:
        argv += ["--config", args.config]
    if args.models:
        argv += ["--models", args.models]
    return argv


def _printable(argv):
    """The argv as a line an operator can paste, quoted where a path would otherwise split."""
    return "python3 " + " ".join(shlex.quote(part) for part in argv[1:])


def _apply(args, run_dir, code):
    """The Apply stage: only when armed, only through the five-condition gate, only on a clean run.

    A reconciliation written over a run with a missing seat is still the product, but it is not a
    document to write back from unattended: the agreement counts it is about to apply edits on were
    computed against a panel that did not all report. So the gate is armed only after a clean run.
    """
    if args.auto_apply != "on":
        return code
    if code != EXIT_OK:
        print("")
        print("auto-apply not armed: this run did not finish clean (exit {0}), so the agreement "
              "counts behind any edit are not the ones the panel was composed to produce.".format(code))
        return code

    print("")
    print("-" * 72)
    child = [sys.executable, APPLY_FIXES, "--run-dir", run_dir, "--i-authored-this"]
    result = subprocess.run(child, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode or code


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
            usable=lambda _family, model: bool(model) and not registry.covers([model]),
            registry=registry)
        if replacement is None:
            print("{0}: no family left to re-seat onto; the run is under-seated".format(seat["reviewer_id"]))
            continue
        entry = registry.get(seat["model"]) or {}
        runs_lib.update_seat(run_dir, seat["reviewer_id"], _reseated(seat, entry, args.max_tokens))
        print("re-seated {0}: {1}".format(seat["reviewer_id"], seat["substitution"]["reason"]))
        moved.append(seat)
    return moved


def _retired_for_references(seat):
    """Mark a citing seat a reference-free draft run will not spawn. Recorded, never silent.

    `failed` rather than a status of its own, because every reader of a manifest already knows what
    `failed` means and `reconcile_core._missing_seats_caveat` already names such a seat in the
    reconciliation. What is new is the reason, which says the seat was never dispatched rather than
    that it was dispatched and did not come back.
    """
    def mutate(record):
        record["status"] = "failed"
        record["report"] = None
        record["failure_reason"] = "no-references"
        record["error"] = (
            "the `{0}` lens cites on every finding and this run supplied no source-of-truth "
            "references, so every finding it returned would fail validation. On a draft pass the "
            "seat is retired rather than the run refused; supply --ref to seat it.".format(
                seat.get("lens")))
    return mutate


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
        record["effort_tokens"] = seat.get("effort_tokens")
        record["max_tokens"] = registry_lib.cap_for(entry, run_cap)
        record["substitution"] = seat["substitution"]
    return mutate


# --- manifest ------------------------------------------------------------------------------------

def _min_families_target(args, panel):
    """`--min-families`, else the panel's, else the default. A target the run is measured against, never a gate.

    `--smoke-test` sets it to 1 below everything else it cannot override: a run with one model
    behind every seat has one family by construction, and holding it to a target of 2 would print
    a shortfall warning about a shortfall the operator asked for. `--draft` is the same case for the
    same reason — the harness leg serves one family — and it is the owner's standing rule that a
    single-family pass is first-class rather than a degraded one.
    """
    if args.min_families is not None:
        return args.min_families
    if args.smoke_test or args.draft:
        return 1
    target = panel.get("min_families")
    return DEFAULT_MIN_FAMILIES if target is None else target


def _run_tier(tier_default, seats):
    """`{resolved, source, unanimous, per_seat}` — the tier this run actually ran at.

    `tier_default` is only the *input* to seat resolution: `--tier`, else the panel's, and null when
    neither set one. Run 3 was dispatched `--tier standard`, every seat resolved to `standard`, and
    a reader of the manifest had to infer that from four seat records because nothing said it at the
    top level. `reconcile_core.method_caveat` and the reader after it both want one answer.

    A panel whose seats carry their own tiers has no single answer, and this says so rather than
    picking one: `resolved` is null, `unanimous` false, and `per_seat` lists what each one ran at.

    **`--model` is never the run's tier source.** A seat records `tier_source: "--model"` because
    the pin beat every tier level *for that seat*, and this block used to take the highest such
    source across the panel — so run 4 read `tier.source: "--model"` although every seat resolved
    `frontier` from `--tier` and exactly one seat was pinned. The tier and the model pin are
    different decisions. The pinned seats are named in `model_pins` instead, and `source` is the
    highest level that decided the tier itself, which is null only when every seat was pinned.
    """
    pairs = [(seat.get("tier"), seat.get("tier_source")) for seat in seats if seat.get("tier")]
    tiers = {tier for tier, _source in pairs}
    per_seat = {seat["reviewer_id"]: seat.get("tier") for seat in seats}
    pins = [seat["reviewer_id"] for seat in seats if seat.get("tier_source") == "--model"]
    tier_levels = [name for name in seating_lib.TIER_SOURCES if name != "--model"]
    if len(tiers) == 1:
        tier = pairs[0][0]
        # The highest-precedence level that decided any seat, by the seating module's own order.
        sources = {source for _tier, source in pairs if source}
        source = next((name for name in tier_levels if name in sources), None)
        return {"resolved": tier, "source": source, "unanimous": True, "per_seat": per_seat,
                "model_pins": pins}
    return {"resolved": None, "source": "per-seat", "unanimous": False,
            "requested": tier_default, "per_seat": per_seat, "model_pins": pins}


def _build_manifest(args, panel, panel_path, config_path, paths, run_dir, seats, dispatched, harness,
                    inputs, fingerprint, tier_default, registry, min_families, seated_families,
                    inference, reconciler, autonomous, judge_seat=None, connector=None,
                    harness_leg=False):
    """The manifest as it stands at Resolve: every seat pending, every input pinned."""
    artifact = inputs["artifact"]
    references = inputs["references"]
    connector = connector or {}
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
            # **A harness seat records a model only when the run resolved one for it.** On a draft
            # run it did: the seat sits on the harness connector and its model file is what the
            # spawn block names, so the id belongs in the record. Under `--skip-claude` the seat
            # resolved against the *metered* map and the subagent will not run that model at all,
            # so recording it would be a false audit line — null stays the honest answer there.
            "model": seat["model"] if (harness_seat and harness_leg) or not harness_seat else None,
            "connector": seat["connector"] if (harness_seat and harness_leg) or not harness_seat else None,
            "provider": None,
            "leg": "harness" if harness_seat else "openrouter",
            "input_delivery": "materialized-paths" if harness_seat else "inlined",
            # The abstract level and who chose it, beside the concrete parameter. A harness seat
            # carries the level and no parameter: the harness leg has no per-spawn effort knob, so
            # what the run *asked for* is knowable and what was *sent* is not.
            "effort_level": seat.get("effort_level"),
            "effort_source": seat.get("effort_source"),
            "effort": None if harness_seat else seat.get("effort"),
            "effort_tokens": None if harness_seat else seat.get("effort_tokens"),
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
            "upstream_unbilled_usd": None,
            "elapsed_s": None,
            "substitution": seat.get("substitution"),
            # A seat pinned to a concrete model carries the family that model belongs to, and this
            # is the record of the template's word being overruled by the model's own. Null on
            # every unpinned seat, and on a pinned one whose model belongs to the family the
            # template asked for anyway.
            "family_relabel": seat.get("family_relabel"),
            "model_pinned": bool(seat.get("pinned")),
        })
        if harness_seat:
            seat_records[-1]["note"] = ("dispatched by the host session as a harness subagent; "
                                        "render with render_harness_report.py")

    return {
        "run_id": args.run_id or os.path.basename(os.path.abspath(run_dir)),
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "panel": panel.get("name", args.panel),
        "panel_path": panel_path,
        # How this run got its panel: `requested` is the `--panel` the operator named or null,
        # `heuristic` is what the artifact's name suggested or null when nothing read it, `resolved`
        # is what ran, and `reason` says why the last two differ when the references rule moved it.
        "panel_inference": inference,
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
        # The endpoint this run billed, named rather than inferred from a driver type: two
        # connectors can share one driver and bill different accounts.
        "connector": {
            "name": connector.get("name"),
            "type": connector.get("type"),
            "base_url": connector.get("base_url"),
            "billing": connector.get("billing"),
            "requires_approval": connectors_lib.requires_approval(connector),
            "path": connector.get("path"),
            "root": connector.get("root"),
        },
        # Filled by the spend gate a few lines after this manifest is written: whether a call on
        # that endpoint needed a person's yes, whether it got one, and where the yes came from.
        "spend_approval": None,
        "workspace": paths.workspace,
        # The cascade's audit record: the search order, the root each loaded file actually came
        # from, and the ones a workspace override supplied. `config-fragment` appears only when a
        # workspace `config.json` deep-merged over the packaged one.
        "roots": paths.roots_block(),
        "tier_default": tier_default,
        # The tier the run **resolved to**, beside the one it was asked for. `tier_default` is the
        # input — `--tier`, else the panel's, else null; `tier` is the answer, with the level that
        # decided it. See `_run_tier`.
        "tier": _run_tier(tier_default, seats),
        "max_tokens_requested": args.max_tokens,
        # A run that proves the pipeline rather than the artifact. Read by
        # `reconcile_core.method_caveat`, which says so at the top of the reconciliation, and it is
        # a top-level field rather than a note on the seats because it governs the whole document.
        "smoke_test": bool(args.smoke_test),
        "smoke_test_model": args.smoke_test,
        # A pass that ran on the subscription and claims no corroboration. Top level rather than a
        # note on the seats, for the same reason `smoke_test` is: it governs how the whole
        # reconciliation should be read, and `reconcile_core.method_caveat` opens with it.
        "draft": bool(args.draft),
        "budget_usd": args.budget_usd,
        # Filled at Project, a few lines after this manifest is written, and left null on a run that
        # never reached the pre-flight. Carries the per-seat table, the totals, the budget and the
        # decision the gate came to, so `cost_usd_total` has something to be compared against.
        "projection": None,
        "skip_claude": args.skip_claude,
        # Resolved here rather than left as `default`, so `reconcile.py` reads a decision instead of
        # asking its own stdin a question the panel already answered.
        "reconciler": reconciler,
        "autonomous": autonomous,
        # The judge's seat as Resolve worked it out, so the projection that gated this run and
        # the call `reconcile.py` later makes are the same seat rather than two derivations of
        # one rule. Null on a run whose judgment comes from the host: it makes no such call.
        "judge_seat": judge_seat,
        "seats": seat_records,
        "families_dispatched": sorted({s["family"] for s in dispatched}),
        # The target and both counts against it. `seated` is over every expected seat, harness seats
        # included; `reporting` is filled at wrap-up, over the seats whose reports validated, and
        # stays null on a run that never reached wrap-up. `reconcile_core.method_caveat` reads this
        # block and names it when either count is short.
        "min_families": {
            "target": min_families,
            "seated": len(seated_families),
            "families_seated": list(seated_families),
            "reporting": None,
            "families_reporting": None,
        },
        "min_families_target": min_families,
        "elapsed_s": None,
        "cost_usd_total": None,
        "failures": [],
    }


def _spend_gate(args, run_dir, connector, seats, projection, autonomous, judgment=False):
    """The connector's own gate: may this run bill an account at all? None means yes, carry on.

    A `metered` connector carries `requires_approval: true` by default, and a metered call without
    an explicit yes is refused. The three exits mirror the budget gate's, deliberately, because an
    operator has already learned that shape: an **autonomous** run refuses with exit 4 and never
    prompts, an **interactive** run asks on stdin, and `--approve-spend` skips the question. What
    differs is what is being approved — the endpoint, not the amount — which is why it is a separate
    flag: a run that has approved a $9 overrun has said nothing about whether paying is allowed, and
    a run that has approved paying has said nothing about how much.

    **It is asked only when this run will make a call on that endpoint.** `seats` is the dispatched
    set and `judgment` says whether the judgment call itself lands here; a run that dispatches
    nothing — every seat on the harness leg under `--draft`, or an all-`claude` panel under
    `--skip-claude` — has nothing to approve, and asking anyway over a $0.00 projection trains an
    operator to say yes without reading.

    Whatever the answer, it is recorded in the manifest as `spend_approval`, so the audit trail
    shows that a person said yes rather than that a default did.
    """
    block = connectors_lib.spend_gate(connector, seats, judgment=judgment)
    if not block["required"]:
        block = dict(block, granted=None, source=None)
        if args.draft:
            # The one thing a reader of a draft run's manifest should not have to work out from the
            # connector file: the gate did not fire because there was nothing to gate.
            block["reason"] = "no metered connector seated"
        elif connectors_lib.requires_approval(connector):
            # The other way `required` comes back false: the endpoint does gate, and this run has
            # nothing on it. Said in the manifest rather than left for a reader to infer from an
            # empty `seats` array beside a metered connector block.
            block["reason"] = "no seat dispatched to this connector and no judgment call on it"
        _record_spend_approval(run_dir, block)
        return None

    if args.approve_spend:
        _record_spend_approval(run_dir, dict(block, granted=True, source="--approve-spend"))
        return None

    # The judgment call is a call on this endpoint like any other and belongs in the list the
    # refusal names, or a run whose only paid call is the judgment refuses without naming anything.
    named = list(seats) + ([{"reviewer_id": "the judgment call"}] if judgment else [])
    message = connectors_lib.refusal(connector, named, projection.get("projection_usd"))
    if autonomous:
        _record_spend_approval(run_dir, dict(block, granted=False, source="autonomous-refusal"))
        _record_projection(run_dir, projection, "refused-spend-not-approved")
        sys.stderr.write(message + "\n  No paid call was made.\n")
        return EXIT_BUDGET

    print(message)
    answer = input("Allow paid calls on {0}? [y/N] ".format(connector.get("name") or "?")).strip().lower()
    if answer not in ("y", "yes"):
        _record_spend_approval(run_dir, dict(block, granted=False, source="declined-interactively"))
        _record_projection(run_dir, projection, "declined-spend-not-approved")
        sys.stderr.write("not dispatched.\n")
        return EXIT_BUDGET
    _record_spend_approval(run_dir, dict(block, granted=True, source="approved-interactively"))
    return None


def _record_spend_approval(run_dir, block):
    """Put the spend gate's answer in the manifest. Written even when nothing was gated."""
    manifest = runs_lib.read_manifest(run_dir)
    manifest["spend_approval"] = block
    runs_lib.write_manifest(run_dir, manifest)


def _record_projection(run_dir, projection, decision):
    """Put the pre-flight in the manifest, with the decision the budget gate came to.

    Run 3 printed "projected $2.30 against a budget of $5.00", was billed $2.75, and left nothing in the
    manifest to compare the two — the projection lived on the console and died with it. Nothing
    metered the difference, and `reconcile_core.method_caveat` had no figure to name.

    Written at Project, before the first paid call, and re-written once the gate resolves. No lock:
    Project runs before any seat is claimed, so this is the only writer at this point in the run.
    """
    manifest = runs_lib.read_manifest(run_dir)
    manifest["projection"] = dict(projection, decision=decision)
    runs_lib.write_manifest(run_dir, manifest)


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
        seat["effort_tokens"] = record.get("effort_tokens")
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
                          "usage", "reasoning_tokens", "cost_usd", "upstream_unbilled_usd",
                          "elapsed_s", "provider", "effort", "effort_tokens",
                          "max_tokens", "model", "connector", "substitution", "failure_reason", "error",
                          "claimed_at", "claimed_by", "stale_lease", "string_truncations", "dispatches",
                          "unavailable_model"):
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

    `dispatching` is the one status this step will not touch **while the lease behind it is live**.
    A seat whose owning process is gone, or whose claim is older than any dispatch could be, is a
    stale lease: it is demoted to `failed` with `failure_reason: "stale-lease"` and re-dispatched,
    exactly as an `ok` seat with a missing report is. Held forever was the old behaviour and it meant
    a run killed mid-dispatch could never be resumed — `--fresh` re-dispatched and re-paid for the
    whole panel, which is not what resume is for. `runs_lib.lease_is_stale` decides.
    """
    manifest = runs_lib.read_manifest(run_dir)
    by_id = {seat.get("reviewer_id"): seat for seat in manifest.get("seats") or []}

    to_dispatch, kept, held = [], [], []
    for seat in dispatched:
        reviewer_id = seat["reviewer_id"]
        record = by_id.get(reviewer_id) or {}
        status = record.get("status")
        if status == "failed" and record.get("failure_reason") == "context-overflow":
            continue

        valid, why = runs_lib.report_is_valid(run_dir, reviewer_id, _validate)

        if status == "dispatching":
            # A report on disk settles it: the seat finished and the process died before it could
            # update the manifest. Nothing to re-dispatch and nothing to hold.
            if valid:
                kept.append((reviewer_id, "a validated report is already on disk; the lease is spent"))
                continue
            stale, lease = runs_lib.lease_is_stale(record)
            if not stale:
                held.append((reviewer_id,
                             "already `dispatching` — another process owns this seat ({0})".format(lease)))
                continue
            runs_lib.update_seat(run_dir, reviewer_id, _stale_lease(lease, why))
            print("re-claiming {0}: stale lease — {1}".format(reviewer_id, lease))
            to_dispatch.append(seat)
            continue

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


def _stale_lease(lease, why):
    """Move a seat left `dispatching` by a dead process back to `failed`, so resume can re-claim it.

    The claim is cleared along with the status: a lease nobody holds must not be readable as one
    somebody does, and the re-dispatch writes a fresh one a moment later.
    """
    def mutate(seat):
        seat["status"] = "failed"
        seat["report"] = None
        seat["failure_reason"] = "stale-lease"
        seat["error"] = ("the manifest left this seat `dispatching` and {0}; {1}. Resume re-claimed "
                         "it.".format(why, lease))
        seat["stale_lease"] = {"claimed_at": seat.get("claimed_at"),
                               "claimed_by": seat.get("claimed_by"), "reason": lease}
        seat["claimed_by"] = None
    return mutate


def _wrap_up(args, run_dir, results, harness, dispatched, elapsed, halt, held, resuming=False):
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

    # **A harness seat whose report has been moved in has reported, and the manifest has to say so.**
    # Nothing else folds one: the operator's `mv` is the last step of that leg and it writes no
    # record, so a seat that had answered went on reading `pending` for the life of the run. That is
    # the same lie resume exists to prevent one direction up — a manifest asserting something the
    # directory contradicts — and it is what makes a draft pass resumable seat by seat.
    for seat in harness:
        landed_path = os.path.join(run_dir, seat["reviewer_id"] + ".json")
        if runs_lib.report_is_valid(run_dir, seat["reviewer_id"], _validate)[0]:
            runs_lib.update_seat(run_dir, seat["reviewer_id"],
                                 lambda record, path=landed_path: seat_record_from_report(record, path))

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

    # Harness seats are expected to sit `pending` until the host spawns them, so on a mixed
    # `--skip-claude` run they are not what "under-seated" means; only the OpenRouter leg's own
    # seats are counted against exit 3 there. A **resumed draft** run is the exception and is handled
    # at the bottom of this function, where the harness leg is the whole panel and the session has
    # already been asked for it once.
    #
    # **The count comes from the reports on disk, not from the manifest's status.** A manifest that
    # says `ok` for a seat whose report is gone would otherwise let the run print "2 of 2" and exit 0
    # over a directory holding one report — the exact lie resume exists to prevent.
    openrouter_seats = [s for s in manifest["seats"] if s.get("leg") != "harness"]
    reporting = [s for s in openrouter_seats
                 if runs_lib.report_is_valid(run_dir, s["reviewer_id"], _validate)[0]]
    # Harness seats whose report has already been rendered and moved in. They are not in `reporting`
    # — the exit code counts the leg this process is responsible for — but they **have** reported,
    # which is what the family count below is about. A draft run has no other kind of seat, so
    # leaving them out made every draft pass read as "0 families reporting" forever.
    harness_reporting = [s for s in manifest["seats"] if s.get("leg") == "harness"
                         and runs_lib.report_is_valid(run_dir, s["reviewer_id"], _validate)[0]]

    # The second family count, over the seats that actually reported — the one the agreement tiers
    # are worth anything against. A harness seat that has not been rendered yet is not counted;
    # `reconcile.py` recomputes `families_reporting` from the reports it finds either way.
    families_reporting = sorted({s.get("family") for s in reporting + harness_reporting
                                 if s.get("family")})
    min_families = _record_min_families(manifest, families_reporting)
    runs_lib.write_manifest(run_dir, manifest)

    print("Panel `{0}` on {1}".format(manifest["panel"], manifest["artifact"]))
    print("Run directory: {0}".format(run_dir))
    print("Seats reporting: {0} of {1} · harness pending: {2} · failed: {3} · held: {4} · {5:.0f}s{6}".format(
        len(reporting) + len(harness_reporting), len(openrouter_seats) + len(harness),
        max(len(harness) - len(harness_reporting), 0), len(manifest["failures"]), len(held), elapsed,
        " · ${0:.4f}".format(total_cost) if cost_known else ""))
    if min_families["reporting"] < min_families["target"]:
        print("Families reporting: {0} of a target {1} ({2}) — {3}".format(
            min_families["reporting"], min_families["target"],
            ", ".join(families_reporting) or "none",
            "this run is lens-diverse only" if min_families["reporting"] <= 1
            else "cross-family corroboration is thinner than the panel asked for"))
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
    landed = {s["reviewer_id"] for s in harness_reporting}
    for seat in harness:
        print("-" * 72)
        if seat["reviewer_id"] in landed:
            print("{0} — reported on the harness leg; validated report on disk".format(seat["reviewer_id"]))
        else:
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
    # **On a draft run the harness leg counts — but only on a resume pass.** The distinction is the
    # one `_harness_judge_stage` already draws: a first pass that has printed the spawn block and
    # holds no report has not failed, it has *stopped for the orchestrating session to act*, exactly
    # as the harness judge stage stops for the session to spawn the judge. That stage returns the
    # panel's own code — 0 on a clean run — and the spawn-block stop has to read the same, or two
    # halts that mean the identical thing report differently to whoever scripted them.
    #
    # A **resume** pass is where a missing or invalid harness report is a real failure: the session
    # was asked for those reports, says it has supplied them, and the directory disagrees. Exiting 0
    # there would let a run with nothing in it read as a finished panel, which is what the harness
    # leg's share of the exit code exists to prevent. So it is counted then, and only then, for the
    # same reason a failed OpenRouter seat is counted always.
    if args.draft and resuming and len(harness_reporting) < len(harness):
        return EXIT_UNDER_SEATED
    return EXIT_OK


def _record_min_families(manifest, families_reporting):
    """Fill the reporting half of the manifest's `min_families` block. Returns the completed block.

    Resolve wrote `target`, `seated` and `families_seated`; this is the count that matters, because a
    seat that failed cannot corroborate anything. A run resumed from a manifest written before this
    block existed gets one built here, so `method_caveat` never has to guess at the target.
    """
    block = manifest.get("min_families")
    if not isinstance(block, dict):
        block = {
            "target": manifest.get("min_families_target") or DEFAULT_MIN_FAMILIES,
            "seated": len({s.get("family") for s in manifest.get("seats") or [] if s.get("family")}),
            "families_seated": sorted({s.get("family") for s in manifest.get("seats") or [] if s.get("family")}),
        }
    block["reporting"] = len(families_reporting)
    block["families_reporting"] = list(families_reporting)
    manifest["min_families"] = block
    return block


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

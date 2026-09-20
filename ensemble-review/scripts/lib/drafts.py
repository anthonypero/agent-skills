"""The draft pass: a whole panel on harness subagents, at $0 beyond the owner's plan.

`run_panel.py --draft` is the cheap mode beside `--smoke-test`, and the two are not the same thing
and must never read as the same thing. A **smoke test** pins every seat to one cheap model to prove
the pipeline end to end; it says nothing about the artifact and its reconciliation says so. A
**draft pass** is a real review by four lenses, run on one subscription model, and what it cannot
claim is **corroboration**: one mind agreeing with itself under four lens prompts is not two minds
agreeing. So the two caveats are written in different words, deliberately — "not evidence" against
"one family's opinion, uncorroborated" — because a reader who conflates them will either throw away
findings worth reading or cite agreement counts that mean nothing.

**Standing rule this implements (owner ruling, 2026-09-19).** Opus is allowed on subscription
early-draft reviewer seats; Fable never runs a reviewer seat; the once-per-stage promotion gate
still seats no Claude family; and single-family and single-tier passes are first-class — the tool
must never require multiple families in order to run.

**How a draft run reaches the harness leg.** It composes against the `harness` connector rather than
the config's `default_connector`, so the tiers × families map it seats from is the one built out of
the model files that name that endpoint — `claude-opus-5` today. Every seat therefore resolves onto
a connector whose driver `type` is `harness`, and `run_panel.py` marks every such seat as a
**harness seat**: the same state `--skip-claude` produces for a `claude` seat, which is what lets
the existing staging paths, `render_harness_report.py` and the operator's move-in carry the whole
leg with no second implementation. Nothing is dispatched, so no driver runs and no call is made —
and `backends/harness.py` refuses loudly if one ever is.

**A draft pass costs nothing, and that is enforced rather than hoped for.** A template whose seats
would land on a `billing: metered` connector is refused before the run directory is claimed, naming
the seat, because the mode is *defined* by its price. An operator who wants a cheap metered pass
wants `--tier fast` on an ordinary panel, which is a different thing and is priced and gated like
any other run.
"""

import os

# The connector a draft run composes against, and the driver `type` that marks a connector as the
# harness leg. The name is what `config.json` would put in `default_connector`; the type is what
# `run_panel.py` tests a seat's endpoint against, because a project may name its own copy of the
# connector anything it likes and still mean the harness.
HARNESS_CONNECTOR = "harness"
HARNESS_DRIVER = "harness"

# The model override the orchestrating session passes on every draft spawn. It is a word the harness
# understands and not a catalogue id: the model file's `claude-opus-5` is the registry's name for
# the seat, and this is what the spawn actually says. Never `fork` — a fork inherits the parent's
# model, which is the one thing the standing rule forbids on a reviewer seat.
SPAWN_MODEL = "opus"

# How long a digest a harness seat may return. The same 2000 characters the OpenRouter leg's digest
# is capped at, because inter-agent messages truncate and disk is the channel either way.
DIGEST_CHARS = 2000

# The reconciliation's opening paragraph on a draft run. Prepended, never appended, for the reason
# the smoke-test prologue is: it says what the caveat under it is a caveat *about*, and a reader who
# stops after the first paragraph has to have read it.
PROLOGUE = (
    "**This run is a draft pass and carries no corroboration claim.** Every seat ran as a harness "
    "subagent on one subscription model behind a different lens, so the family target was 1 and no "
    "cluster here can carry cross-family corroboration at any tier: what two seats agree on is one "
    "mind agreeing with itself under two lens prompts. This is not a smoke test — the lenses are "
    "real and the findings are worth reading one by one — but the agreement counts, the tiers and "
    "the run verdict are not evidence that a defect is real, and nothing here should promote a "
    "document. Run a multi-family panel for that."
)


def is_harness_connector(connector):
    """Whether this connector file describes the harness leg rather than an endpoint to call."""
    return (connector or {}).get("type") == HARNESS_DRIVER


def banner():
    """The console banner a draft run opens with. The twin of the smoke-test banner, in its shape."""
    line = "=" * 72
    return [
        line,
        "DRAFT PASS — every seat is a harness subagent on the subscription.",
        "This run costs nothing beyond the plan and makes no metered call at all. One family",
        "behind four lenses: the findings are real and the agreement counts are not — no cluster",
        "here can carry cross-family corroboration at any tier. The manifest carries `draft: true`",
        "and the reconciliation opens by saying the run carries no corroboration claim. Promote a",
        "document on a multi-family panel, never on this.",
        line,
    ]


def metered_refusal(blocked, tier_hint="fast"):
    """The refusal when `--draft` is asked to seat a panel a metered endpoint would serve.

    A draft pass is *defined* as costing nothing beyond the plan, so this is a composition error
    rather than a prompt or a gate: there is no answer the operator could give that would make the
    run a draft pass. The message names every offending seat, the family it asked for, the model and
    the endpoint that would have billed it, and the flag that does what they probably meant.
    """
    lines = [
        "composition error: --draft cannot seat {0} of this panel's seat(s), because a metered "
        "endpoint is what serves them.".format(len(blocked)),
    ]
    for record in blocked:
        if record.get("kind") == "pin":
            lines.append(
                "  {0} is pinned to {1}, which connector {2!r} serves (billing: {3})".format(
                    record.get("reviewer_id"), record.get("model"), record.get("connector"),
                    record.get("billing")))
            continue
        lines.append(
            "  {0} asks for family {1!r} at tier {2!r}, which the harness leg does not serve; "
            "{3} would serve it on connector {4!r} (billing: {5})".format(
                record.get("reviewer_id"), record.get("family"), record.get("tier"),
                record.get("model") or "a metered model", record.get("connector"),
                record.get("billing")))
    lines.append(
        "A draft pass is defined by its price: it runs on harness subagents on the owner's plan and "
        "makes no metered call at all, so a seat that would bill an account is not a draft seat and "
        "cannot be made into one by approving the spend.")
    lines.append(
        "  For a cheap **metered** pass, drop --draft and run the panel at a cheaper tier: "
        "--tier {0}. It is priced, budgeted and spend-gated like any other run.".format(tier_hint))
    lines.append(
        "  For a draft pass, use a panel whose seats the harness leg serves — `draft-review` is the "
        "shipped one — or give the families you want a model file naming the `harness` connector.")
    return "\n".join(lines) + "\n"


def spawn_instruction(run_dir, spawns, resume_command, render_script=None):
    """The exact spawn block the orchestrating session follows, one per seat. Returns lines.

    This is the seat-leg twin of `judge.spawn_instruction`, and it is deliberately the same shape:
    a script cannot spawn a harness agent, so the run stops here, prints what the session has to do,
    and is **resumed** afterwards to reach the judge stage. One pattern, not two.

    Everything a seat is told to read is named **absolutely**, because the subagent is told to read
    those paths and nothing else and a relative path resolved against a session's working directory
    is a different promise. The artifact and the references are the pinned copies in `inputs/`, never
    the working tree: a mid-run edit must not give two seats two different documents. The report goes
    to the seat's **own** staging directory outside the run directory, so no seat is handed a path
    into the directory holding its siblings' reports.
    """
    lines = [
        "=" * 72,
        "DRAFT PASS — spawn {0} harness subagent(s). A script cannot spawn one, so this run".format(len(spawns)),
        "stops here, exactly as the judge stage does, and is resumed once the reports are in.",
        "",
        "  Spawn all {0} in ONE message so they run concurrently and blind.".format(len(spawns)),
        "  Every spawn passes an explicit model override of `{0}`. Never `subagent_type: \"fork\"`:".format(SPAWN_MODEL),
        "  a fork inherits the parent's model, and a reviewer seat may never run on Fable.",
        "",
    ]
    for spawn in spawns:
        lines.extend(_seat_block(spawn))
        lines.append("")

    lines.extend([
        "Then, for each report, validate and render it where it lies and move it in:",
        "",
    ])
    for spawn in spawns:
        staged = spawn["staging_path"]
        lines.append("  python3 {0} {1} --model {2}".format(
            render_script or "scripts/render_harness_report.py", staged, spawn["model"]))
        lines.append("  mv {0} {1} {2}/".format(staged, staged[:-5] + ".md", run_dir))
    lines.extend([
        "",
        "Then resume this run to reach the judge stage:",
        "",
        "  {0}".format(resume_command),
        "",
        "Resuming re-reads the reports from disk, so a seat whose report did not validate is the",
        "only one you are asked to spawn again.",
        "=" * 72,
    ])
    return lines


def _seat_block(spawn):
    """One seat's brief: the model, the persona, what it may read, and where it writes."""
    lines = [
        "-" * 72,
        "  seat:         {0}   (lens `{1}`)".format(spawn["reviewer_id"], spawn["lens"]),
        "  model:        {0}   (explicit override on the spawn; registry id {1})".format(
            SPAWN_MODEL, spawn["model"]),
        "  system message, verbatim and in this order:",
        "                {0}   (everything AFTER the frontmatter)".format(spawn["persona_path"]),
    ]
    for path in spawn.get("context_paths") or []:
        lines.append("                {0}".format(path))
    lines.append("  artifact:     {0}   (as `{1}`)".format(spawn["artifact"], spawn["artifact_label"]))
    references = spawn.get("references") or []
    if references:
        for path, label in references:
            lines.append("  reference:    {0}   (as `{1}`)".format(path, label))
    else:
        lines.append("  reference:    none supplied — say so in `method_notes`")
    lines.extend([
        "  write to:     {0}".format(spawn["staging_path"]),
        # **The envelope fields no script fills in.** `render_harness_report.py` stamps the audit
        # fields it can derive from the filename and its own flags — `schema_version`,
        # `reviewer_id`, `lens`, `family`, `leg`, `model` — and it cannot know which document the
        # seat read. So `artifact` is the seat's to write, and a report without it fails validation
        # after the turn has been spent, which is the one failure this block exists to prevent.
        "  the report must carry, at top level:",
        "                \"artifact\": \"{0}\"".format(spawn["artifact_label"]),
        "                \"artifact_revision\": \"{0}\"".format(spawn.get("artifact_revision") or ""),
        "                \"verdict\", \"summary\", \"findings\", \"method_notes\"",
        "                (schema_version, reviewer_id, lens, family, model and leg are stamped",
        "                 for you when the report is rendered — do not invent them)",
        "  return:       a digest under {0} characters — reviewer id, verdict, counts by".format(DIGEST_CHARS),
        "                severity, the claim lines of its top three findings, and the report path.",
        "",
        "    Read ONLY the paths above. Do not look for, read or ask about any other reviewer's",
        "    output: blindness is the invariant this panel is worth anything for. Write the report",
        "    as one JSON object against the finding schema, to the staging path and nowhere else —",
        "    not into the run directory, which holds its siblings' reports.",
    ])
    return lines


def resume_command(argv):
    """The `run_panel.py` line that resumes this run, as a string an operator can paste."""
    import shlex
    return "python3 " + " ".join(shlex.quote(part) for part in argv)


def staged_report(staging_dir, reviewer_id):
    """Where one seat writes: `<staging dir>/<reviewer-id>.json`, the name the renderer reads back."""
    return os.path.join(staging_dir, reviewer_id + ".json")

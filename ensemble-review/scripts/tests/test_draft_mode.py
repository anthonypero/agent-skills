#!/usr/bin/env python3
"""`--draft`: a whole panel on harness subagents, at $0 beyond the owner's plan.

The mode is defined by two promises and this file is mostly about holding it to them.

**It costs nothing.** Not "it is cheap" and not "it is gated" — nothing. So a panel a metered
endpoint would serve is refused before the run directory is claimed rather than priced and
approved; the judgment cannot fall to the `synthesis` persona, which is a paid call; the spend gate
never fires and the manifest says why; and no driver is ever reached, which the fake backend's own
call log is what proves.

**It claims no corroboration.** One family behind four lenses is one mind agreeing with itself, and
the reconciliation has to say that in words a reader cannot mistake for the smoke test's "this is
not evidence at all". Both prologues are asserted here, together, so the distinction cannot quietly
collapse into one sentence.

No network and no paid call anywhere in this file: the metered side is `fake_backend.py` and the
harness side dispatches nothing at all.

    python3 scripts/tests/test_draft_mode.py
"""

import io
import json
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import harness  # noqa: E402
import render_harness_report  # noqa: E402
import run_panel  # noqa: E402
from backends import harness as harness_driver  # noqa: E402
from lib import connectors as connectors_lib  # noqa: E402
from lib import drafts as drafts_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import runs as runs_lib  # noqa: E402

RUN_ID = "2026-09-19-1"

DRAFT_LENSES = ("consistency", "adversarial")


class DraftCase(unittest.TestCase):
    """A workspace with both legs: the metered `fake` connector, and a `harness` one beside it."""

    def setUp(self):
        # The other endpoint is **metered**, as the shipped one is: that is what makes the refusal
        # below a refusal rather than an ordinary missing cell, and what a draft run has to steer
        # around on a real machine.
        self.workspace = harness.Workspace(billing="metered")
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.model = self.workspace.harness_leg()
        self.workspace.install_harness_judge()
        self.panel = self.workspace.path("draft-panel.json")
        _write_json(self.panel, {
            "name": "draft-panel",
            "description": "Every seat on the harness leg.",
            "requires_references": False,
            "min_families": 1,
            "tier": "standard",
            "reconciler": "default",
            "seats": [{"lens": lens, "family": "claude"} for lens in DRAFT_LENSES],
        })
        self.run_dir = self.workspace.path("reviews", RUN_ID)

    def run_panel(self, extra=None, panel=None, out=None):
        argv = ["--panel", panel or self.panel, "--artifact", self.workspace.artifact,
                "--out", out or self.run_dir, "--workspace", self.workspace.root,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--autonomous", "--reconcile", "off", "--draft"] + list(extra or [])
        return _capture(argv)

    def manifest(self, run_dir=None):
        return runs_lib.read_manifest(run_dir or self.run_dir)

    def seat_ids(self):
        return [seating_id(lens) for lens in DRAFT_LENSES]

    def land_reports(self, only=None):
        """Write, render and move in one valid harness report per seat — the operator's own steps."""
        manifest = self.manifest()
        for seat in manifest["seats"]:
            if only is not None and seat["reviewer_id"] not in only:
                continue
            staging = runs_lib.staging_dir(self.run_dir, seat["reviewer_id"])
            staged = drafts_lib.staged_report(staging, seat["reviewer_id"])
            document = dict(harness.valid_report(),
                            artifact=manifest["artifact"],
                            artifact_revision=manifest["artifact_revision"])
            _write_json(staged, document)
            code = render_harness_report.main([staged, "--model", self.model])
            self.assertEqual(code, 0)
            for suffix in (".json", ".md"):
                os.replace(staged[:-5] + suffix,
                           os.path.join(self.run_dir, seat["reviewer_id"] + suffix))


# --- a draft-pass template refuses to run as anything else ---------------------------------------

class DraftOnlyTemplateTest(DraftCase):
    """`draft_only: true` is the template saying what it is, and the run holding it to it.

    Without it, `run_panel.py --panel draft-review` with no `--draft` composed those same four
    Claude seats against the **metered** endpoint, priced them at real money and was one
    `--approve-spend` from billing for them. The flag is what selects the harness endpoint, so the
    only thing that could close that was a declaration in the file and a refusal at template load.
    """

    def setUp(self):
        super(DraftOnlyTemplateTest, self).setUp()
        self.draft_only = self.workspace.path("draft-only-panel.json")
        _write_json(self.draft_only, {
            "name": "draft-only-panel",
            "description": "Every seat on the harness leg.",
            "requires_references": False,
            "draft_only": True,
            "min_families": 1,
            "tier": "standard",
            "reconciler": "default",
            "seats": [{"lens": lens, "family": "claude"} for lens in DRAFT_LENSES],
        })

    def plain(self, extra=None, panel=None, out=None):
        """The same invocation the draft tests use, without `--draft`."""
        argv = ["--panel", panel or self.draft_only, "--artifact", self.workspace.artifact,
                "--out", out or self.run_dir, "--workspace", self.workspace.root,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--autonomous", "--reconcile", "off"] + list(extra or [])
        return _capture(argv)

    def test_it_refuses_without_the_flag_and_says_how_to_run_it(self):
        code, _out, err = self.plain()
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("draft-only-panel", err)
        self.assertIn("$0 draft pass", err)
        self.assertIn("--draft", err)

    def test_the_refusal_leaves_nothing_behind_and_calls_nothing(self):
        self.plain()
        self.assertFalse(os.path.isdir(self.run_dir), "the refusal left a run directory behind")
        self.assertEqual(self.workspace.calls(), [])

    def test_skip_claude_is_not_the_same_thing_and_does_not_satisfy_it(self):
        """It leaves `claude` seats to the session on an otherwise metered run and selects no
        endpoint, so the seats still resolve against the metered map."""
        code, _out, err = self.plain(extra=["--skip-claude"])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("--skip-claude is not the same thing", err)
        self.assertFalse(os.path.isdir(self.run_dir))

    def test_with_the_flag_the_same_template_runs_normally(self):
        code, out, err = self.run_panel(panel=self.draft_only)
        self.assertEqual(code, run_panel.EXIT_OK, err)
        self.assertIn("DRAFT PASS", out)
        self.assertIs(self.manifest()["draft"], True)
        self.assertEqual(self.workspace.calls(), [])

    def test_the_strict_loader_accepts_the_key(self):
        panel, _path = run_panel.load_panel(self.draft_only, paths_lib.Paths(self.workspace.root))
        self.assertIs(panel["draft_only"], True)

    def test_the_shipped_draft_review_template_carries_it(self):
        paths = paths_lib.Paths(workspace=os.path.join(tempfile.gettempdir(),
                                                       "ensemble-review-no-such-workspace"))
        panel, _path = run_panel.load_panel("draft-review", paths)
        self.assertIs(panel.get("draft_only"), True,
                      "the one template that would bill four Claude seats without --draft")

    def test_no_other_shipped_template_declares_it(self):
        """It is not a default: an ordinary panel run without `--draft` is the normal case."""
        directory = os.path.join(SKILL_DIR, "templates", "panels")
        declared = []
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
                if json.load(handle).get("draft_only"):
                    declared.append(name)
        self.assertEqual(declared, ["draft-review.json"])


class BannerOrderTest(DraftCase):
    """The "$0" banner is a claim about a run that is going to happen."""

    def test_a_refused_draft_run_prints_no_banner(self):
        _code, out, _err = self.run_panel(panel=self.workspace.panel)   # metered seats, refused
        self.assertNotIn("DRAFT PASS", out,
                         "a banner promising a free run, over a refusal saying there is no run")

    def test_a_run_that_proceeds_still_opens_with_it_before_the_projection(self):
        _code, out, _err = self.run_panel()
        self.assertIn("DRAFT PASS", out)
        self.assertLess(out.index("DRAFT PASS"), out.index("Cost pre-flight"))


# --- the two cheap modes are not one mode ------------------------------------------------------

class MutualExclusionTest(DraftCase):

    def test_draft_and_smoke_test_together_are_refused_before_anything_is_claimed(self):
        code, _out, err = self.run_panel(extra=["--smoke-test", harness.FAST_MODEL])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("--draft", err)
        self.assertIn("--smoke-test", err)
        self.assertFalse(os.path.isdir(self.run_dir), "the refusal left a run directory behind")

    def test_the_refusal_says_what_each_of_the_two_modes_is(self):
        """Not "mutually exclusive" and nothing else: the operator has to be able to pick one."""
        _code, _out, err = self.run_panel(extra=["--smoke-test", harness.FAST_MODEL])
        self.assertIn("not evidence", err)
        self.assertIn("uncorroborated", err)


# --- it costs nothing, and that is enforced ------------------------------------------------------

class MeteredSeatIsRefusedTest(DraftCase):

    def test_a_panel_the_metered_endpoint_would_serve_is_refused_naming_the_seat(self):
        code, _out, err = self.run_panel(panel=self.workspace.panel)   # kimi + xai, on `fake`
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("consistency-kimi", err)
        self.assertIn("adversarial-xai", err)
        self.assertIn(harness.CONNECTOR, err)
        self.assertIn("metered", err)

    def test_the_refusal_names_the_flag_that_does_what_they_probably_meant(self):
        _code, _out, err = self.run_panel(panel=self.workspace.panel)
        self.assertIn("--tier", err, "a cheap metered pass is a tier, not a mode")

    def test_nothing_is_claimed_and_nothing_is_dispatched(self):
        self.run_panel(panel=self.workspace.panel)
        self.assertFalse(os.path.isdir(self.run_dir))
        self.assertEqual(self.workspace.calls(), [])

    def test_a_free_endpoints_seats_get_the_ordinary_missing_cell_error_and_not_this_one(self):
        """The refusal keys on `billing: metered` rather than on "some other connector", so a free
        endpoint's seats are not accused of costing money.

        They still do not run, and the reason is worth stating plainly: `--draft` means **the
        harness leg**, not "any endpoint that bills nothing". It composes against the harness
        connector's own tier map, so a family only some other endpoint serves has no cell there
        whatever that endpoint's billing is, and the seating error says exactly that.
        """
        workspace = harness.Workspace(billing="free")
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.harness_leg()
        code, _out, err = _capture(
            ["--panel", workspace.panel, "--artifact", workspace.artifact,
             "--out", workspace.path("reviews", RUN_ID), "--workspace", workspace.root,
             "--config", workspace.config, "--models", workspace.registry,
             "--autonomous", "--reconcile", "off", "--draft", "--reconciler", "host"])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertNotIn("--draft cannot seat", err)
        self.assertIn("has no model there", err)

    def test_a_pin_onto_a_metered_model_is_refused_too(self):
        """The template can be clean and the command line still metered."""
        code, _out, err = self.run_panel(extra=[
            "--model", "{0}={1}".format(seating_id("consistency"), harness.SLOW_MODEL)])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn(harness.SLOW_MODEL, err)
        self.assertIn("metered", err)

    def test_a_harness_connector_declaring_itself_metered_is_refused(self):
        """A draft pass cannot be made out of an endpoint that bills, whatever the file is called."""
        self.workspace.harness_leg(billing="metered")
        code, _out, err = self.run_panel()
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("billing: metered", err)

    def test_a_connector_named_harness_that_is_not_the_harness_leg_is_refused(self):
        self.workspace.harness_leg(connector_type="openai_compat")
        code, _out, err = self.run_panel()
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("is not the harness leg", err)


class NoPaidJudgmentTest(DraftCase):

    def test_a_draft_run_that_would_fall_to_the_synthesis_persona_is_refused(self):
        """The judgment is the dearest single call most runs make; it cannot be the one thing a
        $0 pass pays for. With no harness judge installed, an unattended `default` resolves there."""
        os.environ["ENSEMBLE_REVIEW_HARNESS_AGENTS_DIR"] = self.workspace.path("no-harness-here")
        code, _out, err = self.run_panel()
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("synthesis", err)
        self.assertIn("--reconciler host", err)
        self.assertFalse(os.path.isdir(self.run_dir))

    def test_the_harness_judge_is_the_one_that_costs_nothing_and_it_is_accepted(self):
        """It is not refused, which is what this asserts. The first pass stops at the spawn block
        and exits 0 — the code the harness judge stage's own stop uses."""
        code, _out, _err = self.run_panel()
        self.assertEqual(code, run_panel.EXIT_OK)
        self.assertEqual(self.manifest()["reconciler"], "harness-judge")


# --- the run itself ------------------------------------------------------------------------------

class DraftRunTest(DraftCase):

    def setUp(self):
        super(DraftRunTest, self).setUp()
        self.code, self.out, self.err = self.run_panel()

    def test_it_dispatches_nothing_at_all_and_stops_for_the_session_to_spawn_the_seats(self):
        """**The first pass stops, it does not fail.** It has printed the spawn block and handed the
        leg to the orchestrating session, which is the same thing the harness judge stage does when
        it prints its own spawn instruction — and that stage exits 0. A resume that still finds no
        report is the failing case; `ResumeWithAMissingReportTest` asserts that half.
        """
        self.assertEqual(self.code, run_panel.EXIT_OK, self.err)
        self.assertEqual(self.workspace.calls(), [],
                         "the harness leg reached the dispatch path and the fake backend answered")

    def test_the_banner_comes_before_the_projection(self):
        self.assertIn("DRAFT PASS", self.out)
        self.assertLess(self.out.index("DRAFT PASS"), self.out.index("Cost pre-flight"))

    def test_the_manifest_says_the_run_was_a_draft(self):
        manifest = self.manifest()
        self.assertIs(manifest["draft"], True)
        self.assertIs(manifest["smoke_test"], False)

    def test_every_seat_records_the_harness_connector_its_model_and_no_effort(self):
        for seat in self.manifest()["seats"]:
            self.assertEqual(seat["leg"], "harness")
            self.assertEqual(seat["connector"], "harness")
            self.assertEqual(seat["model"], self.model)
            self.assertIsNone(seat["effort"], "the harness leg has no per-spawn effort parameter")
            self.assertIsNone(seat["effort_tokens"])
            self.assertEqual(seat["tier_source"], "panel", "the tier order is untouched by the mode")
            self.assertEqual(seat["effort_level"], "standard")
            self.assertEqual(seat["input_delivery"], "materialized-paths")

    def test_the_run_records_the_harness_connector_as_the_endpoint(self):
        connector = self.manifest()["connector"]
        self.assertEqual(connector["name"], "harness")
        self.assertEqual(connector["type"], "harness")
        self.assertEqual(connector["billing"], "subscription")
        self.assertIs(connector["requires_approval"], False)

    def test_the_spend_gate_did_not_fire_and_the_manifest_says_why(self):
        block = self.manifest()["spend_approval"]
        self.assertIs(block["required"], False)
        self.assertIsNone(block["granted"])
        self.assertEqual(block["reason"], "no metered connector seated")

    def test_the_family_target_drops_to_one_without_a_shortfall_warning(self):
        self.assertEqual(self.manifest()["min_families"]["target"], 1)
        self.assertNotIn("min_families: 1 seated against a target of 2", self.out)

    def test_an_explicit_min_families_still_overrides_the_mode(self):
        code, out, _err = self.run_panel(extra=["--min-families", "2"],
                                         out=self.workspace.path("reviews", "2026-09-19-2"))
        self.assertEqual(code, run_panel.EXIT_OK)
        self.assertIn("against a target of 2", out)

    def test_every_seat_is_projected_at_zero_and_the_total_is_zero(self):
        projection = self.manifest()["projection"]
        self.assertEqual(sorted(row["reviewer_id"] for row in projection["per_seat"]),
                         sorted(self.seat_ids()))
        for row in projection["per_seat"]:
            self.assertEqual(row["projected_usd"], 0.0)
        self.assertEqual(projection["projection_usd"], 0.0)
        self.assertEqual(projection["decision"], "within-budget")

    def test_the_registry_gate_accepts_a_zero_priced_subscription_model(self):
        """Zero is a price and null is not. A harness model file with no price at all is a model
        nobody has classified, and a mode that claims to cost nothing has to have looked."""
        registry = registry_lib.load_dir(self.workspace.registry)
        self.assertEqual(registry.covers([self.model]), [])
        self.workspace.edit_model(self.model, input_price_per_token=None)
        code, _out, err = self.run_panel(out=self.workspace.path("reviews", "2026-09-19-3"))
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("carries no input price", err)


# --- the spawn block -----------------------------------------------------------------------------

class SpawnBlockTest(DraftCase):

    def setUp(self):
        super(SpawnBlockTest, self).setUp()
        self.code, self.out, self.err = self.run_panel()

    def test_one_block_per_seat_naming_the_model_override_and_never_a_fork(self):
        self.assertIn("DRAFT PASS — spawn 2 harness subagent(s)", self.out)
        for lens in DRAFT_LENSES:
            self.assertIn("seat:         {0}".format(seating_id(lens)), self.out)
        self.assertIn("model:        opus", self.out)
        self.assertIn("Never `subagent_type: \"fork\"`", self.out)
        self.assertEqual(self.out.count("model:        opus"), len(DRAFT_LENSES))

    def test_every_seat_gets_its_own_staging_path_and_the_directory_exists(self):
        for lens in DRAFT_LENSES:
            reviewer_id = seating_id(lens)
            staging = runs_lib.staging_dir(self.run_dir, reviewer_id)
            self.assertTrue(os.path.isdir(staging), "the seat has nowhere to write")
            self.assertIn(drafts_lib.staged_report(staging, reviewer_id), self.out)
            self.assertNotIn(os.path.join(self.run_dir, reviewer_id + ".json"), self.out.split("mv ")[0],
                             "no seat is given a path into the directory holding its siblings")

    def test_it_names_the_persona_body_and_the_finding_schema_absolutely(self):
        for lens in DRAFT_LENSES:
            self.assertIn(os.path.join(SKILL_DIR, "agents", "lens-{0}.md".format(lens)), self.out)
        self.assertIn(os.path.join(SKILL_DIR, "references", "finding-schema.md"), self.out)

    def test_it_names_the_pinned_inputs_copy_and_not_the_working_tree(self):
        materialized = os.path.join(self.run_dir, "inputs")
        self.assertIn(materialized, self.out)
        self.assertNotIn("artifact:     " + self.workspace.artifact, self.out)

    def test_it_names_the_envelope_fields_no_script_fills_in(self):
        """A report with no `artifact` fails validation after the turn is spent."""
        self.assertIn("\"artifact\"", self.out)
        self.assertIn(self.manifest()["artifact_revision"], self.out)

    def test_it_names_the_digest_cap_and_the_blinding_rule(self):
        self.assertIn("digest under 2000 characters", self.out)
        self.assertIn("Read ONLY the paths above", self.out)

    def test_it_ends_with_the_render_move_and_resume_lines(self):
        self.assertIn("render_harness_report.py", self.out)
        self.assertIn("Then resume this run to reach the judge stage:", self.out)
        self.assertIn("--draft", self.out.split("Then resume this run")[1])
        self.assertIn(self.run_dir, self.out.split("Then resume this run")[1])


class ResumeReachesTheJudgeStageTest(DraftCase):

    def test_once_the_reports_are_in_the_run_asks_for_nobody_and_judges(self):
        self.run_panel()
        self.land_reports()
        code, out, err = self.run_panel()
        self.assertEqual(code, run_panel.EXIT_OK, err)
        self.assertNotIn("DRAFT PASS — spawn", out)
        self.assertIn("Seats reporting: 2 of 2", out)
        for seat in self.manifest()["seats"]:
            self.assertEqual(seat["status"], "ok",
                             "a seat whose report is on disk must not read `pending`")

    def test_the_first_pass_stops_with_the_same_code_the_judge_stage_stop_uses(self):
        """**Two halts that mean one thing report one code.** The spawn-block stop and the harness
        judge stage's stop both print an instruction the orchestrating session has to act on, and
        neither is a failure of the run that printed it. The judge stop's code is observed here
        rather than written down, so that if it ever moves this fails instead of quietly letting the
        two diverge for whatever is scripted around them.
        """
        first_pass, out, err = self.run_panel()
        self.assertIn("DRAFT PASS — spawn", out)

        # Every report in, and `--reconcile auto` this time, so the resumed run falls through the
        # spawn stage and reaches `_harness_judge_stage`: its child writes the worksheet, prints the
        # spawn instruction for the judge and exits "a judgment patch is required".
        self.land_reports()
        judge_stop, judge_out, judge_err = self.run_panel(extra=["--reconcile", "auto"])
        self.assertIn("judgment-request.json", judge_out, judge_err)
        self.assertNotIn("DRAFT PASS — spawn", judge_out)
        self.assertEqual(first_pass, judge_stop, err or judge_err)
        self.assertEqual(first_pass, run_panel.EXIT_OK)

    def test_a_resume_that_still_finds_no_report_exits_non_zero(self):
        """The other half: on a resume the session has been asked once and says it has answered, so
        an absent or invalid report is a real failure and not a mid-flight state."""
        self.run_panel()
        code, out, _err = self.run_panel()          # nothing landed in between
        self.assertIn("DRAFT PASS — spawn 2 harness subagent(s)", out)
        self.assertNotEqual(code, run_panel.EXIT_OK,
                            "a resumed draft run with an empty directory must not read as finished")
        self.assertEqual(code, run_panel.EXIT_UNDER_SEATED)

    def test_a_seat_whose_report_did_not_land_is_the_only_one_asked_for_again(self):
        self.run_panel()
        landed = seating_id(DRAFT_LENSES[0])
        self.land_reports(only={landed})
        _code, out, _err = self.run_panel()
        self.assertIn("DRAFT PASS — spawn 1 harness subagent(s)", out)
        block = out.split("DRAFT PASS — spawn")[1]
        self.assertIn(seating_id(DRAFT_LENSES[1]), block)
        self.assertNotIn("seat:         " + landed, block)


# --- the reference-free case ---------------------------------------------------------------------

class CitingLensWithNoReferencesTest(DraftCase):

    def setUp(self):
        super(CitingLensWithNoReferencesTest, self).setUp()
        _write_json(self.panel, {
            "name": "draft-panel",
            "requires_references": False,
            "min_families": 1,
            "tier": "standard",
            "seats": [{"lens": "fidelity", "family": "claude"},
                      {"lens": "consistency", "family": "claude"}],
        })

    def test_the_seat_is_retired_rather_than_the_run_refused(self):
        """Retired, not refused: the run proceeds to the spawn block and stops there, exit 0. A
        retirement is recorded, not a failure of this pass."""
        code, out, _err = self.run_panel()
        self.assertEqual(code, run_panel.EXIT_OK)
        self.assertIn("retiring 1 seat(s)", out)
        self.assertIn("fidelity-claude", out)

    def test_the_retired_seat_is_recorded_with_its_reason_and_never_spawned(self):
        _code, out, _err = self.run_panel()
        seats = {seat["reviewer_id"]: seat for seat in self.manifest()["seats"]}
        self.assertEqual(seats["fidelity-claude"]["status"], "failed")
        self.assertEqual(seats["fidelity-claude"]["failure_reason"], "no-references")
        self.assertIn("--ref", seats["fidelity-claude"]["error"])
        self.assertNotIn("seat:         fidelity-claude", out)

    def test_the_reconciliation_names_it_as_a_seat_that_did_not_report(self):
        self.run_panel()
        caveat = core.method_caveat("", self.manifest())
        self.assertIn("fidelity-claude", caveat)
        self.assertIn("no-references", caveat)

    def test_with_references_the_seat_is_seated_like_any_other(self):
        _code, out, _err = self.run_panel(extra=["--ref", self.workspace.reference])
        self.assertNotIn("retiring", out)
        self.assertIn("seat:         fidelity-claude", out)

    def test_a_metered_run_still_refuses_rather_than_retiring(self):
        """The rule is unchanged where the money is: a seat whose output would be thrown out is
        worth refusing a paid run over."""
        _write_json(self.workspace.panel, {
            "name": "test-panel", "requires_references": False, "min_families": 1,
            "tier": "standard",
            "seats": [{"lens": "fidelity", "family": "kimi"}]})
        argv = ["--panel", self.workspace.panel, "--artifact", self.workspace.artifact,
                "--out", self.workspace.path("reviews", "metered"), "--workspace", self.workspace.root,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--autonomous", "--reconcile", "off"]
        code, _out, err = _capture(argv)
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("require source-of-truth references", err)


# --- the caveat, and why it is not the smoke test's ----------------------------------------------

class CaveatTest(DraftCase):

    def test_a_draft_run_opens_with_the_draft_prologue(self):
        self.run_panel()
        caveat = core.method_caveat("The panel ran two lenses.", self.manifest())
        self.assertTrue(caveat.startswith(drafts_lib.PROLOGUE))
        self.assertIn("The panel ran two lenses.", caveat)

    def test_the_draft_prologue_says_uncorroborated_and_the_smoke_one_says_not_evidence(self):
        """The distinction is the whole point of writing two: a reader who took them for one would
        either discard a draft's real findings or cite its agreement counts."""
        draft = core.method_caveat("", {"draft": True})
        smoke = core.method_caveat("", {"smoke_test": True})
        self.assertNotEqual(draft, smoke)
        self.assertIn("no corroboration claim", draft)
        self.assertNotIn("not evidence about the artifact", draft)
        self.assertIn("not evidence about the artifact", smoke)
        self.assertIn("This is not a smoke test", draft)

    def test_the_draft_prologue_is_prepended_not_appended(self):
        self.run_panel()
        caveat = core.method_caveat("Supplied text.", self.manifest())
        self.assertLess(caveat.index("no corroboration claim"), caveat.index("Supplied text."))

    def test_an_ordinary_run_gets_neither(self):
        self.assertEqual(core.method_caveat("Supplied.", {"draft": False, "smoke_test": False}),
                         "Supplied.")


# --- the shipped package -------------------------------------------------------------------------

class ShippedDraftPanelTest(unittest.TestCase):
    """The shipped `draft-review` template against the shipped harness connector and model files."""

    @classmethod
    def setUpClass(cls):
        nowhere = tempfile.gettempdir()
        cls.paths = paths_lib.Paths(
            workspace=os.path.join(nowhere, "ensemble-review-no-such-workspace"),
            user=os.path.join(nowhere, "ensemble-review-no-such-user"))
        cls.config, _path = cls.paths.config()
        cls.connector, _cpath = connectors_lib.load(cls.paths, drafts_lib.HARNESS_CONNECTOR)
        cls.registry = registry_lib.load(cls.paths)
        cls.entry = connectors_lib.compose(cls.config, cls.connector, cls.registry)

    def test_the_shipped_harness_connector_is_a_subscription_with_no_key_and_no_url(self):
        self.assertEqual(self.connector["type"], drafts_lib.HARNESS_DRIVER)
        self.assertEqual(self.connector["billing"], connectors_lib.SUBSCRIPTION)
        self.assertIs(connectors_lib.requires_approval(self.connector), False)
        for field in ("base_url", "api_key_secret", "api_key_env", "catalogue_url"):
            self.assertIsNone(self.connector.get(field), "{0} is set on the harness leg".format(field))

    def test_its_map_holds_claude_opus_5_and_nothing_the_metered_endpoint_serves(self):
        seated = {model for cells in self.entry["tiers"].values() for model in cells.values()}
        self.assertEqual(seated, {"claude-opus-5"})
        for cells in self.entry["tiers"].values():
            self.assertEqual(list(cells), ["claude"])

    def test_the_draft_template_resolves_every_seat_onto_opus_at_zero_projected_cost(self):
        from lib import budget as budget_lib
        from lib import seating as seating_lib
        panel, _path = run_panel.load_panel("draft-review", self.paths)
        seats = seating_lib.resolve(panel, self.entry, registry=self.registry,
                                    frontmatter_fn=run_panel.persona_frontmatter(self.paths))
        self.assertEqual(len(seats), len(panel["seats"]))
        for seat in seats:
            self.assertEqual(seat["family"], "claude")
            self.assertEqual(seat["model"], "claude-opus-5")
            self.assertIsNone(seat["effort"], "no rung is bound on a leg with no effort parameter")
        projection = budget_lib.project(
            [{"reviewer_id": s["reviewer_id"], "model": s["model"], "prompt_tokens": 10000}
             for s in seats], self.registry, 5.00, with_synthesis=False)
        self.assertEqual(projection["projection_usd"], 0.0)

    def test_the_shipped_model_is_priced_at_zero_rather_than_left_unpriced(self):
        self.assertEqual(self.registry.covers(["claude-opus-5"]), [])
        entry = self.registry.get("claude-opus-5")
        self.assertEqual(registry_lib.prices(entry), (0.0, 0.0))
        self.assertIsNone(entry["effort"])
        self.assertIsNone(entry["effort_vocabulary"])

    def test_the_packaged_metered_map_is_untouched_by_the_harness_models(self):
        """The derived map filters by connector, so adding a harness model must not move a cell."""
        metered, _p = connectors_lib.load(self.paths, self.config["default_connector"])
        entry = connectors_lib.compose(self.config, metered, self.registry)
        for cells in entry["tiers"].values():
            self.assertNotIn("claude-opus-5", cells.values())
        self.assertEqual(entry["tiers"]["frontier"]["claude"], "anthropic/claude-sonnet-5")


class HarnessDriverTest(unittest.TestCase):
    """The driver exists so a `type: harness` connector resolves, and refuses so it never calls."""

    def test_both_entry_points_raise_and_neither_touches_the_network(self):
        for call in (lambda: harness_driver.dispatch("s", "u", "m", {}),
                     lambda: harness_driver.dispatch_detailed("s", "u", "m", {})):
            with self.assertRaises(harness_driver.HarnessLegError) as raised:
                call()
            self.assertIn("no dispatch path", str(raised.exception))
            self.assertIn("No network call is made", str(raised.exception))

    def test_the_package_resolves_the_type_to_it(self):
        paths = paths_lib.Paths(workspace=SKILL_DIR)
        self.assertEqual(paths.driver_ref(drafts_lib.HARNESS_DRIVER), drafts_lib.HARNESS_DRIVER)


class RefreshLeavesOtherConnectorsAloneTest(unittest.TestCase):
    """A refresh runs against one endpoint's catalogue; a model on another is not "unknown" to it."""

    def test_a_harness_model_is_skipped_rather_than_reported_missing(self):
        import refresh_models
        models = {
            "openrouter/one": {"connector": "openrouter", "input_price_per_token": 1e-06},
            "claude-opus-5": {"connector": "harness", "input_price_per_token": 0},
        }
        foreign = refresh_models.foreign_models(models, "openrouter")
        self.assertEqual(foreign, ["claude-opus-5"])
        catalogue = {"openrouter/one": {"pricing": {"prompt": "0.000002", "completion": "0.000004"}}}
        changes, unknown, _added = refresh_models.refresh(
            models, catalogue, connector="openrouter", skip=foreign)
        self.assertEqual(unknown, [], "a model on another endpoint is not missing from this one")
        self.assertEqual([c["model"] for c in changes], ["openrouter/one"] * len(changes))
        self.assertEqual(models["claude-opus-5"]["input_price_per_token"], 0)

    def test_a_model_naming_no_connector_is_still_refreshed(self):
        import refresh_models
        self.assertEqual(refresh_models.foreign_models({"a/b": {}}, "openrouter"), [])


def seating_id(lens):
    return "{0}-claude".format(lens)


def _write_json(path, data):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2) + "\n")


def _capture(argv):
    """`run_panel.main` in process, with both streams captured. Returns (code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = run_panel.main(list(argv))
    except SystemExit as exit_code:
        code = exit_code.code
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return code, out.getvalue(), err.getvalue()


if __name__ == "__main__":
    unittest.main(verbosity=2)

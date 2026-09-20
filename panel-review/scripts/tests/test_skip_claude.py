#!/usr/bin/env python3
"""`--skip-claude`: the older, narrower door onto the harness leg, and the spend gate beside it.

`--draft` puts a whole panel on the harness. `--skip-claude` is the mixed run it grew out of: the
explicitly seated `claude` seats are left to the orchestrating session and everything else goes
through the metered endpoint. The two share one seat state and almost all of one code path, and
until now only the draft half of it was tested — so the behaviour a `--skip-claude` run depends on
was carried by the draft tests or by nothing.

Three things this file pins:

- **A skipped seat records no model and no connector.** It resolved against the *metered* tier map
  and the subagent will not run that model, so recording one would be a false audit line — which is
  exactly the opposite of a draft seat, whose resolved model is the one the spawn block names.
- **A landed report counts.** Nothing else folds a harness report into the manifest: the operator's
  `mv` writes no record, so a seat that had answered read `pending` for the life of the run and
  `families_reporting` never saw it.
- **The spend gate asks only when this run will call the endpoint.** `--skip-claude` on a panel
  whose every seat is skipped dispatches nothing, and being asked to approve $0.00 of spending
  teaches an operator to answer without reading.

No network and no paid call: the metered side is `fake_backend.py` and the harness side dispatches
nothing at all.

    python3 scripts/tests/test_skip_claude.py
"""

import io
import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import harness  # noqa: E402
import render_harness_report  # noqa: E402
import run_panel  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
from lib import runs as runs_lib  # noqa: E402

RUN_ID = "2026-09-19-1"

CLAUDE_MODEL = "test/claude-on-the-endpoint"


class SkipClaudeCase(unittest.TestCase):
    """A **metered** endpoint serving a `kimi` seat and a `claude` seat, and `--skip-claude`."""

    def setUp(self):
        self.workspace = harness.Workspace(billing="metered")
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        # The metered map has to hold a `claude` cell: that is what `--skip-claude` skips, and the
        # cell is why a skipped seat's `model` is a resolved id the subagent will never run.
        self.workspace.seat_model(CLAUDE_MODEL, "claude", ["standard"])
        self.panel = self.workspace.path("mixed-panel.json")
        _write_json(self.panel, {
            "name": "mixed-panel",
            "requires_references": False,
            "min_families": 2,
            "tier": "standard",
            "seats": [{"lens": "consistency", "family": "kimi"},
                      {"lens": "adversarial", "family": "claude"}],
        })
        self.run_dir = self.workspace.path("reviews", RUN_ID)

    def run_panel(self, extra=None, panel=None, out=None):
        argv = ["--panel", panel or self.panel, "--artifact", self.workspace.artifact,
                "--out", out or self.run_dir, "--workspace", self.workspace.root,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--autonomous", "--reconcile", "off", "--reconciler", "host",
                "--skip-claude"] + list(extra or [])
        return _capture(argv)

    def manifest(self, run_dir=None):
        return runs_lib.read_manifest(run_dir or self.run_dir)

    def seats(self, run_dir=None):
        return {seat["reviewer_id"]: seat for seat in self.manifest(run_dir)["seats"]}

    def land(self, reviewer_id, run_dir=None):
        """Write, render and move in one harness report — the three steps the operator does."""
        run_dir = run_dir or self.run_dir
        manifest = self.manifest(run_dir)
        staging = runs_lib.make_staging_dir(run_dir, reviewer_id)
        staged = os.path.join(staging, reviewer_id + ".json")
        _write_json(staged, dict(harness.valid_report(),
                                 artifact=manifest["artifact"],
                                 artifact_revision=manifest["artifact_revision"]))
        self.assertEqual(render_harness_report.main([staged, "--model", "opus"]), 0)
        for suffix in (".json", ".md"):
            os.replace(staged[:-5] + suffix, os.path.join(run_dir, reviewer_id + suffix))


class MixedRunTest(SkipClaudeCase):

    def setUp(self):
        super(MixedRunTest, self).setUp()
        self.code, self.out, self.err = self.run_panel(extra=["--approve-spend"])

    def test_the_claude_seat_is_on_the_harness_leg_with_no_model_and_no_connector(self):
        """It resolved against the metered map and the subagent will not run what resolved, so an
        id here would be a false audit line. A `--draft` seat records both, for the other reason."""
        seat = self.seats()["adversarial-claude"]
        self.assertEqual(seat["leg"], "harness")
        self.assertEqual(seat["status"], "pending")
        self.assertIsNone(seat["model"])
        self.assertIsNone(seat["connector"])
        self.assertIsNone(seat["effort"])
        self.assertEqual(seat["input_delivery"], "materialized-paths")

    def test_the_other_seat_went_through_the_endpoint_and_records_it(self):
        seat = self.seats()["consistency-kimi"]
        self.assertEqual(seat["leg"], "openrouter")
        self.assertEqual(seat["status"], "ok")
        self.assertEqual(seat["model"], harness.SLOW_MODEL)
        self.assertIsNotNone(seat["connector"], "a dispatched seat records the endpoint it used")

    def test_the_console_counts_the_harness_seat_in_the_denominator(self):
        """`1 of 2`, not `1 of 1`: a seat the host has yet to spawn is still a seat this panel has."""
        self.assertIn("Seats reporting: 1 of 2 · harness pending: 1", self.out)
        self.assertEqual(self.code, run_panel.EXIT_OK,
                         "a pending harness seat is not what under-seated means on a mixed run")

    def test_only_the_dispatched_seat_was_called(self):
        self.assertEqual([call["model"] for call in self.workspace.calls()], [harness.SLOW_MODEL])

    def test_a_landed_report_folds_into_the_manifest_and_counts(self):
        self.land("adversarial-claude")
        code, out, err = self.run_panel(extra=["--approve-spend"])
        self.assertEqual(code, run_panel.EXIT_OK, err)
        self.assertIn("Seats reporting: 2 of 2 · harness pending: 0", out)
        seat = self.seats()["adversarial-claude"]
        self.assertEqual(seat["status"], "ok",
                         "a seat whose report is on disk must not read `pending`")
        self.assertEqual(seat["verdict"], "fix-then-ship")
        self.assertIn("claude", self.manifest()["min_families"]["families_reporting"])
        self.assertEqual(self.manifest()["min_families"]["reporting"], 2)

    def test_the_landed_report_is_never_re_dispatched(self):
        self.land("adversarial-claude")
        self.workspace.reset_calls()
        self.run_panel(extra=["--approve-spend"])
        self.assertEqual(self.workspace.calls(), [], "a seat with a valid report was re-purchased")


class ReconcilerIsResolvedBeforeTheClaimTest(SkipClaudeCase):
    """A refusal that can leave nothing behind leaves nothing behind."""

    def test_an_unknown_reconciler_on_the_template_refuses_with_no_run_directory(self):
        _write_json(self.panel, {
            "name": "mixed-panel",
            "requires_references": False,
            "min_families": 2,
            "tier": "standard",
            "reconciler": "nobody-in-particular",
            "seats": [{"lens": "consistency", "family": "kimi"},
                      {"lens": "adversarial", "family": "claude"}],
        })
        argv = ["--panel", self.panel, "--artifact", self.workspace.artifact,
                "--out", self.run_dir, "--workspace", self.workspace.root,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--autonomous", "--reconcile", "off", "--skip-claude"]
        code, _out, err = _capture(argv)
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("unknown reconciler", err)
        self.assertFalse(os.path.isdir(self.run_dir),
                         "the refusal left a run directory, which moves the next run's sequence number")
        self.assertEqual(self.workspace.calls(), [])


class SpendGateTest(SkipClaudeCase):
    """The gate is the endpoint's, and it is asked only when this run will call the endpoint."""

    def test_a_run_with_a_dispatched_seat_on_a_metered_endpoint_is_gated(self):
        code, _out, err = self.run_panel()
        self.assertEqual(code, run_panel.EXIT_BUDGET, "an ungated metered dispatch was allowed")
        self.assertIn("spend not approved", err)
        self.assertIn("consistency-kimi", err)
        self.assertEqual(self.workspace.calls(), [], "refused before any call")
        block = self.manifest()["spend_approval"]
        self.assertIs(block["required"], True)
        self.assertIs(block["granted"], False)

    def test_a_run_whose_every_seat_is_skipped_is_not_asked_at_all(self):
        """Nothing is dispatched, so there is nothing to approve. Being asked to approve $0.00 of
        spending on a metered endpoint teaches an operator to answer without reading."""
        all_claude = self.workspace.path("all-claude.json")
        _write_json(all_claude, {
            "name": "all-claude",
            "requires_references": False,
            "min_families": 1,
            "tier": "standard",
            "seats": [{"lens": "consistency", "family": "claude"},
                      {"lens": "adversarial", "family": "claude"}],
        })
        code, out, err = self.run_panel(panel=all_claude)
        self.assertNotIn("spend not approved", err)
        self.assertNotIn("spend not approved", out)
        self.assertEqual(code, run_panel.EXIT_OK, err)
        self.assertEqual(self.workspace.calls(), [])
        block = self.manifest()["spend_approval"]
        self.assertIs(block["required"], False)
        self.assertIsNone(block["granted"])
        self.assertEqual(block["seats"], [])
        self.assertIn("no seat dispatched", block["reason"])

    def test_the_manifest_still_records_the_endpoint_and_its_posture(self):
        """`required: false` here means "nothing to gate", never "this endpoint is free"."""
        all_claude = self.workspace.path("all-claude.json")
        _write_json(all_claude, {
            "name": "all-claude", "requires_references": False, "min_families": 1,
            "tier": "standard",
            "seats": [{"lens": "adversarial", "family": "claude"}]})
        self.run_panel(panel=all_claude)
        block = self.manifest()["spend_approval"]
        self.assertEqual(block["billing"], "metered")
        self.assertNotIn("gate_disabled_by", block,
                         "a run with no seats to gate is not a machine that trusts the endpoint")
        self.assertIs(self.manifest()["connector"]["requires_approval"], True)

    def test_a_pending_harness_seat_is_not_reported_as_a_missing_seat(self):
        """The gate is quiet and so is the absence record: the host has simply not spawned it yet.

        What the caveat does say is the family shortfall, which is the true statement about a run
        whose second family is still pending.
        """
        self.run_panel(extra=["--approve-spend"])
        caveat = core.method_caveat("", self.manifest())
        self.assertNotIn("Seats that did not report", caveat)
        self.assertIn("lens-diverse only", caveat)


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

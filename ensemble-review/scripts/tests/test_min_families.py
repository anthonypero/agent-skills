#!/usr/bin/env python3
"""`min_families` as a target: counted twice, recorded, named in the caveat, and never a gate.

No network and no paid call. The spec is explicit that this is a target and not a precondition — "if
it cannot be met the run proceeds lens-diverse-only and the reconciliation says so in its method
caveat" — so the load-bearing assertion in this file is the one that says a one-family panel **exits
0**. A test that only checked the wording would pass on an implementation that refused the run.

Two counts, because they answer different questions. `seated` is over the expected seats and says
what the panel was composed to reach; `reporting` is over the seats whose reports validated and says
what it actually reached. A panel that seated four families and heard back from one is a different
document from one that only ever had one, and `method_caveat` prints both.

    python3 scripts/tests/test_min_families.py
"""

import io
import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import harness  # noqa: E402
import run_panel  # noqa: E402
from lib import reconcile_core as core  # noqa: E402

ONE_FAMILY_PANEL = {
    "name": "one-family",
    "description": "Two lenses, one family. Lens diversity with no model diversity at all.",
    "requires_references": False,
    "min_families": 2,
    "tier": "standard",
    "reconciler": "default",
    "auto_apply": False,
    "seats": [{"lens": "consistency", "family": "kimi"}, {"lens": "adversarial", "family": "kimi"}],
}


def manifest_with(target, seated, reporting, families=("kimi",)):
    return {"seats": [], "min_families": {
        "target": target, "seated": seated, "reporting": reporting,
        "families_seated": list(families), "families_reporting": list(families)[:reporting]}}


# --- the caveat, on its own --------------------------------------------------------------------------

class CaveatTest(unittest.TestCase):

    def test_one_reporting_family_reads_as_lens_diverse_only(self):
        caveat = core.method_caveat("", manifest_with(2, 1, 1))
        self.assertIn("lens-diverse only", caveat)
        self.assertIn("no cluster on it can carry cross-family corroboration", caveat)
        self.assertIn("target 2", caveat)

    def test_a_panel_that_meets_its_target_says_nothing(self):
        self.assertEqual(core.method_caveat("Four families ran.", manifest_with(2, 2, 2, ("kimi", "xai"))),
                         "Four families ran.")

    def test_a_panel_that_seated_enough_and_heard_back_from_one_reports_both_counts(self):
        caveat = core.method_caveat("", manifest_with(2, 4, 1, ("claude", "glm", "kimi", "xai")))
        self.assertIn("4 seated", caveat)
        self.assertIn("1 reporting", caveat)
        self.assertIn("lens-diverse only", caveat)

    def test_two_of_three_is_short_but_not_lens_diverse_only(self):
        caveat = core.method_caveat("", manifest_with(3, 2, 2, ("kimi", "xai")))
        self.assertIn("thinner than the panel asked for", caveat)
        self.assertNotIn("lens-diverse only", caveat)

    def test_the_supplied_caveat_is_kept_whole(self):
        caveat = core.method_caveat("The kimi seat spent its whole cap on reasoning.", manifest_with(2, 1, 1))
        self.assertTrue(caveat.startswith("The kimi seat spent its whole cap on reasoning."))
        self.assertIn("Family diversity below the panel's target", caveat)

    def test_a_manifest_with_no_block_is_left_alone(self):
        """Every run written before stage 2c. Silence beats a caveat invented from nothing."""
        self.assertEqual(core.method_caveat("As it was.", {"seats": []}), "As it was.")


# --- the whole script ---------------------------------------------------------------------------------

class MinFamiliesRunTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.one_family = self.workspace.path("one-family.json")
        with io.open(self.one_family, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(ONE_FAMILY_PANEL, indent=2) + "\n")
        self.workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()},
                                                  {"body": harness.valid_report()}]})

    def run_panel(self, panel, extra=None, out=None):
        argv = [
            "--panel", panel,
            "--artifact", self.workspace.artifact,
            "--out", out or self.workspace.path("reviews", "2026-09-18-1"),
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
            "--tier", "standard",
            "--autonomous",
        ] + list(extra or [])
        out_stream, err_stream = io.StringIO(), io.StringIO()
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_stream, err_stream
        try:
            code = run_panel.main(argv)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        return code, out_stream.getvalue(), err_stream.getvalue()

    def manifest(self, out=None):
        path = os.path.join(out or self.workspace.path("reviews", "2026-09-18-1"), "manifest.json")
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_a_one_family_panel_runs_and_is_not_an_error(self):
        code, out, err = self.run_panel(self.one_family)
        self.assertEqual(code, 0, err + out)
        for reviewer_id in ("consistency-kimi", "adversarial-kimi"):
            self.assertTrue(os.path.isfile(os.path.join(
                self.workspace.path("reviews", "2026-09-18-1"), reviewer_id + ".json")), out)

    def test_it_records_the_target_and_both_counts(self):
        self.run_panel(self.one_family)
        block = self.manifest()["min_families"]
        self.assertEqual((block["target"], block["seated"], block["reporting"]), (2, 1, 1))
        self.assertEqual(block["families_seated"], ["kimi"])
        self.assertEqual(block["families_reporting"], ["kimi"])

    def test_it_says_so_on_the_console_at_both_ends(self):
        _code, out, _err = self.run_panel(self.one_family)
        self.assertIn("min_families: 1 seated against a target of 2", out)
        self.assertIn("lens-diverse only", out)

    def test_the_caveat_on_the_real_manifest_says_lens_diverse_only(self):
        self.run_panel(self.one_family)
        self.assertIn("lens-diverse only", core.method_caveat("", self.manifest()))

    def test_min_families_one_clears_the_target_and_silences_the_caveat(self):
        code, out, err = self.run_panel(self.one_family, extra=["--min-families", "1"])
        self.assertEqual(code, 0, err)
        block = self.manifest()["min_families"]
        self.assertEqual((block["target"], block["seated"], block["reporting"]), (1, 1, 1))
        self.assertNotIn("lens-diverse only", out)
        self.assertEqual(core.method_caveat("", self.manifest()), "")

    def test_the_cli_overrides_a_panel_that_asked_for_less(self):
        """The two-family shipped test panel, held to three: short, recorded, and still exit 0."""
        code, out, err = self.run_panel(self.workspace.panel, extra=["--min-families", "3"])
        self.assertEqual(code, 0, err)
        block = self.manifest()["min_families"]
        self.assertEqual((block["target"], block["seated"], block["reporting"]), (3, 2, 2))
        self.assertIn("thinner than the panel asked for", core.method_caveat("", self.manifest()))
        del out

    def test_a_panel_that_meets_its_target_records_it_and_says_nothing(self):
        code, out, err = self.run_panel(self.workspace.panel)
        self.assertEqual(code, 0, err)
        block = self.manifest()["min_families"]
        self.assertEqual((block["target"], block["seated"], block["reporting"]), (2, 2, 2))
        self.assertNotIn("min_families:", out)
        self.assertEqual(core.method_caveat("", self.manifest()), "")

    def test_a_panel_with_no_min_families_key_takes_the_default(self):
        panel = dict(ONE_FAMILY_PANEL)
        del panel["min_families"]
        path = self.workspace.path("no-target.json")
        with io.open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(panel, indent=2) + "\n")
        self.run_panel(path)
        self.assertEqual(self.manifest()["min_families"]["target"], run_panel.DEFAULT_MIN_FAMILIES)

    def test_a_seat_that_fails_drops_out_of_the_reporting_count_only(self):
        """`seated` is what was composed; `reporting` is what came back. They must not be one number."""
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"status": 500, "body": "upstream is unwell"}],
        })
        code, _out, _err = self.run_panel(self.workspace.panel)
        self.assertEqual(code, run_panel.EXIT_UNDER_SEATED)
        block = self.manifest()["min_families"]
        self.assertEqual((block["target"], block["seated"], block["reporting"]), (2, 2, 1))
        self.assertIn("lens-diverse only", core.method_caveat("", self.manifest()))


if __name__ == "__main__":
    unittest.main(verbosity=2)

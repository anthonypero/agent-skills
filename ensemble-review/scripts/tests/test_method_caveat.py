#!/usr/bin/env python3
"""What `reconcile_core.method_caveat` appends that the judgment supplier could not have known.

The judgment patch is written by something that has read the reports. It has not read the manifest,
so it cannot say which composed seats are absent, how the family counts came out, or what the run
cost against what it was projected to cost. Those three appendices are asserted here; the seat- and
panel-substitution appendices have homes in `test_seating.py` and `test_panel_selection.py`.

    python3 scripts/tests/test_method_caveat.py
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

from lib import judge as judge_lib  # noqa: E402
from lib import reconcile_core as core  # noqa: E402


def manifest(seats=None, min_families=None, projection=None, cost=None, judge=None):
    document = {"seats": list(seats or [])}
    if min_families is not None:
        document["min_families"] = min_families
    if projection is not None:
        document["projection"] = projection
    if cost is not None:
        document["cost_usd_total"] = cost
    if judge is not None:
        document["judge"] = judge
    return document


def seat(reviewer_id, status="ok", **extra):
    record = {"reviewer_id": reviewer_id, "status": status, "lens": reviewer_id.split("-")[0],
              "family": reviewer_id.split("-")[-1]}
    record.update(extra)
    return record


# Run 3's own numbers, so the appendices are asserted against the run that exposed the need for them.
RUN_THREE_SEATS = [
    seat("fidelity-openai", model="openai/gpt-5.6-sol"),
    seat("buildability-glm", status="failed", model="z-ai/glm-5.3-flash",
         error="buildability-glm: dispatch failed: IncompleteRead(528 bytes read)"),
    seat("consistency-kimi", model="moonshotai/kimi-k3"),
    seat("adversarial-xai", model="x-ai/grok-4.6"),
]
RUN_THREE_MIN_FAMILIES = {"target": 2, "seated": 4, "reporting": 3,
                          "families_seated": ["glm", "kimi", "openai", "xai"],
                          "families_reporting": ["kimi", "openai", "xai"]}
RUN_THREE_PROJECTION = {"projection_usd": 2.2987, "budget_usd": 5.0, "estimate": True}


class MissingSeatTest(unittest.TestCase):

    def test_a_failed_seat_is_named_with_its_lens_its_model_and_the_reason(self):
        caveat = core.method_caveat("", manifest(RUN_THREE_SEATS))
        self.assertIn("`buildability-glm`", caveat)
        self.assertIn("buildability", caveat)
        self.assertIn("z-ai/glm-5.3-flash", caveat)
        self.assertIn("IncompleteRead", caveat)
        self.assertIn("1 of 4 composed seat(s) are absent", caveat)

    def test_a_recorded_failure_reason_is_preferred_to_the_raw_stderr(self):
        caveat = core.method_caveat("", manifest([
            seat("fidelity-openai"),
            seat("buildability-glm", status="failed", failure_reason="context-overflow",
                 error="a long stderr tail nobody wants in the reconciliation"),
        ]))
        self.assertIn("context-overflow", caveat)
        self.assertNotIn("stderr tail", caveat)

    def test_a_clean_run_says_nothing_about_missing_seats(self):
        self.assertEqual(core.method_caveat("As supplied.", manifest([seat("fidelity-openai")])),
                         "As supplied.")

    def test_a_seat_record_with_no_status_is_not_asserted_to_be_missing(self):
        """A manifest this function does not understand earns silence, not a fabricated fact."""
        self.assertEqual(core.method_caveat("As supplied.", manifest([{"reviewer_id": "a"}])),
                         "As supplied.")

    def test_a_harness_seat_still_pending_is_not_a_missing_seat(self):
        self.assertEqual(core.method_caveat("As supplied.", manifest([
            seat("fidelity-openai"), seat("security-claude", status="pending", leg="harness")])),
            "As supplied.")


class FamilyCountsTest(unittest.TestCase):

    def test_a_met_target_over_a_short_panel_still_reports_both_counts(self):
        """Run 3 met its target of 2 with 3 of 4 families, and the counts said nothing at all."""
        caveat = core.method_caveat("", manifest(RUN_THREE_SEATS, RUN_THREE_MIN_FAMILIES))
        self.assertIn("`min_families` target 2, 4 seated, 3 reporting", caveat)
        self.assertIn("kimi, openai, xai", caveat)
        self.assertIn("the target is met, but over a short panel", caveat)

    def test_a_met_target_with_every_seat_reporting_stays_silent(self):
        caveat = core.method_caveat("", manifest(
            [seat("fidelity-openai"), seat("adversarial-xai")],
            {"target": 2, "seated": 2, "reporting": 2, "families_reporting": ["openai", "xai"]}))
        self.assertEqual(caveat, "")

    def test_a_missed_target_still_reads_below_and_not_against(self):
        caveat = core.method_caveat("", manifest(
            [seat("fidelity-openai"), seat("adversarial-xai", status="failed")],
            {"target": 2, "seated": 2, "reporting": 1, "families_reporting": ["openai"]}))
        self.assertIn("Family diversity below the panel's target", caveat)
        self.assertIn("lens-diverse only", caveat)


class CostTest(unittest.TestCase):

    def test_an_overrun_names_both_figures_and_the_budget(self):
        caveat = core.method_caveat("", manifest(
            [seat("fidelity-openai")], projection=RUN_THREE_PROJECTION, cost=2.752117))
        self.assertIn("projected $2.30 and the run was billed $2.75", caveat)
        self.assertIn("over the pre-flight by 20%", caveat)
        self.assertIn("against a budget of $5.00", caveat)
        self.assertIn("nothing meters spend as seats return", caveat)

    def test_a_run_with_nothing_unbilled_says_nothing_about_unbilled_spend(self):
        caveat = core.method_caveat("", manifest(
            [seat("fidelity-openai")], projection=RUN_THREE_PROJECTION, cost=2.752117))
        self.assertNotIn("not billed", caveat)

    def test_unbilled_upstream_spend_gets_one_clause_and_never_the_headline_figure(self):
        """The run's own numbers: billed $2.752117, with $1.816461 of upstream inference unbilled."""
        seats = [seat("fidelity-openai"),
                 seat("consistency-kimi", upstream_unbilled_usd=1.816461)]
        caveat = core.method_caveat("", manifest(
            seats, projection=RUN_THREE_PROJECTION, cost=2.752117))
        self.assertIn("the run was billed $2.75", caveat)
        self.assertIn("a further $1.82 of upstream inference was run and not billed", caveat)
        self.assertNotIn("$4.57", caveat, "the billed total is the one that reconciles")

    def test_the_judges_unbilled_spend_is_counted_too(self):
        """The judge record is built by its real producer, not hand-written into the shape wanted.

        A hand-built dict here asserted that `_upstream_unbilled_total` sums a key — it did not
        assert that anything ever writes one. `judge_lib.judge_record` is what writes it, so it is
        what this test calls; `test_synthesis.py` drives the same path through a scripted call.
        """
        record = judge_lib.judge_record(
            reviewer_id="synthesis", family="openai", tier="standard",
            model="openai/gpt-5.6-sol", connector="openai_compat", provider="openrouter",
            effort="high", cap=32000, elapsed_s=150.7, status="ok",
            attempts=[{"n": 1, "cost_usd": 0.4127385, "cost_source": "provider",
                       "upstream_unbilled_usd": 0.5, "usage": {}}])
        self.assertAlmostEqual(record["upstream_unbilled_usd"], 0.5, places=6)
        caveat = core.method_caveat("", manifest(
            [seat("fidelity-openai")], projection=RUN_THREE_PROJECTION, cost=2.752117,
            judge=record))
        self.assertIn("a further $0.50 of upstream inference", caveat)

    def test_a_judge_record_with_nothing_unbilled_adds_no_clause(self):
        record = judge_lib.judge_record(
            reviewer_id="synthesis", family="openai", tier="standard", model="openai/gpt-5.6-sol",
            connector="openai_compat", provider="openrouter", effort="high", cap=32000,
            elapsed_s=150.7, status="ok",
            attempts=[{"n": 1, "cost_usd": 0.4127385, "cost_source": "provider", "usage": {}}])
        self.assertIsNone(record["upstream_unbilled_usd"])
        caveat = core.method_caveat("", manifest(
            [seat("fidelity-openai")], projection=RUN_THREE_PROJECTION, cost=2.752117, judge=record))
        self.assertNotIn("not billed", caveat)

    def test_a_run_inside_its_projection_says_nothing(self):
        self.assertEqual(core.method_caveat("As supplied.", manifest(
            [seat("fidelity-openai")], projection=RUN_THREE_PROJECTION, cost=1.10)), "As supplied.")

    def test_a_run_inside_its_projection_but_missing_a_seat_reports_it_anyway(self):
        caveat = core.method_caveat("", manifest(RUN_THREE_SEATS, projection=RUN_THREE_PROJECTION, cost=1.10))
        self.assertIn("projected $2.30 and the run was billed $1.10", caveat)
        self.assertIn("under the pre-flight", caveat)

    def test_a_manifest_with_no_projection_block_says_nothing(self):
        self.assertEqual(core.method_caveat("As supplied.", manifest(
            [seat("fidelity-openai")], cost=9.99)), "As supplied.")

    def test_a_run_that_never_reached_wrap_up_says_nothing(self):
        self.assertEqual(core.method_caveat("As supplied.", manifest(
            [seat("fidelity-openai")], projection=RUN_THREE_PROJECTION)), "As supplied.")


class WholeCaveatTest(unittest.TestCase):

    def test_run_threes_manifest_produces_all_three_appendices_under_the_supplied_text(self):
        supplied = "Three model families reported: xAI, Moonshot and OpenAI."
        caveat = core.method_caveat(supplied, manifest(
            RUN_THREE_SEATS, RUN_THREE_MIN_FAMILIES, RUN_THREE_PROJECTION, 2.752117))
        self.assertTrue(caveat.startswith(supplied), "the supplied text is never rewritten")
        self.assertIn("buildability-glm", caveat)
        self.assertIn("4 seated, 3 reporting", caveat)
        self.assertIn("projected $2.30", caveat)
        self.assertEqual(len(caveat.split("\n\n")), 4, "the supplied text plus three appendices")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""The run lifecycle: claim, materialize, project, the budget gate, dispatch's CAS, and resume.

No network and no paid call: `run_panel.py` shells out to `dispatch.py` as it always does, and the
connector both of them load is `fake_backend.py`, scripted through a plan file.

    python3 scripts/tests/test_run_lifecycle.py
"""

import io
import json
import os
import socket
import stat
import subprocess
import sys
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import harness  # noqa: E402
import run_panel  # noqa: E402
from lib import budget as budget_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import runs as runs_lib  # noqa: E402


# --- the library, on its own ----------------------------------------------------------------------

class ClaimTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)

    def test_the_claim_is_an_exclusive_mkdir(self):
        claimed = runs_lib.claim_run_dir(self.workspace.path("reviews", "2026-09-18-1"))
        self.assertTrue(os.path.isdir(claimed))
        self.assertEqual(os.path.basename(claimed), "2026-09-18-1")

    def test_a_collision_increments_the_sequence_number_and_retries(self):
        first = runs_lib.claim_run_dir(self.workspace.path("reviews", "2026-09-18-1"))
        second = runs_lib.claim_run_dir(self.workspace.path("reviews", "2026-09-18-1"))
        third = runs_lib.claim_run_dir(self.workspace.path("reviews", "2026-09-18-1"))
        self.assertEqual(os.path.basename(second), "2026-09-18-2")
        self.assertEqual(os.path.basename(third), "2026-09-18-3")
        self.assertNotEqual(first, second)
        self.assertTrue(all(os.path.isdir(d) for d in (first, second, third)))

    def test_a_name_with_no_sequence_number_gains_one(self):
        runs_lib.claim_run_dir(self.workspace.path("reviews", "run"))
        again = runs_lib.claim_run_dir(self.workspace.path("reviews", "run"))
        self.assertEqual(os.path.basename(again), "run-2")


class MaterializeTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.run_dir = runs_lib.claim_run_dir(self.workspace.path("run"))
        self.addCleanup(self.workspace.close)

    def test_inputs_are_copied_read_only_and_hashed_from_the_bytes_written(self):
        records = runs_lib.materialize(self.run_dir, [self.workspace.artifact, self.workspace.reference])
        artifact = records[0]
        landed = os.path.join(self.run_dir, artifact["materialized"])
        self.assertTrue(os.path.isfile(landed))
        self.assertEqual(artifact["revision"], runs_lib.sha256_file(landed))
        self.assertEqual(artifact["revision"], runs_lib.sha256_file(self.workspace.artifact))
        mode = stat.S_IMODE(os.stat(landed).st_mode)
        self.assertEqual(mode, 0o444, "materialized inputs are read-only")

    def test_the_revision_follows_the_bytes_not_the_path(self):
        before = runs_lib.materialize(self.run_dir, [self.workspace.artifact])[0]["revision"]
        with open(self.workspace.artifact, "a", encoding="utf-8") as handle:
            handle.write("\nOne more sentence.\n")
        after = runs_lib.preview([self.workspace.artifact])[0]["revision"]
        self.assertNotEqual(before, after)

    def test_two_references_with_one_basename_do_not_overwrite_each_other(self):
        nested = self.workspace.path("nested")
        os.makedirs(nested)
        twin = os.path.join(nested, "reference.md")
        with open(twin, "w", encoding="utf-8") as handle:
            handle.write("# A different reference\n")
        records = runs_lib.materialize(self.run_dir, [self.workspace.artifact, self.workspace.reference, twin])
        names = [r["materialized"] for r in records]
        self.assertEqual(len(set(names)), 3, names)
        self.assertNotEqual(records[1]["revision"], records[2]["revision"])

    def test_the_fingerprint_ignores_reference_order_and_moves_with_the_artifact(self):
        records = runs_lib.materialize(self.run_dir, [self.workspace.artifact, self.workspace.reference])
        one = runs_lib.input_fingerprint(records[0], records[1:], "test-panel", "standard")
        two = runs_lib.input_fingerprint(records[0], list(reversed(records[1:])), "test-panel", "standard")
        self.assertEqual(one, two)
        self.assertNotEqual(one, runs_lib.input_fingerprint(records[0], records[1:], "test-panel", "frontier"))


class GitCommitTest(unittest.TestCase):
    """The commit id recorded beside a revision — and, more importantly, when it is not recorded.

    A commit id beside bytes that have changed since reads as a pin and is not one, so a file whose
    working tree is dirty gets the hash alone.
    """

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.repo = self.workspace.path("repo")
        os.makedirs(self.repo)
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.invalid")
        self._git("config", "user.name", "Test")
        self.tracked = os.path.join(self.repo, "artifact.md")
        with open(self.tracked, "w", encoding="utf-8") as handle:
            handle.write("# Committed\n")
        self._git("add", "artifact.md")
        self._git("commit", "-q", "-m", "seed")

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.repo] + list(args), capture_output=True, text=True)

    def test_a_clean_file_records_the_commit_id(self):
        head = self._git("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(runs_lib.git_commit_if_clean(self.tracked), head)

    def test_a_dirty_file_records_no_commit_id(self):
        with open(self.tracked, "a", encoding="utf-8") as handle:
            handle.write("Edited since the commit.\n")
        self.assertIsNone(runs_lib.git_commit_if_clean(self.tracked),
                          "a commit id beside edited bytes reads as a pin and is not one")

    def test_an_untracked_file_records_no_commit_id(self):
        untracked = os.path.join(self.repo, "draft.md")
        with open(untracked, "w", encoding="utf-8") as handle:
            handle.write("# Never committed\n")
        self.assertIsNone(runs_lib.git_commit_if_clean(untracked))

    def test_a_file_outside_any_repository_records_no_commit_id(self):
        self.assertIsNone(runs_lib.git_commit_if_clean(self.workspace.artifact))

    def test_materialize_carries_the_commit_id_through(self):
        run_dir = runs_lib.claim_run_dir(self.workspace.path("run"))
        record = runs_lib.materialize(run_dir, [self.tracked])[0]
        self.assertEqual(record["commit"], self._git("rev-parse", "HEAD").stdout.strip())
        self.assertEqual(record["revision"], runs_lib.sha256_file(self.tracked))


class CompareAndSetTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.run_dir = runs_lib.claim_run_dir(self.workspace.path("run"))
        runs_lib.write_manifest(self.run_dir, {"run_id": "x", "seats": [
            {"reviewer_id": "consistency-kimi", "status": "pending"},
            {"reviewer_id": "adversarial-xai", "status": "ok"},
        ]})
        self.addCleanup(self.workspace.close)

    def test_a_pending_seat_is_claimed_once(self):
        applied, seen = runs_lib.claim_seat(self.run_dir, "consistency-kimi")
        self.assertTrue(applied)
        self.assertEqual(seen, "pending")
        self.assertEqual(self._status("consistency-kimi"), "dispatching")

    def test_a_seat_already_dispatching_is_left_strictly_alone(self):
        runs_lib.claim_seat(self.run_dir, "consistency-kimi")
        applied, seen = runs_lib.claim_seat(self.run_dir, "consistency-kimi")
        self.assertFalse(applied, "two resumes must not both pay for one seat")
        self.assertEqual(seen, "dispatching")

    def test_a_seat_that_already_reported_is_not_reclaimed(self):
        applied, seen = runs_lib.claim_seat(self.run_dir, "adversarial-xai")
        self.assertFalse(applied)
        self.assertEqual(seen, "ok")

    def _status(self, reviewer_id):
        manifest = runs_lib.read_manifest(self.run_dir)
        return next(s["status"] for s in manifest["seats"] if s["reviewer_id"] == reviewer_id)


class ProjectionTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.registry = registry_lib.load(self.workspace.registry)
        self.addCleanup(self.workspace.close)

    def test_a_projection_prices_prompt_tokens_plus_the_prior_plus_both_allowances(self):
        seats = [{"reviewer_id": "adversarial-xai", "model": harness.FAST_MODEL, "prompt_tokens": 100000}]
        projection = budget_lib.project(seats, self.registry, 5.0)
        row = projection["per_seat"][0]
        catalogue = 1e-07 * 100000 + 5e-07 * 10000
        overrun = 5e-07 * 10000 * (budget_lib.OUTPUT_OVERRUN_ALLOWANCE - 1.0)
        self.assertAlmostEqual(row["catalogue_usd"], catalogue, places=8)
        self.assertAlmostEqual(row["overrun_allowance_usd"], overrun, places=8,
                               msg="the overrun allowance lands on the output line only")
        self.assertAlmostEqual(row["base_usd"], catalogue + overrun, places=8)
        self.assertAlmostEqual(row["repair_allowance_usd"],
                               (catalogue + overrun) * budget_lib.REPAIR_ALLOWANCE_FRACTION, places=8)
        self.assertTrue(projection["estimate"])

        rendered = budget_lib.render(projection)
        self.assertIn("ESTIMATE", rendered)
        for label in ("catalogue prices", "output overrun allowance", "repair allowance"):
            self.assertIn(label, rendered, "each allowance is printed as its own labelled line")

    def test_the_overrun_allowance_reproduces_run_twos_actual_spend_from_catalogue_prices(self):
        """The seed's calibration, pinned against the frozen manifest it was derived from.

        Run 2 cost $1.47. Its four seats' measured prompt tokens and the shipped registry's priors
        and catalogue prices are the inputs; the multiplier is what closes the gap. If a future
        refresh moves the catalogue prices far enough that this no longer holds, the multiplier is
        the thing to re-derive, and this test is how that is noticed.
        """
        registry = registry_lib.load(registry_lib.DEFAULT_REGISTRY)
        seats = [
            {"reviewer_id": "fidelity-openai", "model": "openai/gpt-5.6-sol", "prompt_tokens": 59287},
            {"reviewer_id": "buildability-glm", "model": "z-ai/glm-5.3-flash", "prompt_tokens": 60939},
            {"reviewer_id": "consistency-kimi", "model": "moonshotai/kimi-k3", "prompt_tokens": 58688},
            {"reviewer_id": "adversarial-xai", "model": "x-ai/grok-4.6", "prompt_tokens": 60270},
        ]
        # Run 2 was reconciled by the host in session, so it made no synthesis call.
        projection = budget_lib.project(seats, registry, 5.0, with_synthesis=False)
        self.assertGreaterEqual(
            projection["projection_usd"], 1.47,
            "the projection must not read below the $1.47 the run actually cost; got ${0:.4f}".format(
                projection["projection_usd"]))
        self.assertLess(projection["projection_usd"], 2.00, "and it must not become a wild over-estimate")

    def test_the_synthesis_allowance_is_only_charged_when_synthesis_will_run(self):
        seats = [{"reviewer_id": "adversarial-xai", "model": harness.FAST_MODEL, "prompt_tokens": 100000}]
        charged = budget_lib.project(seats, self.registry, 5.0, with_synthesis=True)
        not_charged = budget_lib.project(seats, self.registry, 5.0, with_synthesis=False)
        self.assertIsNotNone(charged["synthesis_allowance_usd"])
        self.assertIsNone(not_charged["synthesis_allowance_usd"])
        self.assertGreater(charged["projection_usd"], not_charged["projection_usd"])
        self.assertIn("not charged", budget_lib.render(not_charged))

    def test_an_unpriced_model_cannot_be_projected_at_all(self):
        """Defence in depth. The composition gate refuses these seats before the projection sees
        them, but the arithmetic must still return None rather than a zero if one ever gets here."""
        workspace = harness.Workspace(models={
            harness.FAST_MODEL: {"input_price_per_token": None, "output_price_per_token": None,
                                 "context_limit": 1000, "output_token_prior": 100}})
        self.addCleanup(workspace.close)
        projection = budget_lib.project(
            [{"reviewer_id": "adversarial-xai", "model": harness.FAST_MODEL, "prompt_tokens": 10}],
            registry_lib.load(workspace.registry), 5.0)
        self.assertIsNone(projection["per_seat"][0]["projected_usd"])
        self.assertIsNone(projection["per_seat"][0]["base_usd"])

    def test_a_prompt_over_the_context_limit_is_flagged_and_left_out(self):
        workspace = harness.Workspace(models={
            harness.FAST_MODEL: {"input_price_per_token": 1e-07, "output_price_per_token": 5e-07,
                                 "context_limit": 1000, "output_token_prior": 100}})
        self.addCleanup(workspace.close)
        projection = budget_lib.project(
            [{"reviewer_id": "adversarial-xai", "model": harness.FAST_MODEL, "prompt_tokens": 500000}],
            registry_lib.load(workspace.registry), 5.0)
        self.assertEqual(projection["context_overflows"], ["adversarial-xai"])
        self.assertEqual(projection["projection_usd"], 0.0, "an overflowing seat is never dispatched, so never priced")


# --- the whole script -----------------------------------------------------------------------------

class PanelTestCase(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)

    def run_panel(self, extra=None, out=None):
        argv = [
            "--panel", self.workspace.panel,
            "--artifact", self.workspace.artifact,
            "--ref", self.workspace.reference,
            "--out", out or self.workspace.path("reviews", "2026-09-18-1"),
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
            "--tier", "standard",
            "--autonomous",
            # The Judge stage is pinned off: these tests are about dispatch, and an autonomous run
            # would otherwise finish the pipeline by asking the fake backend for a judgment patch.
            "--reconcile", "off",
        ] + list(extra or [])
        out_stream, err_stream = io.StringIO(), io.StringIO()
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_stream, err_stream
        try:
            code = run_panel.main(argv)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        return code, out_stream.getvalue(), err_stream.getvalue()

    def both_seats_report(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })


class RegistryGateTest(PanelTestCase):
    """`run_panel.py`'s own Resolve-stage gate, which is not `dispatch.py`'s."""

    def test_a_model_absent_from_the_registry_refuses_the_whole_run(self):
        models = harness.default_models()
        del models[harness.SLOW_MODEL]
        self._refuses(models, "is not in the registry")

    def test_a_model_with_a_null_price_refuses_the_whole_run(self):
        """Presence is not coverage: a seat with no price is a budget gate that does not gate."""
        models = harness.default_models()
        models[harness.SLOW_MODEL]["output_price_per_token"] = None
        self._refuses(models, "carries no output price")

    def test_a_model_missing_both_prices_names_both(self):
        models = harness.default_models()
        models[harness.SLOW_MODEL]["input_price_per_token"] = None
        models[harness.SLOW_MODEL]["output_price_per_token"] = None
        self._refuses(models, "carries no input or output price")

    def _refuses(self, models, expected):
        workspace = harness.Workspace(models=models)
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.plan({harness.FAST_MODEL: [{"body": harness.valid_report()}]})
        self.workspace = workspace

        code, _out, err = self.run_panel()
        self.assertEqual(code, 1, err)
        self.assertIn(expected, err)
        self.assertIn(harness.SLOW_MODEL, err)
        self.assertIn("refresh_models.py --add", err)
        self.assertEqual(workspace.calls(), [], "nothing is dispatched, not even the priced seat")


class BudgetGateTest(PanelTestCase):

    def test_an_autonomous_run_over_budget_refuses_before_any_paid_call(self):
        self.both_seats_report()
        code, out, err = self.run_panel(extra=["--budget-usd", "0.001"])
        self.assertEqual(code, 4, err)
        self.assertEqual(self.workspace.calls(), [], "no paid call was made")

        refusal_path = self.workspace.path("reviews", "2026-09-18-1", "budget-refusal.json")
        self.assertTrue(os.path.isfile(refusal_path), out)
        with open(refusal_path, "r", encoding="utf-8") as handle:
            refusal = json.load(handle)
        for field in ("budget_usd", "projection_usd", "per_seat", "shortfall_usd"):
            self.assertIn(field, refusal)
        self.assertEqual(refusal["budget_usd"], 0.001)
        self.assertGreater(refusal["shortfall_usd"], 0)
        self.assertEqual(sorted(row["reviewer_id"] for row in refusal["per_seat"]),
                         ["adversarial-xai", "consistency-kimi"])
        for row in refusal["per_seat"]:
            self.assertIn("prior_tokens", row)
            self.assertIn("projected_usd", row)

    def test_every_seat_is_still_pending_when_the_refusal_fires(self):
        """The refusal happens after Resolve wrote the manifest and before any seat was claimed.

        A seat left `dispatching` by a refused run would be held by the next resume forever, and a
        seat marked anything but `pending` would say the run got further than it did.
        """
        self.both_seats_report()
        code, _out, err = self.run_panel(extra=["--budget-usd", "0.001"])
        self.assertEqual(code, 4, err)
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "2026-09-18-1"))
        self.assertEqual([seat["status"] for seat in manifest["seats"]], ["pending", "pending"])
        for seat in manifest["seats"]:
            self.assertIsNone(seat["report"])
            self.assertIsNone(seat["cost_usd"])
            self.assertNotIn("claimed_at", seat)

    def test_an_interactive_run_is_not_charged_for_a_synthesis_call(self):
        self.both_seats_report()
        _code, autonomous_out, _err = self.run_panel()
        self.assertIn("synthesis call on", autonomous_out,
                      "an autonomous run with the default reconciler will dispatch synthesis")

        self.workspace.reset_calls()
        _code, host_out, _err = self.run_panel(extra=["--reconciler", "host", "--fresh"])
        self.assertIn("not charged", host_out)
        self.assertIn("comes from the host", host_out)

    def test_approve_budget_dispatches_anyway(self):
        self.both_seats_report()
        code, out, err = self.run_panel(extra=["--budget-usd", "0.001", "--approve-budget"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.workspace.calls()), 2)
        self.assertFalse(os.path.isfile(self.workspace.path("reviews", "2026-09-18-1", "budget-refusal.json")))

    def test_the_projection_is_printed_before_dispatch_and_labelled_an_estimate(self):
        self.both_seats_report()
        _code, out, _err = self.run_panel()
        self.assertIn("Cost pre-flight", out)
        self.assertIn("ESTIMATE", out)
        self.assertIn("against a budget of", out)


class ContextOverflowTest(PanelTestCase):

    def test_an_overflowing_seat_is_never_re_seated_and_the_run_is_under_seated(self):
        tight = harness.default_models()
        tight[harness.SLOW_MODEL]["context_limit"] = 10
        workspace = harness.Workspace(models=tight)
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.plan({harness.FAST_MODEL: [{"body": harness.valid_report()}]})
        self.workspace = workspace

        code, out, err = self.run_panel()
        self.assertEqual(code, 3, "under-seated is exit 3, not a terminal failure")
        self.assertIn("CONTEXT OVERFLOW", out)

        manifest = runs_lib.read_manifest(workspace.path("reviews", "2026-09-18-1"))
        overflowed = next(s for s in manifest["seats"] if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(overflowed["status"], "failed")
        self.assertEqual(overflowed["failure_reason"], "context-overflow")
        self.assertEqual(workspace.calls(harness.SLOW_MODEL), [], "an overflowing seat is not dispatched")
        self.assertEqual(len(workspace.calls(harness.FAST_MODEL)), 1)
        self.assertEqual({s["family"] for s in manifest["seats"]}, {"kimi", "xai"},
                         "the lens is not re-seated onto another family")

    def test_the_recorded_projection_is_recomputed_over_the_seats_actually_dispatched(self):
        """Otherwise the caveat compares one seat's billed spend against a two-seat projection."""
        tight = harness.default_models()
        tight[harness.SLOW_MODEL]["context_limit"] = 10
        workspace = harness.Workspace(models=tight)
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.plan({harness.FAST_MODEL: [{"body": harness.valid_report(), "cost": 0.05}]})
        self.workspace = workspace

        code, out, err = self.run_panel()
        self.assertEqual(code, 3, err)
        self.assertIn("CONTEXT OVERFLOW", out, "the operator is still shown the full pre-flight")

        manifest = runs_lib.read_manifest(workspace.path("reviews", "2026-09-18-1"))
        projection = manifest["projection"]
        self.assertEqual([row["reviewer_id"] for row in projection["per_seat"]], ["adversarial-xai"],
                         "the overflowing seat is out of the recorded table")
        self.assertEqual(projection["excluded_seats"], ["consistency-kimi"])
        self.assertEqual(projection["context_overflows"], ["consistency-kimi"],
                         "the drop stays visible in the record rather than vanishing from it")
        self.assertEqual(projection["decision"], "within-budget")

    def test_the_recomputed_projection_is_what_the_cost_caveat_compares_against(self):
        tight = harness.default_models()
        tight[harness.SLOW_MODEL]["context_limit"] = 10
        workspace = harness.Workspace(models=tight)
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.plan({harness.FAST_MODEL: [{"body": harness.valid_report(), "cost": 0.05}]})
        self.workspace = workspace
        self.assertEqual(self.run_panel()[0], 3)

        manifest = runs_lib.read_manifest(workspace.path("reviews", "2026-09-18-1"))
        priced = [row["projected_usd"] for row in manifest["projection"]["per_seat"]]
        self.assertEqual(len(priced), 1)
        # The recorded total is the one seat's projection plus the synthesis allowance, and the
        # allowance is priced off the largest prompt among the seats that remain.
        self.assertLessEqual(priced[0], manifest["projection"]["projection_usd"])
        self.assertAlmostEqual(manifest["cost_usd_total"], 0.05, places=6)


class ManifestTest(PanelTestCase):

    def test_the_manifest_is_written_at_resolve_with_every_input_pinned(self):
        self.both_seats_report()
        code, _out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        manifest = runs_lib.read_manifest(run_dir)

        self.assertEqual(manifest["artifact_revision"], runs_lib.sha256_file(self.workspace.artifact))
        self.assertEqual(manifest["artifact_input"], os.path.join("inputs", "artifact.md"))
        self.assertEqual(len(manifest["reference_revisions"]), 1)
        self.assertTrue(manifest["input_fingerprint"])
        self.assertIn("roots", manifest)
        for seat in manifest["seats"]:
            self.assertEqual(seat["status"], "ok")
            self.assertIsNotNone(seat["max_tokens"])
            self.assertIsNotNone(seat["attempts"])
            self.assertEqual(seat["truncations"], 0)
            self.assertIsNotNone(seat["reasoning_tokens"])
            self.assertIsNotNone(seat["cost_usd"])
        self.assertAlmostEqual(manifest["cost_usd_total"], 0.02, places=6)

    def test_a_failed_seat_carries_its_cost_into_the_run_total(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": {"verdict": "nonsense", "summary": "x", "findings": []}, "cost": 0.4}],
            harness.FAST_MODEL: [{"body": harness.valid_report(), "cost": 0.1}],
        })
        code, _out, err = self.run_panel()
        self.assertEqual(code, 3, err)
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "2026-09-18-1"))
        failed = next(s for s in manifest["seats"] if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(failed["status"], "failed")
        self.assertAlmostEqual(failed["cost_usd"], 0.8, places=6, msg="two attempts at $0.40")
        self.assertAlmostEqual(manifest["cost_usd_total"], 0.9, places=6,
                               msg="cost_usd_total includes the failed seat")

    def test_the_manifest_records_the_resolved_tier_and_the_level_that_decided_it(self):
        """Run 3 ran `--tier standard` and the manifest's only tier field was the *input* to seating."""
        self.both_seats_report()
        code, _out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "2026-09-18-1"))
        self.assertEqual(manifest["tier"]["resolved"], "standard")
        self.assertEqual(manifest["tier"]["source"], "--tier")
        self.assertTrue(manifest["tier"]["unanimous"])
        self.assertEqual(set(manifest["tier"]["per_seat"]), {"consistency-kimi", "adversarial-xai"})

    def test_a_panel_without_a_cli_tier_records_the_level_that_did_decide(self):
        self.both_seats_report()
        argv_without_tier = ["--panel", self.workspace.panel, "--artifact", self.workspace.artifact,
                             "--ref", self.workspace.reference,
                             "--out", self.workspace.path("reviews", "no-cli-tier"),
                             "--config", self.workspace.config, "--models", self.workspace.registry,
                             "--autonomous", "--reconcile", "off"]
        out_stream, err_stream = io.StringIO(), io.StringIO()
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_stream, err_stream
        try:
            code = run_panel.main(argv_without_tier)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        self.assertEqual(code, 0, err_stream.getvalue())
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "no-cli-tier"))
        self.assertEqual(manifest["tier"]["resolved"], "standard")
        self.assertEqual(manifest["tier"]["source"], "panel",
                         "the test panel sets `tier`, so the panel is the level that decided it")

    def test_the_manifest_records_the_projection_the_budget_gate_read(self):
        """The pre-flight used to live on the console and die with it; nothing could compare it."""
        self.both_seats_report()
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "2026-09-18-1"))
        projection = manifest["projection"]
        self.assertTrue(projection["estimate"], "the recorded block is labelled an estimate too")
        self.assertEqual(projection["budget_usd"], run_panel.DEFAULT_BUDGET_USD)
        self.assertEqual({row["reviewer_id"] for row in projection["per_seat"]},
                         {"consistency-kimi", "adversarial-xai"})
        for key in ("projection_usd", "catalogue_usd", "overrun_allowance_usd",
                    "repair_allowance_usd", "synthesis_allowance_usd"):
            self.assertIn(key, projection)
        self.assertEqual(projection["decision"], "within-budget")
        self.assertIn("projected ${0:.2f}".format(projection["projection_usd"]), out,
                      "the recorded number is the one the operator was shown")

    def test_the_projection_and_the_actual_are_both_in_the_manifest_to_be_compared(self):
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "2026-09-18-1"))
        self.assertIsNotNone(manifest["projection"]["projection_usd"])
        self.assertIsNotNone(manifest["cost_usd_total"])

    def test_an_over_budget_refusal_still_records_what_it_refused_over(self):
        self.both_seats_report()
        code, _out, _err = self.run_panel(extra=["--budget-usd", "0.001"])
        self.assertEqual(code, run_panel.EXIT_BUDGET)
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "2026-09-18-1"))
        self.assertEqual(manifest["projection"]["decision"], "refused-over-budget")
        self.assertEqual(manifest["projection"]["budget_usd"], 0.001)
        self.assertIsNone(manifest["cost_usd_total"], "no paid call was made")

    def test_approving_an_over_budget_projection_is_recorded_as_such(self):
        self.both_seats_report()
        code, _out, err = self.run_panel(extra=["--budget-usd", "0.001", "--approve-budget"])
        self.assertEqual(code, 0, err)
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "2026-09-18-1"))
        self.assertEqual(manifest["projection"]["decision"], "approved-over-budget")

    def test_unbilled_upstream_spend_reaches_the_seat_and_stays_out_of_the_run_total(self):
        """`cost_usd_total` reconciles against the credit ledger; unbilled inference must not enter it."""
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report(), "cost": 0,
                                  "cost_details": {"upstream_inference_cost": 1.816461}}],
            harness.FAST_MODEL: [{"body": harness.valid_report(), "cost": 0.1}],
        })
        code, _out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        manifest = runs_lib.read_manifest(self.workspace.path("reviews", "2026-09-18-1"))
        seat = next(s for s in manifest["seats"] if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(seat["cost_usd"], 0.0)
        self.assertAlmostEqual(seat["upstream_unbilled_usd"], 1.816461, places=6)
        self.assertAlmostEqual(manifest["cost_usd_total"], 0.1, places=6,
                               msg="the run total is what the account was billed and nothing else")
        other = next(s for s in manifest["seats"] if s["reviewer_id"] == "adversarial-xai")
        self.assertIsNone(other["upstream_unbilled_usd"])

    def test_a_seat_that_dies_in_the_transport_is_never_recorded_with_null_attempts(self):
        """Run 3's `buildability-glm`: 147 seconds, `attempts: null`, `cost_usd: null`, no record."""
        self.workspace.plan({
            harness.SLOW_MODEL: [{"raise": "incomplete"}],
            harness.FAST_MODEL: [{"body": harness.valid_report(), "cost": 0.1}],
        })
        code, out, err = self.run_panel()
        self.assertEqual(code, 3, err)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "consistency-kimi.failed.json")))
        seat = next(s for s in runs_lib.read_manifest(run_dir)["seats"]
                    if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(seat["status"], "failed")
        self.assertIsNotNone(seat["attempts"], "a called seat is never `attempts: null`")
        self.assertEqual(len(seat["attempts"]), 1)
        self.assertEqual(seat["attempts"][0]["finish_reason"], "dispatch-error")
        self.assertIsNotNone(seat["attempts"][0]["elapsed_s"])
        self.assertIn("IncompleteRead", seat["attempts"][0]["validation_errors"][0])
        self.assertEqual(seat["dispatches"], 1)
        del out

    def test_seats_read_the_inputs_copy_not_the_working_tree(self):
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        with open(os.path.join(run_dir, "consistency-kimi.json"), "r", encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["artifact_revision"], runs_lib.sha256_file(self.workspace.artifact))
        self.assertTrue(report["artifact"].endswith("artifact.md"))
        self.assertNotIn("inputs/", report["artifact"], "the reviewer cites the working-tree path")


class ResumeTest(PanelTestCase):

    def test_resume_re_dispatches_only_the_seats_whose_reports_are_missing(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": {"verdict": "nonsense", "summary": "x", "findings": []}}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        self.assertEqual(self.run_panel()[0], 3)
        self.assertEqual(len(self.workspace.calls(harness.FAST_MODEL)), 1)

        self.workspace.reset_calls()
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.workspace.calls(harness.FAST_MODEL), [],
                         "a validated report is never re-dispatched — a repeat panel is not a no-op purchase")
        self.assertEqual(len(self.workspace.calls(harness.SLOW_MODEL)), 1)
        self.assertIn("keeping adversarial-xai", out)

    def test_resume_re_dispatches_a_seat_whose_report_was_deleted(self):
        """The report on disk is the authority, not the manifest status.

        The manifest says `ok` and the report is gone. A resume that trusted the status would print
        "2 of 2", exit 0 and make no call, over a directory holding one report — and the run that
        reconciled it would be reconciling three seats while claiming four.
        """
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        os.remove(os.path.join(run_dir, "consistency-kimi.json"))
        self.assertEqual(self._status(run_dir, "consistency-kimi"), "ok", "the manifest still says ok")

        self.workspace.reset_calls()
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.workspace.calls(harness.SLOW_MODEL)), 1,
                         "the seat with no report on disk is re-dispatched")
        self.assertEqual(self.workspace.calls(harness.FAST_MODEL), [],
                         "the seat that did report is still not re-dispatched")
        self.assertNotIn("another process owns this seat", out)
        self.assertIn("Seats reporting: 2 of 2", out)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "consistency-kimi.json")))

    def test_resume_re_dispatches_a_seat_whose_report_is_corrupted(self):
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        with open(os.path.join(run_dir, "consistency-kimi.json"), "w", encoding="utf-8") as handle:
            handle.write('{ "verdict": "nonsense", "findings": ')

        self.workspace.reset_calls()
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.workspace.calls(harness.SLOW_MODEL)), 1)
        self.assertEqual(self.workspace.calls(harness.FAST_MODEL), [])
        self.assertNotIn("another process owns this seat", out)
        self.assertEqual(self._status(run_dir, "consistency-kimi"), "ok")

    def test_a_re_dispatched_seat_carries_its_earlier_spend_forward(self):
        """A seat dispatched twice was paid for twice, and the manifest has to say so.

        Overwriting the seat record on the second dispatch would drop the first one's money out of
        both `cost_usd` and `cost_usd_total` — the same under-reporting the failed-seat accounting
        exists to close, one level up.
        """
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report(), "cost": 1.0}],
            harness.FAST_MODEL: [{"body": harness.valid_report(), "cost": 0.1}],
        })
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        self.assertAlmostEqual(runs_lib.read_manifest(run_dir)["cost_usd_total"], 1.1, places=6)

        with open(os.path.join(run_dir, "consistency-kimi.json"), "w", encoding="utf-8") as handle:
            handle.write("{ corrupted")

        self.workspace.reset_calls()
        code, _out, err = self.run_panel()
        self.assertEqual(code, 0, err)

        manifest = runs_lib.read_manifest(run_dir)
        seat = next(s for s in manifest["seats"] if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(seat["dispatches"], 2)
        self.assertAlmostEqual(seat["cost_usd"], 2.0, places=6,
                               msg="the seat was dispatched twice at $1.00 and spent $2.00")
        self.assertAlmostEqual(manifest["cost_usd_total"], 2.1, places=6,
                               msg="$2.00 on the re-dispatched seat plus $0.10 on the one that held")
        self.assertEqual([attempt["dispatch"] for attempt in seat["attempts"]], [1, 2],
                         "attempts carry one entry per call across both dispatches, each tagged")
        self.assertEqual([attempt["cost_usd"] for attempt in seat["attempts"]], [1.0, 1.0])

        untouched = next(s for s in manifest["seats"] if s["reviewer_id"] == "adversarial-xai")
        self.assertEqual(untouched["dispatches"], 1, "the seat that was not re-dispatched is unchanged")
        self.assertAlmostEqual(untouched["cost_usd"], 0.1, places=6)

    def test_a_seat_that_cannot_be_repaired_is_reported_as_missing_not_as_reporting(self):
        """The count comes from disk, so a re-dispatch that fails again cannot read as a clean run."""
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        os.remove(os.path.join(run_dir, "consistency-kimi.json"))

        self.workspace.reset_calls()
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": {"verdict": "nonsense", "summary": "x", "findings": []}}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.run_panel()
        self.assertEqual(code, 3, err)
        self.assertIn("Seats reporting: 1 of 2", out)
        self.assertEqual(self._status(run_dir, "consistency-kimi"), "failed")

    def _status(self, run_dir, reviewer_id):
        manifest = runs_lib.read_manifest(run_dir)
        return next(s["status"] for s in manifest["seats"] if s["reviewer_id"] == reviewer_id)

    def test_a_seat_already_dispatching_is_held_not_re_dispatched(self):
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        os.remove(os.path.join(run_dir, "consistency-kimi.json"))
        runs_lib.update_seat(run_dir, "consistency-kimi", lambda seat: seat.update({"status": "dispatching"}))

        self.workspace.reset_calls()
        code, out, err = self.run_panel()
        self.assertEqual(code, 3, err)
        self.assertIn("holding consistency-kimi", out)
        self.assertIn("another process owns this seat", out)
        self.assertIn("held: 1", out, "a held seat has to reach the count, not just the log line")
        self.assertEqual(self.workspace.calls(), [], "a seat another process owns is left strictly alone")

    def test_a_dispatching_seat_whose_owner_is_gone_is_re_claimed_as_a_stale_lease(self):
        """Run 3's adversarial cluster SF-5: a seat that crashed mid-dispatch used to stay
        `dispatching` forever, and resume could only hold it. The lease now names its owner, so a
        resume can see that the owner is gone and demote the seat the way a missing report is."""
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        os.remove(os.path.join(run_dir, "consistency-kimi.json"))
        runs_lib.update_seat(run_dir, "consistency-kimi", lambda seat: seat.update({
            "status": "dispatching",
            "claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - 600)),
            # A pid on this host that cannot be running: pid 0 is never a user process.
            "claimed_by": {"pid": 2 ** 22 + 7, "host": socket.gethostname()},
        }))

        self.workspace.reset_calls()
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        self.assertIn("re-claiming consistency-kimi: stale lease", out)
        self.assertIn("is gone", out)
        self.assertEqual(len(self.workspace.calls(harness.SLOW_MODEL)), 1,
                         "the stale lease is re-dispatched, not held")
        self.assertEqual(self.workspace.calls(harness.FAST_MODEL), [])
        self.assertIn("Seats reporting: 2 of 2", out)

    def test_a_dispatching_seat_older_than_the_ceiling_is_re_claimed_whoever_holds_it(self):
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        os.remove(os.path.join(run_dir, "consistency-kimi.json"))
        stale = time.time() - runs_lib.LEASE_STALE_S - 60
        runs_lib.update_seat(run_dir, "consistency-kimi", lambda seat: seat.update({
            "status": "dispatching",
            "claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(stale)),
            "claimed_by": {"pid": os.getpid(), "host": "some-other-host"},
        }))

        self.workspace.reset_calls()
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        self.assertIn("past the", out)
        self.assertEqual(len(self.workspace.calls(harness.SLOW_MODEL)), 1)

    def test_a_stale_lease_is_recorded_as_such_before_it_is_re_dispatched(self):
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        os.remove(os.path.join(run_dir, "consistency-kimi.json"))
        claimed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - 600))
        runs_lib.update_seat(run_dir, "consistency-kimi", lambda seat: seat.update({
            "status": "dispatching", "claimed_at": claimed_at,
            "claimed_by": {"pid": 2 ** 22 + 7, "host": socket.gethostname()},
        }))

        self.workspace.reset_calls()
        # The re-dispatch fails, so the demotion the partition made is what the manifest still holds.
        self.workspace.plan({
            harness.SLOW_MODEL: [{"raise": "incomplete"}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, _out, err = self.run_panel()
        self.assertEqual(code, 3, err)
        seat = next(s for s in runs_lib.read_manifest(run_dir)["seats"]
                    if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(seat["stale_lease"]["claimed_at"], claimed_at)
        self.assertEqual(seat["stale_lease"]["claimed_by"]["pid"], 2 ** 22 + 7)
        self.assertIn("is gone", seat["stale_lease"]["reason"])

    def test_a_dispatching_seat_whose_report_did_land_is_kept_not_re_dispatched(self):
        """The owner died between writing the report and updating the manifest. Nothing to redo."""
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        runs_lib.update_seat(run_dir, "consistency-kimi", lambda seat: seat.update({
            "status": "dispatching",
            "claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - 600)),
            "claimed_by": {"pid": 2 ** 22 + 7, "host": socket.gethostname()},
        }))

        self.workspace.reset_calls()
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.workspace.calls(), [], "a report on disk is not re-purchased")
        self.assertIn("the lease is spent", out)

    def test_claim_seat_records_who_took_the_lease(self):
        """Without an owner a resume cannot tell a live dispatch from a crashed one, and holds both."""
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        runs_lib.update_seat(run_dir, "consistency-kimi",
                             lambda seat: seat.update({"status": "pending", "claimed_by": None}))
        claimed, _seen = runs_lib.claim_seat(run_dir, "consistency-kimi")
        self.assertTrue(claimed)
        seat = next(s for s in runs_lib.read_manifest(run_dir)["seats"]
                    if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(seat["claimed_by"], {"pid": os.getpid(), "host": socket.gethostname()})
        self.assertTrue(seat["claimed_at"])

    def test_a_stale_lease_demotion_clears_the_owner_and_keeps_the_old_claim(self):
        """A lease nobody holds must not read as one somebody does — and the evidence is kept."""
        seat = {"status": "dispatching", "claimed_at": "2026-09-19T03:42:44-0400",
                "claimed_by": {"pid": 4242, "host": "somewhere"}, "report": "x"}
        run_panel._stale_lease("its owner is gone", "no report file")(seat)
        self.assertEqual(seat["status"], "failed")
        self.assertEqual(seat["failure_reason"], "stale-lease")
        self.assertIsNone(seat["claimed_by"], "a cleared lease cannot be mistaken for a live one")
        self.assertIsNone(seat["report"])
        self.assertEqual(seat["stale_lease"]["claimed_at"], "2026-09-19T03:42:44-0400")
        self.assertEqual(seat["stale_lease"]["claimed_by"], {"pid": 4242, "host": "somewhere"})
        self.assertIn("its owner is gone", seat["stale_lease"]["reason"])
        self.assertIn("no report file", seat["error"])

    def test_a_foreign_host_claim_is_held_until_the_ceiling_however_dead_the_pid_looks(self):
        """The pid check is local. A pid absent here says nothing about a process on another host."""
        dead_here = 2 ** 22 + 1
        recent = {"claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                              time.localtime(time.time() - 3600)),
                  "claimed_by": {"pid": dead_here, "host": socket.gethostname() + "-elsewhere"}}
        stale, why = runs_lib.lease_is_stale(recent)
        self.assertFalse(stale, why)
        self.assertIn("owner is still running", why)

        aged = dict(recent, claimed_at=time.strftime(
            "%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - runs_lib.LEASE_STALE_S - 60)))
        stale, why = runs_lib.lease_is_stale(aged)
        self.assertTrue(stale, "the ceiling is the backstop for a claim this host cannot check")
        self.assertIn("past the", why)

    def test_the_same_claim_on_this_host_is_stale_at_once(self):
        """The companion to the test above: only the host string differs, and the answer flips."""
        seat = {"claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                            time.localtime(time.time() - 3600)),
                "claimed_by": {"pid": 2 ** 22 + 1, "host": socket.gethostname()}}
        stale, why = runs_lib.lease_is_stale(seat)
        self.assertTrue(stale)
        self.assertIn("is gone", why)

    def test_a_pid_we_may_not_signal_counts_as_alive(self):
        """`os.kill(pid, 0)` on another user's process raises PermissionError. That is not absence."""
        def refuse(_pid, _signal):
            raise PermissionError("not yours")

        saved = runs_lib.os.kill
        runs_lib.os.kill = refuse
        self.addCleanup(setattr, runs_lib.os, "kill", saved)
        self.assertTrue(runs_lib._pid_alive(4242),
                        "stealing a running process's lease is worse than waiting out the ceiling")

    def test_a_pid_that_is_absent_counts_as_gone(self):
        def absent(_pid, _signal):
            raise ProcessLookupError("no such process")

        saved = runs_lib.os.kill
        runs_lib.os.kill = absent
        self.addCleanup(setattr, runs_lib.os, "kill", saved)
        self.assertFalse(runs_lib._pid_alive(4242))

    def test_a_lease_with_no_claim_time_at_all_is_stale(self):
        stale, why = runs_lib.lease_is_stale({"status": "dispatching"})
        self.assertTrue(stale)
        self.assertIn("no claim time", why)

    def test_a_live_lease_held_by_this_process_is_not_stale(self):
        seat = {"claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "claimed_by": {"pid": os.getpid(), "host": socket.gethostname()}}
        stale, _why = runs_lib.lease_is_stale(seat)
        self.assertFalse(stale)

    def test_a_dead_owner_inside_the_grace_window_is_not_yet_stale(self):
        """The window between `claim_seat` writing the record and the subprocess starting."""
        seat = {"claimed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "claimed_by": {"pid": 2 ** 22 + 7, "host": socket.gethostname()}}
        stale, _why = runs_lib.lease_is_stale(seat)
        self.assertFalse(stale, "a claim seconds old is a claim in flight, not an abandoned one")

    def test_a_seat_claimed_between_partition_and_dispatch_is_held_and_counted(self):
        """The narrow race the compare-and-set exists for, forced by claiming behind the partition."""
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        os.remove(os.path.join(run_dir, "consistency-kimi.json"))

        original = runs_lib.claim_seat

        def claim_then_lose_the_race(directory, reviewer_id, *args, **kwargs):
            if reviewer_id == "consistency-kimi":
                original(directory, reviewer_id)          # another process gets there first
            return original(directory, reviewer_id, *args, **kwargs)

        runs_lib.claim_seat = claim_then_lose_the_race
        self.addCleanup(setattr, runs_lib, "claim_seat", original)
        self.workspace.reset_calls()
        code, out, err = self.run_panel()
        runs_lib.claim_seat = original

        self.assertEqual(code, 3, err)
        self.assertIn("held: 1", out)
        self.assertIn("another process owns this seat", out)
        self.assertEqual(self.workspace.calls(), [])

    def test_a_changed_artifact_refuses_the_resume_and_writes_nothing(self):
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        pinned_before = runs_lib.sha256_file(os.path.join(run_dir, "inputs", "artifact.md"))

        with open(self.workspace.artifact, "a", encoding="utf-8") as handle:
            handle.write("\n## Three\nA section the seats never saw.\n")
        self.workspace.reset_calls()
        code, _out, err = self.run_panel()
        self.assertEqual(code, 1, "mixing two documents' reviews in one directory is refused")
        self.assertIn("Start a new run id", err)
        self.assertEqual(self.workspace.calls(), [])
        self.assertEqual(runs_lib.sha256_file(os.path.join(run_dir, "inputs", "artifact.md")), pinned_before,
                         "the refused resume must not overwrite the pinned inputs it is refusing over")

    def test_fresh_claims_the_next_sequence_number_instead_of_resuming(self):
        self.both_seats_report()
        self.assertEqual(self.run_panel()[0], 0)
        self.workspace.reset_calls()
        code, out, err = self.run_panel(extra=["--fresh"])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(self.workspace.path("reviews", "2026-09-18-2")), out)
        self.assertEqual(len(self.workspace.calls()), 2, "a fresh claim dispatches the whole panel again")


class AuthHaltTest(PanelTestCase):

    def test_an_auth_failure_ends_the_run_with_exit_two(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"raise": "auth"}],
            harness.FAST_MODEL: [{"raise": "auth"}],
        })
        code, _out, err = self.run_panel()
        self.assertEqual(code, 2, err)
        self.assertIn("auth failure", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)

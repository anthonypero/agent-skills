#!/usr/bin/env python3
"""The harness judge: `agents/judge.md`, spawned by the session, ingested by `reconcile.py`.

Owner ruling, 2026-09-19 (SF-4). The judgment call used to land on the first non-`claude` family
the panel itself seated, which on every shipped template is the same family as the fidelity seat —
the correlation the no-Claude-seat rule exists to prevent, reintroduced one level up on the
arbitration path. The ruling seats the judge on a fixed, named harness agent on frontier Claude:
a family no seat holds, and no bill.

**A script cannot spawn a harness agent**, so what is tested here is the seam. `run_panel.py` stops
at the judge stage and prints the exact spawn instruction; the session spawns the agent; the agent
writes its patch to a seat-private staging path outside the run directory; `reconcile.py` ingests
it and holds it to its author. The agent itself is not run — this suite makes no calls of any kind
— so the seam is what there is to test, and the seam is where the mistakes would be.

No network and no paid call.

    python3 scripts/tests/test_harness_judge.py
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
import reconcile  # noqa: E402
import run_panel  # noqa: E402
from lib import judge as judge_lib  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
from lib import runs as runs_lib  # noqa: E402

RUN_ID = "2026-09-19-1"


def patch(author=judge_lib.HARNESS_JUDGE_AUTHOR, dispositions=None, **overrides):
    """A patch against the one provisional cluster the two scripted seats produce.

    Both findings are `judgment-call` and tagged `fork`, so `flag-for-human` is the only
    disposition an unattended author may give it — which is the floor this agent is held to.
    """
    document = {
        "schema_version": "1",
        "run_id": RUN_ID,
        "author": author,
        "generated_at": "2026-09-19T09:00:00-04:00",
        "dispositions": dispositions if dispositions is not None else [{
            "cluster": "P-1",
            "disposition": "flag-for-human",
            "disposition_reason": "Both seats file it as a design fork and neither reference settles it.",
        }],
        "method_caveat": "Two families reported; no reference was withheld.",
    }
    document.update(overrides)
    return document


def _capture(fn, argv):
    out_stream, err_stream = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_stream, err_stream
    try:
        code = fn(argv)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return code, out_stream.getvalue(), err_stream.getvalue()


class ReconcilerVocabularyTest(unittest.TestCase):
    """Who `default` resolves to, which now depends on whether a harness judge is installed."""

    def test_unattended_with_a_harness_is_the_harness_judge(self):
        self.assertEqual(judge_lib.resolve_reconciler(None, None, autonomous=True, harness=True),
                         judge_lib.HARNESS_JUDGE_AUTHOR)

    def test_unattended_without_one_falls_back_to_the_synthesis_persona(self):
        self.assertEqual(judge_lib.resolve_reconciler(None, None, autonomous=True, harness=False),
                         judge_lib.SYNTHESIS_AUTHOR)

    def test_an_attached_human_still_judges_whatever_is_installed(self):
        """The harness judge replaces the *unattended* judge. A host sitting there is not one."""
        self.assertEqual(judge_lib.resolve_reconciler(None, None, autonomous=False, harness=True),
                         "host")

    def test_an_explicit_choice_still_beats_it(self):
        self.assertEqual(
            judge_lib.resolve_reconciler("synthesis", None, autonomous=True, harness=True),
            "synthesis")
        self.assertEqual(
            judge_lib.resolve_reconciler(None, "host", autonomous=True, harness=True), "host")

    def test_a_template_may_name_it(self):
        self.assertEqual(
            judge_lib.resolve_reconciler(None, "harness-judge", autonomous=False),
            judge_lib.HARNESS_JUDGE_AUTHOR)

    def test_it_is_in_the_reconciler_vocabulary_and_the_unattended_floor(self):
        self.assertIn(judge_lib.HARNESS_JUDGE_AUTHOR, judge_lib.RECONCILERS)
        self.assertIn(judge_lib.HARNESS_JUDGE_AUTHOR, core.AUTHORS)
        self.assertIn(judge_lib.HARNESS_JUDGE_AUTHOR, core.UNATTENDED_AUTHORS)

    def test_detection_is_the_installed_agent_file_and_nothing_else(self):
        self.assertFalse(judge_lib.harness_present(os.path.join(SKILL_DIR, "no-such-dir")))
        self.assertFalse(judge_lib.harness_present(os.path.join(SKILL_DIR, "agents")),
                         "the package's own `judge.md` is not an installed `panel-judge`")


class HarnessJudgeRunTestCase(unittest.TestCase):
    """A real two-seat run with the judge installed, then the seam."""

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.workspace.install_harness_judge()
        self.run_dir = self.workspace.path("reviews", RUN_ID)
        self.staging = os.path.join(
            runs_lib.staging_dir(self.run_dir, judge_lib.HARNESS_JUDGE_AGENT), "judgment.json")

    def run_panel(self, extra=None):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        argv = ["--panel", self.workspace.panel, "--artifact", self.workspace.artifact,
                "--ref", self.workspace.reference, "--out", self.run_dir,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--tier", "standard", "--autonomous"] + list(extra or [])
        return _capture(run_panel.main, argv)

    def reconcile(self, extra=None):
        self.workspace.reset_calls()
        argv = ["--run-dir", self.run_dir, "--config", self.workspace.config,
                "--models", self.workspace.registry] + list(extra or [])
        return _capture(reconcile.main, argv)

    def patch_from_worksheet(self, **overrides):
        """A patch built from the ids `reconcile.py` actually wrote, the way the agent builds one.

        Hard-coding `P-1` tests the merge and never tests the question: a worksheet that was never
        written, or written with different ids, would pass just the same.
        """
        with open(os.path.join(self.run_dir, "judgment-request.json"), "r", encoding="utf-8") as handle:
            request = json.load(handle)
        dispositions = [{
            "cluster": cluster["provisional_id"],
            "disposition": "flag-for-human",
            "disposition_reason": "Every member files it as a design fork and no reference settles it.",
        } for cluster in request["clusters"]]
        self.assertTrue(dispositions, "the worksheet posed no question")
        return patch(dispositions=dispositions, **overrides), request

    def write_staged_patch(self, document):
        runs_lib.make_staging_dir(self.run_dir, judge_lib.HARNESS_JUDGE_AGENT)
        with open(self.staging, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        return self.staging

    def manifest(self):
        return runs_lib.read_manifest(self.run_dir)

    def wrote(self, name):
        return os.path.isfile(os.path.join(self.run_dir, name))

    def read(self, name):
        with open(os.path.join(self.run_dir, name), "r", encoding="utf-8") as handle:
            return json.load(handle)


class PanelStopsAtTheJudgeStageTest(HarnessJudgeRunTestCase):

    def test_an_unattended_run_with_the_judge_installed_records_it_as_the_reconciler(self):
        code, _out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.manifest()["reconciler"], judge_lib.HARNESS_JUDGE_AUTHOR)

    def test_the_manifest_records_the_answer_rather_than_the_premise(self):
        """What a later reader needs is who judged, not whether a file existed on some machine on
        some day. `reconcile.py` reads `reconciler`; nothing reads, or should read, the probe."""
        self.run_panel()
        self.assertNotIn("harness_judge_installed", self.manifest())

    def test_the_projection_charges_nothing_for_a_judgment_call(self):
        """It runs on the subscription. Charging a budget for it would gate a call nobody bills."""
        self.run_panel()
        projection = self.manifest()["projection"]
        self.assertIsNone(projection["synthesis_allowance_usd"])
        self.assertIsNone(self.manifest()["judge_seat"])

    def test_it_prints_the_spawn_instruction_and_makes_no_judgment_call(self):
        _code, out, err = self.run_panel()
        printed = out + err
        self.assertIn(judge_lib.HARNESS_JUDGE_AGENT, printed)
        self.assertIn(self.run_dir, printed)
        self.assertIn(self.staging, printed)
        self.assertIn(SKILL_DIR, printed, "the agent is told where the package is")
        self.assertFalse(self.wrote("judgment.json"))
        self.assertFalse(self.wrote("reconciliation.json"))

    def test_the_worksheet_exists_before_the_instruction_that_points_at_it(self):
        """The agent answers the provisional clusters, and nothing but `reconcile.py`'s first pass
        computes them. An instruction printed over a run directory with no `judgment-request.json`
        in it spawns an agent that reads the run, finds no question, and halts."""
        _code, out, err = self.run_panel()
        request = os.path.join(self.run_dir, "judgment-request.json")
        self.assertTrue(os.path.isfile(request), "the worksheet was never written")
        self.assertIn(request, out + err, "the instruction names the worksheet it just wrote")

    def test_the_worksheet_holds_the_clusters_the_patch_will_have_to_answer(self):
        """Question generation, not a hard-coded id: the ids come off disk."""
        self.run_panel()
        with open(os.path.join(self.run_dir, "judgment-request.json"), "r", encoding="utf-8") as handle:
            request = json.load(handle)
        self.assertTrue(request["clusters"], "the judge would have nothing to answer")
        for cluster in request["clusters"]:
            with self.subTest(cluster=cluster.get("provisional_id")):
                self.assertTrue(cluster["provisional_id"].startswith("P-"))
                self.assertTrue(cluster["members"])

    def test_the_printed_ingest_command_is_the_one_that_works(self):
        _code, out, err = self.run_panel()
        self.assertIn("--judgment {0}".format(self.staging), out + err)

    def test_a_run_with_no_reporting_seat_never_reaches_the_spawn(self):
        """Zero reports is a terminal stop for every judge. Printing a spawn instruction over an
        empty run directory costs a session's turn to discover the same thing."""
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": {"verdict": "nonsense", "summary": "x", "findings": []}},
                                 {"body": {"verdict": "nonsense", "summary": "x", "findings": []}}],
            harness.FAST_MODEL: [{"body": {"verdict": "nonsense", "summary": "x", "findings": []}},
                                 {"body": {"verdict": "nonsense", "summary": "x", "findings": []}}],
        })
        argv = ["--panel", self.workspace.panel, "--artifact", self.workspace.artifact,
                "--ref", self.workspace.reference, "--out", self.run_dir,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--tier", "standard", "--autonomous"]
        code, out, err = _capture(run_panel.main, argv)
        self.assertEqual(code, run_panel.EXIT_TERMINAL)
        self.assertNotIn("Spawn one subagent", out + err)

    def test_the_staging_directory_is_outside_the_run_directory(self):
        """The harness-leg rule: no mind is *given* a path into a directory holding others' work."""
        self.run_panel()
        self.assertTrue(os.path.isdir(os.path.dirname(self.staging)))
        self.assertNotIn(os.path.abspath(self.run_dir) + os.sep, os.path.abspath(self.staging))

    def test_reconcile_with_nothing_on_disk_says_who_to_spawn_and_exits_three(self):
        self.run_panel()
        code, _out, err = self.reconcile()
        self.assertEqual(code, reconcile.EXIT_PATCH)
        self.assertIn(judge_lib.HARNESS_JUDGE_AGENT, err)
        self.assertIn(self.staging, err)
        self.assertEqual(self.workspace.calls(), [], "a harness judge run never dispatches a call")
        self.assertFalse(self.wrote("reconciliation.json"))


class IngestTest(HarnessJudgeRunTestCase):
    """What comes back from the agent, and what is done with it."""

    def test_a_staged_patch_is_found_without_being_pointed_at(self):
        self.run_panel()
        self.write_staged_patch(patch())
        code, _out, err = self.reconcile()
        self.assertEqual(code, 0, err)
        self.assertTrue(self.wrote("reconciliation.json"))
        self.assertEqual(self.read("reconciliation.json")["reconciler"],
                         judge_lib.HARNESS_JUDGE_AUTHOR)

    def test_the_whole_seam_end_to_end_with_the_ids_the_worksheet_posed(self):
        """The one test that walks the real sequence: panel → worksheet → patch over *its* ids →
        reconciliation. Every other test in this file hard-codes `P-1`, which would pass over a
        worksheet that was never written."""
        self.run_panel()
        document, request = self.patch_from_worksheet()
        self.write_staged_patch(document)

        code, _out, err = self.reconcile()
        self.assertEqual(code, 0, err)
        reconciliation = self.read("reconciliation.json")
        self.assertEqual(len(reconciliation["clusters"]), len(request["clusters"]),
                         "every cluster the worksheet posed is answered in the product")
        for cluster in reconciliation["clusters"]:
            with self.subTest(cluster=cluster["id"]):
                self.assertEqual(cluster["disposition"], "flag-for-human")

    def test_the_printed_command_ingests_it_too(self):
        self.run_panel()
        self.write_staged_patch(patch())
        code, _out, err = self.reconcile(extra=["--judgment", self.staging])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("reconciliation.json")["judgment"]["author"],
                         judge_lib.HARNESS_JUDGE_AUTHOR)

    def test_the_manifest_records_the_judgment_as_a_harness_call_that_cost_nothing(self):
        self.run_panel()
        self.write_staged_patch(patch())
        self.reconcile()
        record = self.manifest()["judge"]
        self.assertEqual(record["reviewer_id"], judge_lib.HARNESS_JUDGE_AGENT)
        self.assertEqual(record["leg"], "harness")
        self.assertEqual(record["family"], "claude")
        self.assertEqual(record["status"], "ok")
        self.assertIsNone(record["cost_usd"], "the harness judge runs on the subscription")

    def test_the_reconciliation_names_it_as_the_judgment_supplier(self):
        self.run_panel()
        self.write_staged_patch(patch())
        self.reconcile()
        with open(os.path.join(self.run_dir, "reconciliation.md"), "r", encoding="utf-8") as handle:
            rendered = handle.read()
        self.assertIn("judgment supplied by `harness-judge`", rendered)

    def test_a_run_total_is_not_disturbed_by_a_free_judgment(self):
        self.run_panel()
        before = self.manifest()["cost_usd_total"]
        self.write_staged_patch(patch())
        self.reconcile()
        self.assertEqual(self.manifest()["cost_usd_total"], before)


class AuthorEnforcementTest(HarnessJudgeRunTestCase):
    """The same on-disk enforcement `synthesis` gets, and for the same reason."""

    def test_a_staged_patch_claiming_host_is_refused(self):
        self.run_panel()
        self.write_staged_patch(patch(author="host", dispositions=[{
            "cluster": "P-1", "disposition": "fix-now",
            "disposition_reason": "A host wrote this, so the fork rule does not bind."}]))
        code, _out, err = self.reconcile()
        self.assertEqual(code, reconcile.EXIT_PATCH)
        self.assertIn("says `author: 'host'`", err)
        self.assertIn("harness-judge", err)
        self.assertFalse(self.wrote("reconciliation.json"))

    def test_pointing_at_the_staging_path_explicitly_does_not_excuse_it(self):
        """`--judgment <path>` normally means an operator vouching for a file. Not this path: the
        agent writes there by design, so it is the ordinary ingest and not a human's assertion."""
        self.run_panel()
        self.write_staged_patch(patch(author="host"))
        code, _out, err = self.reconcile(extra=["--judgment", self.staging])
        self.assertEqual(code, reconcile.EXIT_PATCH)
        self.assertIn("says `author: 'host'`", err)

    def test_a_patch_claiming_synthesis_is_refused_on_a_harness_run(self):
        """Not interchangeable: the author names which mind actually judged, for the audit trail."""
        self.run_panel()
        self.write_staged_patch(patch(author="synthesis"))
        code, _out, err = self.reconcile()
        self.assertEqual(code, reconcile.EXIT_PATCH)
        self.assertIn("harness-judge", err)

    def test_reconciler_host_is_the_way_to_say_a_human_wrote_it(self):
        self.run_panel()
        self.write_staged_patch(patch(author="host", dispositions=[{
            "cluster": "P-1", "disposition": "fix-now",
            "disposition_reason": "The owner settled this fork on 2026-09-19."}]))
        code, _out, err = self.reconcile(extra=["--judgment", self.staging, "--reconciler", "host"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("reconciliation.json")["reconciler"], "host")


class MechanicalFloorTest(HarnessJudgeRunTestCase):
    """A frontier model with file tools is more capable than the persona and no more entitled."""

    def test_it_may_not_dispose_an_all_judgment_call_cluster_anything_but_flag_for_human(self):
        self.run_panel()
        self.write_staged_patch(patch(dispositions=[{
            "cluster": "P-1", "disposition": "fix-now",
            "disposition_reason": "I am a frontier model and I am confident."}]))
        code, _out, err = self.reconcile()
        self.assertEqual(code, reconcile.EXIT_PATCH)
        self.assertIn("an unattended judge always flags a judgment call", err)
        self.assertFalse(self.wrote("reconciliation.json"))

    def test_it_may_not_state_a_ruling(self):
        self.run_panel()
        self.write_staged_patch(patch(rulings=[{
            "cluster": "P-1", "text": "Carry the family axis under the tier map.",
            "owner_to_confirm": True}]))
        code, _out, err = self.reconcile()
        self.assertEqual(code, reconcile.EXIT_PATCH)
        self.assertIn("rulings", err)
        self.assertFalse(self.wrote("reconciliation.json"))

    def test_the_floor_is_stated_in_the_agent_body_as_well_as_enforced(self):
        """A body whose instructions and whose validator disagree is worse than one that repeats."""
        with open(os.path.join(SKILL_DIR, "agents", "judge.md"), "r", encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("always `flag-for-human`", body)
        self.assertIn("You may not emit `rulings`", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""The auto-apply gate: five conditions, all-or-nothing application, and the audit log.

No network and no paid call — `apply_fixes.py` reads `reconciliation.json` and `manifest.json` and
writes one file, so a run directory built by hand is exactly the input it takes in production.

Each test starts from a run whose single cluster passes all five conditions and then breaks one
thing, because that is the claim worth pinning: the gate is a conjunction, and anything that is not
applied is reported with the reason rather than dropped.

    python3 scripts/tests/test_apply_fixes.py
"""

import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import apply_fixes  # noqa: E402
from lib import runs as runs_lib  # noqa: E402

ARTIFACT = """# A spec

## Budget

The digest cap is 2000 characters.

## Tiers

There are three tiers: frontier, standard, fast.
"""

OLD_TEXT = "The digest cap is 2000 characters."
NEW_TEXT = "The digest cap is 2000 characters, enforced by `lib/report.py`."

SECOND_OLD = "There are three tiers: frontier, standard, fast."
SECOND_NEW = "There are three tiers: frontier, standard and fast."


def cluster(cluster_id="SF-1", old_text=OLD_TEXT, new_text=NEW_TEXT, **overrides):
    """One cluster that passes conditions 1 to 4. Overrides break exactly one of them."""
    record = {
        "id": cluster_id,
        "provisional_id": "P-1",
        "claim": "The digest cap is stated without saying what enforces it.",
        "location": "Budget",
        "quote": old_text,
        "members": [
            {"reviewer_id": "fidelity-openai", "finding_id": "F1", "severity": "should-fix",
             "confidence": "high", "cited": True, "collapsed_finding_ids": []},
            {"reviewer_id": "consistency-kimi", "finding_id": "F2", "severity": "should-fix",
             "confidence": "high", "cited": False, "collapsed_finding_ids": []},
        ],
        "n_reviewers": 2,
        "n_families": 2,
        "families": ["kimi", "openai"],
        "tier": "consensus",
        "match_key": "quote",
        "judgment": False,
        "split_reason": None,
        "singleton_label": None,
        "singleton_reason": None,
        "severity": "should-fix",
        "severity_spread": [{"reviewer_id": "fidelity-openai", "severity": "should-fix"},
                            {"reviewer_id": "consistency-kimi", "severity": "should-fix"}],
        "arbitration_reason": "members agree",
        "disposition": "fix-now",
        "disposition_reason": "Two families, one determinate fix.",
        "ruling": None,
        "contradicted_by": [],
        "canonical_edit": {
            "old_text": old_text,
            "new_text": new_text,
            "source": {"reviewer_id": "fidelity-openai", "finding_id": "F1"},
            "accepted_by": "host",
            "reason": "The wording says what the other seat asked for and nothing more.",
        },
        "edit_conflict": False,
        "tags": ["gap"],
    }
    record.update(overrides)
    return record


class GateTestCase(unittest.TestCase):
    """A run directory holding a manifest, a reconciliation and the working-tree artifact."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ensemble-review-apply-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.artifact = os.path.join(self.root, "spec.md")
        _write(self.artifact, ARTIFACT)
        self.run_dir = os.path.join(self.root, "reviews", "2026-09-18-1")
        os.makedirs(self.run_dir)
        self.revision = runs_lib.sha256_file(self.artifact)

    UNCHANGED = object()

    def build(self, clusters=None, families=("kimi", "openai"), revision=UNCHANGED):
        # A sentinel rather than None, because `revision=None` is the case under test: a run whose
        # manifest never recorded one.
        revision = self.revision if revision is self.UNCHANGED else revision
        _write_json(os.path.join(self.run_dir, "manifest.json"), {
            "run_id": "2026-09-18-1",
            "panel": "spec-review",
            "artifact": self.artifact,
            "artifact_revision": revision,
            "seats": [],
        })
        _write_json(os.path.join(self.run_dir, "reconciliation.json"), {
            "schema_version": "2",
            "run_id": "2026-09-18-1",
            "artifact": {"path": self.artifact, "revision": revision},
            "panel": "spec-review",
            "reconciler": "host",
            "judgment": {"path": "judgment.json", "author": "host", "generated_at": None},
            "generated_at": "2026-09-19T09:00:00-04:00",
            "seats_expected": ["fidelity-openai", "consistency-kimi"],
            "seats_reporting": ["consistency-kimi", "fidelity-openai"],
            "missing_seats": [],
            "anchor_drops": [],
            "families_reporting": list(families),
            "clusters": list(clusters if clusters is not None else [cluster()]),
            "disagreements": {"contradictions": [], "severity_spreads": [], "altitude_splits": []},
            "verdict": "fix-then-ship",
            "method_caveat": "Two families reported.",
            "counts": {"clusters": 1, "by_severity": {}, "by_tier": {}, "by_disposition": {}},
        })

    def run_apply(self, extra=None):
        argv = ["--run-dir", self.run_dir, "--i-authored-this"] + list(extra or [])
        return _capture(apply_fixes.main, argv)

    def text(self):
        with open(self.artifact, "r", encoding="utf-8") as handle:
            return handle.read()

    def log(self):
        path = os.path.join(self.run_dir, "applied.md")
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()


class SuccessTest(GateTestCase):

    def test_a_cluster_that_passes_every_condition_is_applied_once(self):
        self.build()
        code, out, err = self.run_apply()
        self.assertEqual(code, 0, err)
        self.assertIn(NEW_TEXT, self.text())
        self.assertNotIn(OLD_TEXT + "\n", self.text())
        self.assertEqual(self.text().count(NEW_TEXT), 1, "written once, not twice")
        self.assertIn("applied 1 edit", out)

    def test_the_write_is_atomic_and_leaves_no_temp_file(self):
        self.build()
        self.run_apply()
        self.assertEqual([name for name in os.listdir(self.root) if name.endswith(".tmp")], [])

    def test_the_artifacts_mode_survives_the_write(self):
        """`os.replace` swaps the directory entry, so the mode has to be carried across by hand."""
        self.build()
        os.chmod(self.artifact, 0o640)
        self.run_apply()
        self.assertEqual(stat.S_IMODE(os.stat(self.artifact).st_mode), 0o640)

    def test_the_audit_log_traces_the_edit_back_to_the_seat_that_wrote_it(self):
        self.build()
        self.run_apply()
        log = self.log()
        self.assertIn("SF-1", log)
        self.assertIn("fidelity-openai", log, "the canonical edit's source reviewer")
        self.assertIn("consistency-kimi", log, "the other reviewer behind the defect")
        self.assertIn("accepted by `host`", log)
        self.assertIn("consensus", log)
        self.assertIn("- " + OLD_TEXT, log)
        self.assertIn("+ " + NEW_TEXT, log)

    def test_two_non_overlapping_edits_both_land(self):
        self.build(clusters=[cluster(), cluster("SF-2", SECOND_OLD, SECOND_NEW)])
        code, _out, err = self.run_apply()
        self.assertEqual(code, 0, err)
        self.assertIn(NEW_TEXT, self.text())
        self.assertIn(SECOND_NEW, self.text())

    def test_a_dry_run_reports_and_writes_nothing(self):
        self.build()
        code, out, err = _capture(apply_fixes.main, ["--run-dir", self.run_dir, "--dry-run"])
        self.assertEqual(code, 0, err)
        self.assertIn("dry run", out)
        self.assertEqual(self.text(), ARTIFACT)
        self.assertIsNone(self.log(), "a dry run does not even write the audit log")


class FiveConditionsTest(GateTestCase):
    """One cluster, one condition broken at a time. Nothing is applied and the reason is named."""

    def _refused(self, expected, clusters=None, code=0):
        self.build(clusters=clusters)
        before = self.text()
        actual, out, err = self.run_apply()
        self.assertEqual(actual, code, err)
        self.assertEqual(self.text(), before, "the artifact was not touched")
        self.assertIn(expected, out + err)
        return out + err

    def test_condition_one_a_cluster_not_disposed_fix_now(self):
        self._refused("condition 1", [cluster(disposition="flag-for-human")])

    def test_condition_two_a_contradicted_cluster(self):
        self._refused("condition 2", [cluster(contradicted_by=[
            {"reviewer_id": "adversarial-glm", "finding_id": "F9"}])])

    def test_condition_two_an_edit_conflict(self):
        self._refused("competing replacements", [cluster(edit_conflict=True)])

    def test_condition_three_no_canonical_edit_was_accepted(self):
        self._refused("condition 3", [cluster(canonical_edit=None)])

    def test_condition_three_a_canonical_edit_nobody_accepted(self):
        """The belt to `reconcile_core`'s braces: a canonical edit with no `accepted_by` never applies.

        `reconcile_core._canonical_edit` already returns None when the patch did not accept the edit,
        and always fills `accepted_by` when it did, so this shape cannot come out of `reconcile.py`.
        It can come out of a hand-edited `reconciliation.json`, and this gate reads that file and
        nothing else — so the acceptance mark is checked here rather than assumed upstream.
        """
        edit = dict(cluster()["canonical_edit"], accepted_by="")
        self._refused("nobody who accepted it", [cluster(canonical_edit=edit)])

    def test_condition_three_an_old_text_that_occurs_twice_aborts_the_set(self):
        _write(self.artifact, ARTIFACT + "\n" + OLD_TEXT + "\n")
        self.revision = runs_lib.sha256_file(self.artifact)
        self._refused("occurs 2 times", code=3)

    def test_condition_three_an_old_text_that_is_not_in_the_artifact_aborts_the_set(self):
        self._refused("does not occur in the artifact",
                      [cluster(old_text="Text nobody ever wrote.")], code=3)

    def test_condition_four_a_single_family_cluster(self):
        self._refused("condition 4", [cluster(n_families=1, families=["openai"], tier="singleton")])

    def test_condition_four_one_family_on_a_cross_family_tier(self):
        """The evidence half of condition 4, with the enum half deliberately satisfied.

        `tier: consensus` with `n_families: 1` cannot be minted by `reconcile_core` — the tier is
        computed from the family count — so this is the case where the two wordings of condition 4
        disagree in the direction the enum alone would wave through. The count is what the gate
        turns on, and it refuses.
        """
        self._refused("1 family", [cluster(n_families=1, families=["openai"], tier="consensus")])

    def test_condition_four_a_same_family_tier_with_two_families_recorded(self):
        """Both wordings are checked, so a tier table that moved cannot slip past the enum half."""
        self._refused("same-family tier", [cluster(tier="same-family")])

    def test_condition_four_a_tier_neither_wording_knows(self):
        text = self._refused("the gate and the tier table disagree", [cluster(tier="plurality")])
        self.assertIn("plurality", text)

    def test_condition_five_a_revision_mismatch_aborts_the_set(self):
        self.build()
        _write(self.artifact, ARTIFACT + "\nOne more sentence.\n")
        code, out, err = self.run_apply()
        self.assertEqual(code, 3)
        self.assertIn("condition 5", err)
        self.assertNotIn(NEW_TEXT, self.text())

    def test_condition_five_a_manifest_and_reconciliation_that_disagree_abort_the_set(self):
        """Two records of one revision, and the gate believes neither when they differ.

        `apply_fixes.py` reads both files, so it can see a disagreement no single-file reader could.
        A run whose two records of what the panel read do not match is a run whose provenance is
        broken; picking one and applying against it would be guessing which half is the lie.
        """
        self.build()
        _write_json(os.path.join(self.run_dir, "manifest.json"), {
            "run_id": "2026-09-18-1",
            "panel": "spec-review",
            "artifact": self.artifact,
            "artifact_revision": "0" * 64,
            "seats": [],
        })
        code, _out, err = self.run_apply()
        self.assertEqual(code, 3)
        self.assertIn("disagree about which revision", err)
        self.assertIn("0" * 64, err)
        self.assertIn(self.revision, err, "both recorded revisions are named")
        self.assertEqual(self.text(), ARTIFACT, "the artifact was not touched")

    def test_condition_five_an_unpinned_run_is_not_auto_appliable(self):
        self.build(revision=None)   # a manifest written before revisions existed
        code, _out, err = self.run_apply()
        self.assertEqual(code, 3)
        self.assertIn("no `artifact_revision`", err)
        self.assertEqual(self.text(), ARTIFACT)


class AllOrNothingTest(GateTestCase):

    def test_an_overlapping_pair_applies_neither(self):
        overlapping = cluster("SF-2", old_text="digest cap is 2000", new_text="digest cap is 2500")
        self.build(clusters=[cluster(), overlapping])
        code, _out, err = self.run_apply()
        self.assertEqual(code, 3)
        self.assertIn("overlapping spans", err)
        self.assertEqual(self.text(), ARTIFACT)

    def test_one_unresolvable_anchor_blocks_the_other_candidates(self):
        self.build(clusters=[cluster(), cluster("SF-2", "Text nobody ever wrote.", "x")])
        code, _out, err = self.run_apply()
        self.assertEqual(code, 3)
        self.assertEqual(self.text(), ARTIFACT, "the resolvable edit is not applied either")
        self.assertIn("all-or-nothing", err)

    def test_the_abort_is_logged_with_every_candidate_marked_unapplied(self):
        self.build(clusters=[cluster(), cluster("SF-2", "Text nobody ever wrote.", "x")])
        self.run_apply()
        log = self.log()
        self.assertIn("aborted", log)
        self.assertIn("SF-1", log)

    def test_a_cluster_that_fails_the_gate_does_not_block_one_that_passes(self):
        """Failing the gate is not failing an anchor: the first is reported, the second is applied."""
        self.build(clusters=[cluster(), cluster("SF-2", SECOND_OLD, SECOND_NEW,
                                                disposition="defer")])
        code, out, err = self.run_apply()
        self.assertEqual(code, 0, err)
        self.assertIn(NEW_TEXT, self.text())
        self.assertNotIn(SECOND_NEW, self.text())
        self.assertIn("SF-2 not applied", out)


class RefusalTest(GateTestCase):

    def test_without_the_authorship_assertion_nothing_is_read_or_written(self):
        self.build()
        code, _out, err = _capture(apply_fixes.main, ["--run-dir", self.run_dir])
        self.assertEqual(code, 1)
        self.assertIn("--i-authored-this", err)
        self.assertIn("instruct its own reviewers", err,
                      "the refusal says why, not just that")
        self.assertEqual(self.text(), ARTIFACT)
        self.assertIsNone(self.log())

    def test_a_single_family_run_is_a_no_op_and_says_so(self):
        self.build(families=("openai",))
        code, out, err = self.run_apply()
        self.assertEqual(code, 0, err)
        self.assertIn("no-op", out)
        self.assertIn("condition 4", out.lower())
        self.assertEqual(self.text(), ARTIFACT)
        self.assertIsNone(self.log(), "a no-op run writes no audit log, because nothing was weighed")

    def test_it_refuses_to_edit_the_runs_own_pinned_copy(self):
        """`inputs/` is the evidence of what the seats read, and it is read-only on purpose."""
        self.build()
        inputs = os.path.join(self.run_dir, "inputs")
        os.makedirs(inputs)
        pinned = os.path.join(inputs, "spec.md")
        _write(pinned, ARTIFACT)
        code, _out, err = self.run_apply(extra=["--artifact", pinned])
        self.assertEqual(code, 1)
        self.assertIn("inside the run directory", err)

    def test_a_run_directory_with_no_reconciliation_is_a_usage_error(self):
        code, _out, err = self.run_apply()
        self.assertEqual(code, 1)
        self.assertIn("reconciliation.json", err)


class NothingEligibleTest(GateTestCase):

    def test_a_reconciliation_with_no_appliable_cluster_exits_zero_and_logs_why(self):
        """Nothing to do is an outcome, not a failure — and the log still says what was weighed."""
        self.build(clusters=[cluster(disposition="flag-for-human")])
        code, out, err = self.run_apply()
        self.assertEqual(code, 0, err)
        self.assertIn("nothing was applied", out)
        self.assertIn("SF-1", self.log())
        self.assertIn("condition 1", self.log())


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _write_json(path, data):
    _write(path, json.dumps(data, indent=2) + "\n")


def _capture(entry_point, argv):
    out_stream, err_stream = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_stream, err_stream
    try:
        code = entry_point(argv)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return code, out_stream.getvalue(), err_stream.getvalue()


if __name__ == "__main__":
    unittest.main(verbosity=2)

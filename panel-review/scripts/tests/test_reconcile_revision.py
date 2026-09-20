#!/usr/bin/env python3
"""`reconcile.py` against the artifact's revision: refuse a mismatch, warn on an unpinned run.

`reconciliation.json` is a function of the artifact's current bytes — the anchor check verifies every
`quote` and every `literal_edit.old_text` against them — so reconciling an old run against a document
that has moved on since checks anchors no reviewer ever saw. This is the check that stops it.

`artifact_revision: null` is **unpinned, not mismatched**. Every report written before revisions
existed reads that way, the replay fixture included, so a null warns and proceeds rather than
retiring the acceptance control.

The fixtures are the frozen 2026-09-18 run 1 directory, copied to a temp directory first: nothing
under `reviews/` is ever written by a test.

    python3 scripts/tests/test_reconcile_revision.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import reconcile  # noqa: E402
from lib import runs as runs_lib  # noqa: E402

REPO = os.path.dirname(os.path.dirname(SKILL_DIR))
RUN_DIR = os.environ.get("PANEL_REVIEW_REPLAY_DIR", os.path.join(
    REPO, ".agents", "subprojects", "panel-review", "reviews", "v1-spec", "2026-09-18-1"))
ARTIFACT = os.environ.get("PANEL_REVIEW_REPLAY_ARTIFACT", os.path.join(
    REPO, ".agents", "subprojects", "panel-review", "design", "v1-spec.md"))


def _require_fixtures():
    for path, what in ((RUN_DIR, "the run 1 fixture directory"), (ARTIFACT, "the pinned artifact")):
        if not os.path.exists(path):
            raise AssertionError("cannot run: {0} is not at {1}".format(what, path))


class RevisionCheckTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _require_fixtures()
        cls.real_hash = runs_lib.sha256_file(ARTIFACT)

    def copy_run(self, artifact_revision=..., artifact_path=..., with_inputs=False):
        """A throwaway copy of the frozen run, with its manifest adjusted."""
        root = tempfile.mkdtemp(prefix="panel-review-revision-")
        self.addCleanup(shutil.rmtree, root, True)
        run_dir = os.path.join(root, "run")
        shutil.copytree(RUN_DIR, run_dir)
        os.remove(os.path.join(run_dir, "reconciliation.md"))

        manifest_path = os.path.join(run_dir, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        if artifact_revision is not ...:
            manifest["artifact_revision"] = artifact_revision
        if artifact_path is not ...:
            manifest["artifact"] = artifact_path
        if with_inputs:
            os.makedirs(os.path.join(run_dir, "inputs"))
            shutil.copyfile(ARTIFACT, os.path.join(run_dir, "inputs", "v1-spec.md"))
            manifest["artifact_input"] = os.path.join("inputs", "v1-spec.md")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        return run_dir

    def reconcile(self, run_dir, extra=None):
        argv = ["--run-dir", run_dir, "--judgment", os.path.join(run_dir, "judgment.json")] + list(extra or [])
        stderr, saved = io.StringIO(), sys.stderr
        out, saved_out = io.StringIO(), sys.stdout
        sys.stderr, sys.stdout = stderr, out
        try:
            code = reconcile.main(argv)
        finally:
            sys.stderr, sys.stdout = saved, saved_out
        return code, stderr.getvalue()

    def test_a_hash_mismatch_refuses_and_names_both_hashes(self):
        run_dir = self.copy_run(artifact_revision="0" * 64)
        before = sorted(os.listdir(run_dir))
        code, err = self.reconcile(run_dir, ["--artifact", ARTIFACT])
        self.assertEqual(code, 1, err)
        self.assertIn("0" * 64, err, "the manifest's revision is named")
        self.assertIn(self.real_hash, err, "the file's actual hash is named")
        self.assertEqual(sorted(os.listdir(run_dir)), before,
                         "nothing may be written when the artifact is not the one the seats read")

    def test_a_matching_hash_reconciles(self):
        run_dir = self.copy_run(artifact_revision=self.real_hash)
        code, err = self.reconcile(run_dir, ["--artifact", ARTIFACT])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "reconciliation.json")))

    def test_an_unpinned_run_warns_and_proceeds(self):
        """The frozen run 1 manifest records no revision, and the replay control depends on it."""
        run_dir = self.copy_run()
        with open(os.path.join(run_dir, "manifest.json"), "r", encoding="utf-8") as handle:
            self.assertIsNone(json.load(handle).get("artifact_revision"),
                              "the frozen fixture is unpinned; this test exists because of that")
        code, err = self.reconcile(run_dir, ["--artifact", ARTIFACT])
        self.assertEqual(code, 0, err)
        self.assertIn("unpinned", err)
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "reconciliation.json")))

    def test_an_explicit_null_revision_is_treated_as_unpinned_too(self):
        run_dir = self.copy_run(artifact_revision=None)
        code, err = self.reconcile(run_dir, ["--artifact", ARTIFACT])
        self.assertEqual(code, 0, err)
        self.assertIn("unpinned", err)

    def test_the_inputs_copy_is_preferred_over_a_working_tree_path_that_moved(self):
        """A moved working-tree artifact no longer matters: the run holds its own pinned copy."""
        run_dir = self.copy_run(artifact_revision=self.real_hash,
                                artifact_path="somewhere/that/no/longer/exists/v1-spec.md",
                                with_inputs=True)
        code, err = self.reconcile(run_dir)
        self.assertEqual(code, 0, err)
        with open(os.path.join(run_dir, "reconciliation.json"), "r", encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual(document["anchor_drops"], [],
                         "the anchors were checked against the inputs/ copy, not against nothing")

    def test_render_only_runs_without_the_check(self):
        run_dir = self.copy_run(artifact_revision=self.real_hash)
        self.assertEqual(self.reconcile(run_dir, ["--artifact", ARTIFACT])[0], 0)
        os.remove(os.path.join(run_dir, "reconciliation.md"))

        manifest_path = os.path.join(run_dir, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["artifact_revision"] = "f" * 64
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)

        stderr, saved = io.StringIO(), sys.stderr
        out, saved_out = io.StringIO(), sys.stdout
        sys.stderr, sys.stdout = stderr, out
        try:
            code = reconcile.main(["--run-dir", run_dir, "--render-only"])
        finally:
            sys.stderr, sys.stdout = saved, saved_out
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertTrue(os.path.isfile(os.path.join(run_dir, "reconciliation.md")),
                        "--render-only re-renders a document whose anchors were checked when it was written")


if __name__ == "__main__":
    unittest.main(verbosity=2)

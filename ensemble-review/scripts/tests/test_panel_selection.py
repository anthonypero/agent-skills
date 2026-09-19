#!/usr/bin/env python3
"""Choosing the panel: the inference heuristic, the references rule, and the two refusals.

No network and no paid call. The unit half runs against the real shipped templates through the real
cascade; the end-to-end half runs a whole panel through the scripted connector with no `--panel` at
all, which is the path an operator who just names an artifact actually takes.

The rules under test, from the spec's knobs table and its failure table:

- With no `--panel`, infer from the artifact's kind if a cheap heuristic exists, else `spec-review`.
- **A run with no references never infers a template whose `requires_references` is true.** It lands
  on `design-decision` and the substitution is recorded — in the manifest, on the console, and in the
  reconciliation's method caveat.
- An **explicitly requested** fidelity-bearing seat with no references is a composition error, exit 1.
  Inference bends; a named panel does not, because an operator who named it should hear why.
- `code-review` ships deferred and refuses with exit 1, naming `/code-review`.

    python3 scripts/tests/test_panel_selection.py
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
import run_panel  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import reconcile_core as core  # noqa: E402


def cited_report():
    """A report a `fidelity` seat can pass validation with: that lens cites on every finding."""
    finding = dict(harness.valid_report()["findings"][0])
    finding["citation"] = {
        "reference": "reference.md",
        "location": "the source of truth",
        "quote": "The source of truth says the config shape must carry a family dimension.",
    }
    return harness.valid_report(findings=[finding])


def package_paths():
    """A cascade whose workspace root does not exist, so every file comes from the package."""
    return paths_lib.Paths(workspace=os.path.join(SKILL_DIR, "scripts", "tests"))


# --- the heuristic -------------------------------------------------------------------------------

class HeuristicTest(unittest.TestCase):
    """The artifact's filename and nothing else. Nothing reads the document."""

    CASES = (
        ("design/v3-spec.md", "spec-review"),
        ("pm/technical-requirements.md", "spec-review"),
        ("plan.md", "spec-review"),
        (".agents/ideas/model-landscape-research.md", "research-report"),
        ("2026-09-18-findings.md", "research-report"),
        ("reports/quarterly-report.md", "research-report"),
        ("adr-0007-storage.md", "design-decision"),
        ("docs/rfc-caching.md", "design-decision"),
        ("the-connector-decision.md", "design-decision"),
    )

    def test_the_filename_decides(self):
        for path, expected in self.CASES:
            with self.subTest(artifact=path):
                self.assertEqual(run_panel.panel_from_artifact(path), expected)

    def test_a_name_with_no_marker_lands_on_the_default(self):
        self.assertEqual(run_panel.panel_from_artifact("/tmp/untitled"), run_panel.DEFAULT_PANEL)

    def test_the_heuristic_is_case_and_separator_insensitive(self):
        self.assertEqual(run_panel.panel_from_artifact("Q3_Research_Report.MD"), "research-report")


# --- the references rule -------------------------------------------------------------------------

class InferenceTest(unittest.TestCase):

    def infer(self, artifact, refs):
        return run_panel.infer_panel(artifact, refs, package_paths())

    def test_with_references_the_heuristic_stands(self):
        panel, _path, record = self.infer("design/v3-spec.md", ["pm/prd.md"])
        self.assertEqual(panel["name"], "spec-review")
        self.assertEqual((record["heuristic"], record["resolved"], record["reason"]),
                         ("spec-review", "spec-review", None))

    def test_with_no_references_a_reference_bearing_template_is_never_inferred(self):
        panel, _path, record = self.infer("design/v3-spec.md", [])
        self.assertEqual(panel["name"], run_panel.FALLBACK_PANEL)
        self.assertIs(panel["requires_references"], False)
        self.assertEqual(record["heuristic"], "spec-review")
        self.assertEqual(record["resolved"], run_panel.FALLBACK_PANEL)
        self.assertIn("none were supplied", record["reason"])

    def test_the_research_template_falls_back_the_same_way(self):
        panel, _path, record = self.infer("model-landscape-research.md", [])
        self.assertEqual((record["heuristic"], record["resolved"]),
                         ("research-report", run_panel.FALLBACK_PANEL))
        self.assertEqual(panel["name"], run_panel.FALLBACK_PANEL)

    def test_a_reference_free_heuristic_is_left_alone(self):
        panel, _path, record = self.infer("adr-0007-storage.md", [])
        self.assertEqual(panel["name"], "design-decision")
        self.assertIsNone(record["reason"], "nothing was substituted, so nothing is recorded")

    def test_the_inference_record_says_no_panel_was_requested(self):
        """Null by construction on this path: `infer_panel` runs only when `--panel` named nothing."""
        _panel, _path, record = self.infer("design/v3-spec.md", [])
        self.assertIsNone(record["requested"])
        self.assertTrue(record["heuristic"], "something read the artifact's name, and the record says what")


class InferenceCaveatTest(unittest.TestCase):
    """A panel substitution reaches the reader the same way a seat substitution does."""

    def test_the_method_caveat_names_a_substituted_panel(self):
        manifest = {"seats": [], "panel_inference": {
            "requested": None, "heuristic": "spec-review", "resolved": "design-decision",
            "reason": "panel 'spec-review' was inferred and requires references; none were supplied"}}
        caveat = core.method_caveat("Four families ran.", manifest)
        self.assertIn("Four families ran.", caveat)
        self.assertIn("Panel substitution", caveat)
        self.assertIn("spec-review", caveat)
        self.assertIn("design-decision", caveat)

    def test_an_unsubstituted_inference_says_nothing(self):
        manifest = {"seats": [], "panel_inference": {
            "requested": None, "heuristic": "spec-review", "resolved": "spec-review", "reason": None}}
        self.assertEqual(core.method_caveat("Four families ran.", manifest), "Four families ran.")


# --- the two refusals ----------------------------------------------------------------------------

class PanelRunTestCase(unittest.TestCase):
    """A workspace whose own `panels/` shadow the shipped ones, so the seats are the test families."""

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        with open(self.workspace.config, "r", encoding="utf-8") as handle:
            self.workspace.override("config.json", json.load(handle))
        self.workspace.override("models.json", {"schema_version": "1", "models": harness.default_models()})
        self.workspace.override("panels/spec-review.json", {
            "name": "spec-review",
            "description": "The reference-hungry template, shadowed for this test.",
            "requires_references": True,
            "min_families": 2,
            "tier": "standard",
            "reconciler": "default",
            "auto_apply": False,
            "seats": [{"lens": "fidelity", "family": "kimi"}, {"lens": "adversarial", "family": "xai"}],
        })
        self.workspace.override("panels/design-decision.json", {
            "name": "design-decision",
            "description": "The reference-free fallback, shadowed for this test.",
            "requires_references": False,
            "min_families": 2,
            "tier": "standard",
            "reconciler": "default",
            "auto_apply": False,
            "seats": [{"lens": "consistency", "family": "kimi"}, {"lens": "adversarial", "family": "xai"}],
        })
        # The slow model holds the `fidelity` seat in the shadowed `spec-review`, and that lens is
        # refused a finding with no `citation`, so its scripted report carries one.
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": cited_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })

    def run_panel(self, extra=None, refs=(), out=None):
        argv = [
            "--artifact", self.workspace.artifact,
            "--out", out or self.workspace.path("reviews", "2026-09-18-1"),
            "--workspace", self.workspace.root,
            "--tier", "standard",
            "--autonomous",
        ]
        for ref in refs:
            argv += ["--ref", ref]
        argv += list(extra or [])
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


class DeferredPanelTest(PanelRunTestCase):

    def test_the_code_review_stub_refuses_and_names_the_slash_command(self):
        code, _out, err = self.run_panel(extra=["--panel", "code-review"], refs=[self.workspace.reference])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("/code-review", err)
        self.assertIn("deferred", err)

    def test_it_refuses_before_claiming_a_run_directory(self):
        out = self.workspace.path("reviews", "never-claimed")
        self.run_panel(extra=["--panel", "code-review"], refs=[self.workspace.reference], out=out)
        self.assertFalse(os.path.exists(out), "a refusal that leaves a run directory behind is a refusal that ran")


class ReferenceRequiredSeatTest(PanelRunTestCase):

    def test_a_named_panel_with_a_fidelity_seat_and_no_references_is_a_composition_error(self):
        code, _out, err = self.run_panel(extra=["--panel", "spec-review"])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("fidelity-kimi", err)
        self.assertIn("cites on every finding", err)
        self.assertIn(run_panel.FALLBACK_PANEL, err, "the message names the reference-free template")

    def test_the_same_panel_with_references_runs(self):
        code, _out, err = self.run_panel(extra=["--panel", "spec-review"], refs=[self.workspace.reference])
        self.assertEqual(code, 0, err)

    def test_source_credibility_is_refused_identically_to_fidelity(self):
        self.workspace.override("panels/sources.json", {
            "name": "sources",
            "description": "Two citing lenses and nothing to cite.",
            "requires_references": True,
            "min_families": 2,
            "tier": "standard",
            "reconciler": "default",
            "auto_apply": False,
            "seats": [{"lens": "source-credibility", "family": "kimi"}, {"lens": "adversarial", "family": "xai"}],
        })
        code, _out, err = self.run_panel(extra=["--panel", "sources"])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("source-credibility-kimi", err)

    def test_it_refuses_before_claiming_a_run_directory(self):
        """The counterpart of the deferred stub's refusal: a refused run leaves nothing on disk.

        `lib/runs.py` increments the sequence number on a collision, so a directory left behind here
        would silently move the operator's retry — the one with `--ref` supplied — into `<name>-2`.
        """
        out = self.workspace.path("reviews", "never-claimed")
        code, _stdout, err = self.run_panel(extra=["--panel", "spec-review"], out=out)
        self.assertEqual(code, run_panel.EXIT_COMPOSITION, err)
        self.assertFalse(os.path.exists(out), "a refusal that leaves a run directory behind is a refusal that ran")

    def test_no_paid_call_is_made(self):
        self.workspace.reset_calls()
        self.run_panel(extra=["--panel", "spec-review"])
        self.assertEqual(self.workspace.calls(), [], "refused before dispatch means refused before spending")


class InferredRunTest(PanelRunTestCase):
    """The whole script with no `--panel` at all."""

    def test_a_run_with_no_references_lands_on_the_reference_free_template(self):
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, err)
        manifest = self.manifest()
        self.assertEqual(manifest["panel"], run_panel.FALLBACK_PANEL)
        self.assertEqual(manifest["panel_inference"]["heuristic"], "spec-review")
        self.assertEqual(manifest["panel_inference"]["resolved"], run_panel.FALLBACK_PANEL)
        self.assertIn("panel inferred", out)
        for reviewer_id in ("consistency-kimi", "adversarial-xai"):
            self.assertTrue(os.path.isfile(os.path.join(
                self.workspace.path("reviews", "2026-09-18-1"), reviewer_id + ".json")), out)

    def test_the_recorded_substitution_reaches_the_method_caveat(self):
        self.run_panel()
        caveat = core.method_caveat("", self.manifest())
        self.assertIn("Panel substitution", caveat)
        self.assertIn(run_panel.FALLBACK_PANEL, caveat)

    def test_a_run_with_references_keeps_the_heuristics_panel(self):
        code, _out, err = self.run_panel(refs=[self.workspace.reference])
        self.assertEqual(code, 0, err)
        manifest = self.manifest()
        self.assertEqual(manifest["panel"], "spec-review")
        self.assertIsNone(manifest["panel_inference"]["reason"])

    def test_an_explicit_panel_is_recorded_as_requested_and_nothing_is_inferred(self):
        """`requested` is the field that tells the two paths apart, so it carries the name given."""
        code, out, err = self.run_panel(extra=["--panel", "design-decision"])
        self.assertEqual(code, 0, err)
        record = self.manifest()["panel_inference"]
        self.assertEqual(record["requested"], "design-decision")
        self.assertIsNone(record["heuristic"], "nothing read the artifact's name on this path")
        self.assertEqual(record["resolved"], "design-decision")
        self.assertIsNone(record["reason"])
        self.assertNotIn("panel inferred", out, "nothing was inferred, so nothing says it was")

    def test_an_inferred_run_records_no_requested_panel(self):
        self.run_panel()
        self.assertIsNone(self.manifest()["panel_inference"]["requested"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

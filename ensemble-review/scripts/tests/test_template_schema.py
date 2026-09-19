#!/usr/bin/env python3
"""The strict panel-template loader: an unrecognized key is a composition error, exit 1.

Owner ruling, 2026-09-19 (SF-12). The fork was between ignoring unknown keys — so a workspace
template can carry operator metadata and a future key degrades gracefully — and rejecting them, so
`min_famalies` is an error rather than a silently ignored typo that runs the panel at the default.
The owner chose strict, and the reasoning is about who writes these files: the author is the
orchestrating agent, not a human reading a diff, so a silent typo is the agent's mistake spending
the owner's money and the refusal is what pushes it straight back to the agent.

Three things are asserted here, and the third is the one that would catch a mistake in the ruling
itself: **every shipped template must load.** A vocabulary that refuses the catalog is a vocabulary
that is wrong.

    python3 scripts/tests/test_template_schema.py
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
from lib import panels as panels_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402

PANELS_DIR = os.path.join(SKILL_DIR, "templates", "panels")


def a_template(**overrides):
    """A minimal live template that loads, before the test breaks one key of it."""
    document = {
        "name": "under-test",
        "description": "A template written for this test.",
        "requires_references": False,
        "min_families": 2,
        "tier": "standard",
        "reconciler": "default",
        "auto_apply": False,
        "verify_web": False,
        "seats": [{"lens": "consistency", "family": "kimi"},
                  {"lens": "adversarial", "family": "xai"}],
    }
    document.update(overrides)
    return document


class VocabularyTest(unittest.TestCase):
    """`lib/panels.py` on its own: what it accepts and what it names when it refuses."""

    def test_a_clean_template_passes_and_is_returned_unchanged(self):
        template = a_template()
        self.assertIs(panels_lib.validate(template, "t.json"), template)

    def test_a_top_level_typo_is_refused_and_the_message_names_the_key_and_the_file(self):
        """`min_famalies` is the failure the ruling is about: silently ignored, it runs at 2."""
        with self.assertRaises(panels_lib.TemplateError) as raised:
            panels_lib.validate(a_template(min_famalies=4), "/tmp/typo.json")
        message = str(raised.exception)
        self.assertIn("min_famalies", message)
        self.assertIn("/tmp/typo.json", message)
        self.assertIn("min_families", message, "the message lists the key it was probably meant to be")

    def test_a_typo_inside_a_seat_is_refused_and_the_message_says_which_seat(self):
        template = a_template()
        template["seats"][1]["familly"] = "glm"
        with self.assertRaises(panels_lib.TemplateError) as raised:
            panels_lib.validate(template, "t.json")
        message = str(raised.exception)
        self.assertIn("familly", message)
        self.assertIn("seats[1]", message, "a panel with eight seats needs to know which one")

    def test_a_typo_inside_an_optional_seat_is_refused_too(self):
        """`optional_seats` is read by nothing today, which is exactly why a typo there would live."""
        template = a_template(optional_seats=[{"lens": "completeness", "family": "deepseek",
                                               "notes": "plural"}])
        with self.assertRaises(panels_lib.TemplateError) as raised:
            panels_lib.validate(template, "t.json")
        self.assertIn("optional_seats[0]", str(raised.exception))
        self.assertIn("notes", str(raised.exception))

    def test_a_typo_inside_the_synthesis_block_is_refused(self):
        template = a_template(synthesis={"tier": "frontier", "model": "openai/gpt-6-astra"})
        with self.assertRaises(panels_lib.TemplateError) as raised:
            panels_lib.validate(template, "t.json")
        self.assertIn("`synthesis` block", str(raised.exception))
        self.assertIn("model", str(raised.exception))

    def test_a_synthesis_block_naming_a_family_and_a_tier_is_fine(self):
        panels_lib.validate(a_template(synthesis={"tier": "frontier", "family": "deepseek"}), "t.json")

    def test_every_unknown_key_is_listed_rather_than_only_the_first(self):
        template = a_template(min_famalies=4, reconcilier="host")
        with self.assertRaises(panels_lib.TemplateError) as raised:
            panels_lib.validate(template, "t.json")
        self.assertIn("min_famalies", str(raised.exception))
        self.assertIn("reconcilier", str(raised.exception))

    def test_there_is_no_leniency_prefix(self):
        """The owner's words: operator metadata goes in `description`."""
        with self.assertRaises(panels_lib.TemplateError):
            panels_lib.validate(a_template(**{"x-owner": "anthony"}), "t.json")

    def test_a_deferred_stub_loads(self):
        panels_lib.validate({"name": "code-review", "description": "…", "deferred": True,
                             "routes_to": "/code-review", "seats": []}, "t.json")


class ShippedTemplatesTest(unittest.TestCase):
    """Every template the package ships must pass its own loader. Including the deferred stub."""

    def test_every_shipped_template_loads(self):
        paths = paths_lib.Paths(workspace=SKILL_DIR)
        for name in sorted(os.listdir(PANELS_DIR)):
            if not name.endswith(".json"):
                continue
            with self.subTest(panel=name):
                panel, path = run_panel.load_panel(name[:-5], paths)
                self.assertEqual(panel["name"], name[:-5])
                self.assertTrue(path.endswith(name))

    def test_the_vocabulary_covers_every_key_the_shipped_templates_actually_use(self):
        """The converse of the above, stated as data rather than as an exception that did not fire."""
        for name in sorted(os.listdir(PANELS_DIR)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(PANELS_DIR, name), "r", encoding="utf-8") as handle:
                panel = json.load(handle)
            with self.subTest(panel=name):
                self.assertEqual(panels_lib.unknown_keys(panel), [])


class ReconcileReadsTemplatesStrictlyTooTest(unittest.TestCase):
    """`reconcile.py` reads one field off the template — the judge's seat — and must not trust a
    template `run_panel.py` would refuse to dispatch. It warns and falls back rather than refusing:
    this path is only reached for a run with no recorded `judge_seat`, and a judgment that cannot
    be made is a worse outcome than a judge seated by the declaration-order default."""

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(workspace=self.workspace.root)

    def test_a_clean_workspace_template_is_read(self):
        self.workspace.override("panels/seated.json",
                                a_template(name="seated", synthesis={"family": "glm"}))
        panel = reconcile._panel_for(self.paths, {"panel": "seated"})
        self.assertEqual(panel["synthesis"], {"family": "glm"})

    def test_a_template_with_an_unknown_key_is_ignored_with_a_warning(self):
        self.workspace.override("panels/typo.json",
                                a_template(name="typo", synthesis={"family": "glm"},
                                           min_famalies=4))
        err = io.StringIO()
        saved = sys.stderr
        sys.stderr = err
        try:
            panel = reconcile._panel_for(self.paths, {"panel": "typo"})
        finally:
            sys.stderr = saved
        self.assertIsNone(panel, "a template the dispatch loader refuses must not steer the judge")
        self.assertIn("min_famalies", err.getvalue())
        self.assertIn("declaration-order default", err.getvalue())


class RefusalIsBeforeAnythingTest(unittest.TestCase):
    """The ruling's other half: the refusal lands before a seat is priced or a directory claimed."""

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)

    def run_panel(self, panel_path, out):
        argv = ["--panel", panel_path, "--artifact", self.workspace.artifact,
                "--out", out, "--workspace", self.workspace.root,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--tier", "standard", "--autonomous", "--reconcile", "off"]
        out_stream, err_stream = io.StringIO(), io.StringIO()
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_stream, err_stream
        try:
            code = run_panel.main(argv)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        return code, out_stream.getvalue(), err_stream.getvalue()

    def test_a_typo_refuses_with_exit_one_and_leaves_no_run_directory(self):
        path = os.path.join(self.workspace.root, "typo-panel.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(a_template(min_famalies=4), handle)
        out = self.workspace.path("reviews", "never-claimed")

        code, _stdout, err = self.run_panel(path, out)
        self.assertEqual(code, run_panel.EXIT_COMPOSITION, err)
        self.assertIn("min_famalies", err)
        self.assertIn(path, err)
        self.assertFalse(os.path.exists(out), "the refusal claimed a run directory")
        self.assertEqual(self.workspace.calls(), [], "the refusal came after a paid call")

    def test_the_same_template_without_the_typo_runs(self):
        path = os.path.join(self.workspace.root, "clean-panel.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(a_template(min_families=2), handle)
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, _stdout, err = self.run_panel(path, self.workspace.path("reviews", "2026-09-19-1"))
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""The two-root cascade: first hit wins for files, deep merge for the config, nothing writes home.

No network and no paid call. The read-only case copies the whole package to a temp directory and
chmods it, rather than touching the real one: a test that left the working tree unwritable would be a
worse defect than the one it is checking for.

    python3 scripts/tests/test_paths.py
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import harness  # noqa: E402
from lib import paths as paths_lib  # noqa: E402


class CascadeTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root)

    def test_the_package_answers_when_the_workspace_holds_nothing(self):
        found = self.paths.require("persona", "lens-consistency")
        self.assertEqual(found["root"], paths_lib.SKILL_DIR)
        self.assertTrue(found["packaged"])
        self.assertEqual(self.paths.roots_block()["workspace_overrides"], [])

    def test_a_workspace_file_replaces_the_packaged_one_whole(self):
        self.workspace.override("agents/lens-consistency.md", text="---\nname: x\n---\n\n# Role\n\nOverridden.\n")
        found = self.paths.require("persona", "lens-consistency")
        self.assertEqual(found["root"], self.workspace.workspace_root())
        with open(found["path"], "r", encoding="utf-8") as handle:
            self.assertIn("Overridden.", handle.read())
        self.assertEqual(self.paths.roots_block()["workspace_overrides"], ["persona:lens-consistency"])

    def test_the_search_order_is_workspace_then_package(self):
        self.assertEqual(self.paths.roots, [self.workspace.workspace_root(), paths_lib.SKILL_DIR])
        self.assertEqual(self.paths.roots_block()["search"], self.paths.roots)

    def test_a_file_in_neither_root_names_both_roots_and_every_candidate(self):
        with self.assertRaises(paths_lib.PathError) as caught:
            self.paths.require("panel", "no-such-panel")
        message = str(caught.exception)
        self.assertIn(self.workspace.workspace_root(), message)
        self.assertIn(paths_lib.SKILL_DIR, message)
        self.assertIn(os.path.join("panels", "no-such-panel.json"), message)
        self.assertIn(os.path.join("templates", "panels", "no-such-panel.json"), message)

    def test_a_workspace_may_mirror_the_package_layout_too(self):
        self.workspace.override(os.path.join("templates", "panels", "mirrored.json"), {"name": "mirrored", "seats": []})
        found = self.paths.require("panel", "mirrored")
        self.assertEqual(found["root"], self.workspace.workspace_root())


class ConfigMergeTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root)

    def test_a_one_cell_fragment_changes_one_cell_and_nothing_else(self):
        """The spec's own example: a project that wants one family pointed somewhere else adds one
        key, and does not copy a file it would then have to keep in step."""
        self.workspace.override("config.json", {"openrouter": {"tiers": {"standard": {"glm": "x"}}}})
        merged, _path = self.paths.config()
        entry = merged["openrouter"]
        self.assertEqual(entry["tiers"]["standard"]["glm"], "x")
        self.assertEqual(entry["tiers"]["standard"]["kimi"], "moonshotai/kimi-k3", "the sibling cell is untouched")
        self.assertEqual(entry["tiers"]["frontier"]["openai"], "openai/gpt-6-astra", "the sibling tier is untouched")
        self.assertEqual(entry["base_url"], "https://openrouter.ai/api/v1", "the connector's own keys survive")
        self.assertIn("effort", entry, "keys the fragment never mentions are still there")

    def test_the_merge_reaches_connector_tier_family_and_model_id_granularity(self):
        self.workspace.override("config.json", {
            "openrouter": {"default_tier": "fast", "effort": {"moonshotai/kimi-k3": "low"}},
            "azure": {"type": "azure_openai", "tiers": {"standard": {"openai": "azure/gpt"}}},
        })
        merged, _path = self.paths.config()
        self.assertEqual(merged["openrouter"]["default_tier"], "fast")
        self.assertEqual(merged["openrouter"]["effort"]["moonshotai/kimi-k3"], "low")
        self.assertEqual(merged["openrouter"]["effort"]["x-ai/grok-4.6"], "high", "the other efforts stand")
        self.assertEqual(merged["azure"]["tiers"]["standard"]["openai"], "azure/gpt", "a new connector merges in")

    def test_the_merge_is_recorded_as_a_fragment_not_as_a_replacement(self):
        self.workspace.override("config.json", {"openrouter": {"default_tier": "fast"}})
        self.paths.config()
        block = self.paths.roots_block()
        self.assertEqual(block["files"]["config"], paths_lib.SKILL_DIR)
        self.assertEqual(block["files"]["config-fragment"], self.workspace.workspace_root())
        self.assertEqual(block["workspace_overrides"], ["config-fragment"])

    def test_an_explicit_config_path_is_taken_as_given_and_never_merged(self):
        self.workspace.override("config.json", {"openrouter": {"default_tier": "fast"}})
        merged, path = self.paths.config(self.workspace.config)
        self.assertEqual(path, os.path.abspath(self.workspace.config))
        self.assertEqual(merged["openrouter"]["default_tier"], "standard")
        self.assertNotIn("config-fragment", self.paths.roots_block()["files"])


class DriverTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root)

    def test_the_packaged_driver_stays_a_package_module(self):
        self.assertEqual(self.paths.driver_ref("openai_compat"), "openai_compat")
        self.assertEqual(self.paths.roots_block()["files"]["driver:openai_compat"], paths_lib.SKILL_DIR)

    def test_a_workspace_driver_comes_back_as_a_file_path_and_imports_without_editing_the_package(self):
        """The egress control: a project binds a family to its own connector by dropping a file."""
        path = self.workspace.override("backends/azure_openai.py", text=(
            "def dispatch_detailed(*args, **kwargs):\n"
            "    return {'text': '{}', 'attempts': []}\n\n\n"
            "def dispatch(*args, **kwargs):\n"
            "    return '{}'\n"))
        reference = self.paths.driver_ref("azure_openai")
        self.assertEqual(reference, path)

        sys.path.insert(0, os.path.dirname(TESTS_DIR))
        from backends import load_driver
        module = load_driver(reference)
        self.assertTrue(hasattr(module, "dispatch_detailed"))
        self.assertNotIn("azure_openai.py", os.listdir(os.path.join(paths_lib.SKILL_DIR, "scripts", "backends")))

    def test_a_workspace_driver_shadows_a_packaged_one_of_the_same_name(self):
        path = self.workspace.override("backends/openai_compat.py", text="def dispatch(*a, **k):\n    return ''\n")
        self.assertEqual(self.paths.driver_ref("openai_compat"), path)

    def test_a_type_that_resolves_nowhere_is_a_composition_error(self):
        with self.assertRaises(paths_lib.PathError) as caught:
            self.paths.driver_ref("nowhere_at_all")
        message = str(caught.exception)
        self.assertIn("nowhere_at_all", message)
        self.assertIn(os.path.join(self.workspace.workspace_root(), "backends", "nowhere_at_all.py"), message)

    def test_a_type_that_already_names_a_file_is_an_operator_path(self):
        self.assertEqual(self.paths.driver_ref(harness.FAKE_BACKEND), harness.FAKE_BACKEND)


class ReadOnlyPackageTest(unittest.TestCase):
    """The skill package is read-only, per framework principle 12. A whole run proves it.

    The package is copied to a temp directory and every file and directory in the copy is stripped of
    its write bit, so anything the run tried to write into it would fail loudly. The config, the
    registry and the panel come from the workspace override root; the personas, the finding schema
    and the driver loader come from the read-only copy.
    """

    def setUp(self):
        self.workspace = harness.Workspace(models=harness.default_models())
        self.addCleanup(self.workspace.close)
        self.package = self._read_only_copy()

        self.workspace.override("config.json", {
            "openrouter": {
                "type": harness.FAKE_BACKEND,
                "base_url": "https://example.invalid/api/v1",
                "api_key_secret": None,
                "api_key_env": "ENSEMBLE_REVIEW_TEST_KEY",
                "default_tier": "standard",
                "tiers": {"standard": {"kimi": harness.SLOW_MODEL, "xai": harness.FAST_MODEL}},
                "effort": {harness.FAST_MODEL: "high"},
            }
        })
        self.workspace.override("models.json", {"schema_version": "1", "models": harness.default_models()})
        self.workspace.override("panels/read-only.json", {
            "name": "read-only",
            "requires_references": False,
            "tier": "standard",
            "seats": [{"lens": "consistency", "family": "kimi"}, {"lens": "adversarial", "family": "xai"}],
        })
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })

    def _read_only_copy(self):
        root = tempfile.mkdtemp(prefix="ensemble-review-readonly-")
        self.addCleanup(self._restore_and_remove, root)
        package = os.path.join(root, "ensemble-review")
        shutil.copytree(paths_lib.SKILL_DIR, package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
        for directory, _subdirs, files in os.walk(package, topdown=False):
            for name in files:
                os.chmod(os.path.join(directory, name), 0o444)
            os.chmod(directory, 0o555)
        return package

    def _restore_and_remove(self, root):
        for directory, _subdirs, files in os.walk(root):
            os.chmod(directory, 0o755)
            for name in files:
                os.chmod(os.path.join(directory, name), 0o644)
        shutil.rmtree(root, ignore_errors=True)

    def test_the_package_is_actually_unwritable(self):
        with self.assertRaises(OSError):
            with open(os.path.join(self.package, "templates", "canary.json"), "w") as handle:
                handle.write("{}")

    def test_a_whole_panel_runs_with_the_package_read_only(self):
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        result = subprocess.run(
            [sys.executable, os.path.join(self.package, "scripts", "run_panel.py"),
             "--panel", "read-only",
             "--workspace", self.workspace.root,
             "--artifact", self.workspace.artifact,
             "--out", run_dir,
             "--tier", "standard",
             "--autonomous"],
            capture_output=True, text=True, env=self.workspace.env(),
            cwd=self.workspace.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        for reviewer_id in ("consistency-kimi", "adversarial-xai"):
            self.assertTrue(os.path.isfile(os.path.join(run_dir, reviewer_id + ".json")), result.stdout)

        with open(os.path.join(run_dir, "manifest.json"), "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        roots = manifest["roots"]
        self.assertEqual(roots["search"][0], self.workspace.workspace_root())
        self.assertEqual(roots["files"]["config"], self.package, "the packaged config is the base layer")
        self.assertEqual(roots["files"]["config-fragment"], self.workspace.workspace_root(),
                         "and the workspace's config merged over it")
        self.assertEqual(roots["files"]["registry"], self.workspace.workspace_root(),
                         "the registry is a file, so first hit wins outright — no merge")
        self.assertEqual(roots["files"]["panel:read-only"], self.workspace.workspace_root())
        self.assertEqual(roots["files"]["persona:lens-consistency"], self.package,
                         "the personas came from the read-only package")
        self.assertEqual(roots["files"]["reference:finding-schema.md"], self.package,
                         "the finding schema is in every seat's system message, so it is in the record")

    def test_a_workspace_finding_schema_is_recorded_as_an_override(self):
        """Overriding it changes what every reviewer was asked for — the last thing `roots` may omit."""
        self.workspace.override("references/finding-schema.md", text="# A different finding schema\n")
        run_dir = self.workspace.path("reviews", "2026-09-18-1")
        result = subprocess.run(
            [sys.executable, os.path.join(self.package, "scripts", "run_panel.py"),
             "--panel", "read-only",
             "--workspace", self.workspace.root,
             "--artifact", self.workspace.artifact,
             "--out", run_dir,
             "--tier", "standard",
             "--autonomous"],
            capture_output=True, text=True, env=self.workspace.env(), cwd=self.workspace.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        with open(os.path.join(run_dir, "manifest.json"), "r", encoding="utf-8") as handle:
            roots = json.load(handle)["roots"]
        self.assertEqual(roots["files"]["reference:finding-schema.md"], self.workspace.workspace_root())
        self.assertIn("reference:finding-schema.md", roots["workspace_overrides"])

    def test_nothing_in_the_package_was_modified_by_the_run(self):
        before = self._fingerprint(self.package)
        self.test_a_whole_panel_runs_with_the_package_read_only()
        self.assertEqual(self._fingerprint(self.package), before)

    def _fingerprint(self, root):
        seen = {}
        for directory, subdirs, files in os.walk(root):
            subdirs[:] = [d for d in subdirs if d != "__pycache__"]
            for name in files:
                path = os.path.join(directory, name)
                info = os.stat(path)
                seen[os.path.relpath(path, root)] = (info.st_size, stat.S_IMODE(info.st_mode))
        return seen


if __name__ == "__main__":
    unittest.main(verbosity=2)

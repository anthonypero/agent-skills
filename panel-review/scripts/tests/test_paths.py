#!/usr/bin/env python3
"""The three-root cascade: first hit wins for files, deep merge for the config and the model files.

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
from lib import connectors as connectors_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402


class CascadeTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root, user=self.workspace.user_root())

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

    def test_the_search_order_is_project_then_user_then_package(self):
        self.assertEqual(self.paths.roots,
                         [self.workspace.workspace_root(), self.workspace.user_root(), paths_lib.SKILL_DIR])
        self.assertEqual(self.paths.roots_block()["search"], self.paths.roots)

    def test_the_user_tier_is_in_the_record_whether_or_not_it_exists(self):
        """A tier left out of `search` because this machine does not use it is a tier a reader of
        the run cannot rule out."""
        block = self.paths.roots_block()
        self.assertEqual(block["user_root"], self.workspace.user_root())
        self.assertFalse(block["user_root_present"])
        self.workspace.user_override("panels/from-user.json", {"name": "from-user", "seats": []})
        self.assertTrue(paths_lib.Paths(self.workspace.root,
                                        user=self.workspace.user_root()).roots_block()["user_root_present"])

    def test_the_user_tier_answers_when_the_project_holds_nothing(self):
        self.workspace.user_override("panels/from-user.json", {"name": "from-user", "seats": []})
        found = self.paths.require("panel", "from-user")
        self.assertEqual(found["root"], self.workspace.user_root())
        self.assertFalse(found["packaged"])

    def test_the_project_beats_the_user_tier_and_the_user_tier_beats_the_package(self):
        self.workspace.user_override("agents/lens-consistency.md", text="---\nname: u\n---\n\n# Role\n\nUser.\n")
        self.assertEqual(self.paths.require("persona", "lens-consistency")["root"],
                         self.workspace.user_root())
        self.workspace.override("agents/lens-consistency.md", text="---\nname: p\n---\n\n# Role\n\nProject.\n")
        fresh = paths_lib.Paths(self.workspace.root, user=self.workspace.user_root())
        self.assertEqual(fresh.require("persona", "lens-consistency")["root"],
                         self.workspace.workspace_root())

    def test_an_override_is_recorded_against_the_tier_that_supplied_it(self):
        """A project override travels with the repository; a per-owner one lives on one laptop.
        Folding them into one list would make a run that depends on this machine look reproducible."""
        self.workspace.user_override("panels/from-user.json", {"name": "from-user", "seats": []})
        self.workspace.override("panels/from-project.json", {"name": "from-project", "seats": []})
        self.paths.require("panel", "from-user")
        self.paths.require("panel", "from-project")
        block = self.paths.roots_block()
        self.assertEqual(block["user_overrides"], ["panel:from-user"])
        self.assertEqual(block["workspace_overrides"], ["panel:from-project"])

    def test_a_file_in_no_root_names_every_root_and_every_candidate(self):
        with self.assertRaises(paths_lib.PathError) as caught:
            self.paths.require("panel", "no-such-panel")
        message = str(caught.exception)
        self.assertIn(self.workspace.workspace_root(), message)
        self.assertIn(self.workspace.user_root(), message)
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
        self.paths = paths_lib.Paths(self.workspace.root, user=self.workspace.user_root())

    def test_a_one_key_fragment_changes_one_key_and_nothing_else(self):
        """The spec's own example: a project that wants a different default adds one key, and does
        not copy a file it would then have to keep in step."""
        self.workspace.override("config.json", {"default_tier": "fast"})
        merged, _path = self.paths.config()
        self.assertEqual(merged["default_tier"], "fast")
        self.assertEqual(merged["default_connector"], "openrouter", "the sibling key is untouched")
        self.assertEqual(merged["default_effort"], "standard")
        self.assertIn("family_order", merged, "keys the fragment never mentions are still there")

    def test_the_project_fragment_lands_over_the_user_fragment_over_the_package(self):
        self.workspace.user_override("config.json", {"default_tier": "standard", "default_effort": "light"})
        self.workspace.override("config.json", {"default_tier": "fast"})
        merged, _path = self.paths.config()
        self.assertEqual(merged["default_tier"], "fast", "the project wins where both speak")
        self.assertEqual(merged["default_effort"], "light", "and the user tier stands where it does not")
        self.assertEqual(merged["default_connector"], "openrouter", "the package is still the base")

    def test_the_merge_is_recorded_as_a_fragment_not_as_a_replacement(self):
        self.workspace.override("config.json", {"default_tier": "fast"})
        self.paths.config()
        block = self.paths.roots_block()
        self.assertEqual(block["files"]["config"], paths_lib.SKILL_DIR)
        self.assertEqual(block["files"]["config-fragment"], self.workspace.workspace_root())
        self.assertEqual(block["workspace_overrides"], ["config-fragment"])

    def test_an_explicit_config_path_is_taken_as_given_and_never_merged(self):
        self.workspace.override("config.json", {"default_tier": "fast"})
        merged, path = self.paths.config(self.workspace.config)
        self.assertEqual(path, os.path.abspath(self.workspace.config))
        self.assertEqual(merged["default_tier"], "standard")
        self.assertNotIn("config-fragment", self.paths.roots_block()["files"])


class ConnectorResolutionTest(unittest.TestCase):
    """Connector files resolve as **files**: first hit wins, whole file, at any tier."""

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root, user=self.workspace.user_root())

    def _connector(self, **fields):
        base = {"name": "acme", "type": "openai_compat", "base_url": "https://packaged.invalid/v1",
                "api_key_secret": None, "api_key_env": "PANEL_REVIEW_TEST_KEY",
                "billing": "free"}
        base.update(fields)
        return base

    def test_the_package_answers_when_no_outer_root_holds_one(self):
        entry, path = connectors_lib.load(self.paths, "openrouter")
        self.assertEqual(entry["name"], "openrouter")
        self.assertTrue(path.startswith(paths_lib.SKILL_DIR))
        self.assertEqual(self.paths.roots_block()["files"]["connector:openrouter"], paths_lib.SKILL_DIR)

    def test_the_user_tier_answers_over_the_package(self):
        self.workspace.user_override("connectors/acme.json", self._connector(base_url="https://user.invalid/v1"))
        entry, _path = connectors_lib.load(self.paths, "acme")
        self.assertEqual(entry["base_url"], "https://user.invalid/v1")
        self.assertEqual(entry["root"], self.workspace.user_root())
        self.assertIn("connector:acme", self.paths.roots_block()["user_overrides"])

    def test_the_project_answers_over_the_user_tier(self):
        self.workspace.user_override("connectors/acme.json", self._connector(base_url="https://user.invalid/v1"))
        self.workspace.override("connectors/acme.json", self._connector(base_url="https://project.invalid/v1"))
        entry, _path = connectors_lib.load(self.paths, "acme")
        self.assertEqual(entry["base_url"], "https://project.invalid/v1")
        self.assertEqual(entry["root"], self.workspace.workspace_root())
        self.assertIn("connector:acme", self.paths.roots_block()["workspace_overrides"])

    def test_a_connector_replaces_whole_and_is_never_merged(self):
        """Half an endpoint is not an endpoint: a file that set only `requires_approval` over a
        packaged `base_url` would read as a decision about that endpoint while pointing elsewhere."""
        self.workspace.override("connectors/openrouter.json", {
            "name": "openrouter", "type": "openai_compat", "base_url": "https://mine.invalid/v1",
            "api_key_secret": None, "api_key_env": "X", "billing": "free"})
        entry, _path = connectors_lib.load(self.paths, "openrouter")
        self.assertEqual(entry["base_url"], "https://mine.invalid/v1")
        self.assertNotIn("provider_routing", entry,
                         "the packaged file's routing block is gone, not merged under the new one")

    def test_a_file_that_calls_itself_something_else_is_refused(self):
        self.workspace.override("connectors/acme.json", self._connector(name="other"))
        with self.assertRaises(connectors_lib.ConnectorError) as caught:
            connectors_lib.load(self.paths, "acme")
        self.assertIn("'other'", str(caught.exception))

    def test_a_connector_with_no_billing_posture_is_refused(self):
        self.workspace.override("connectors/acme.json", self._connector(billing=None))
        with self.assertRaises(connectors_lib.ConnectorError) as caught:
            connectors_lib.load(self.paths, "acme")
        self.assertIn("billing", str(caught.exception))


class ModelFileMergeTest(unittest.TestCase):
    """Model files deep-merge across the three roots: package facts, outer-root choices."""

    MODEL = "moonshotai/kimi-k3"

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root, user=self.workspace.user_root())

    def test_a_user_copy_that_sets_one_choice_keeps_the_packages_price(self):
        """The whole reason model files merge rather than replace: a per-owner effort rung must not
        freeze the price from the day somebody wrote it."""
        self.workspace.user_override("models/moonshotai__kimi-k3.json", {"effort": {"standard": "max"}})
        registry = registry_lib.load(self.paths)
        entry = registry.get(self.MODEL)
        self.assertEqual(entry["effort"]["standard"], "max", "the user's choice wins")
        self.assertEqual(entry["effort"]["light"], "low", "the package's other levels survive")
        with open(os.path.join(harness.SKILL_DIR, "templates", "models", "moonshotai__kimi-k3.json")) as handle:
            package_price = json.load(handle)["input_price_per_token"]
        self.assertEqual(entry["input_price_per_token"], package_price, "and so does the package's price")
        self.assertEqual(entry["family"], "kimi")

    def test_the_project_layer_lands_over_the_user_layer(self):
        self.workspace.user_override("models/moonshotai__kimi-k3.json",
                                     {"effort": {"standard": "max"}, "min_max_tokens": 1000})
        self.workspace.override("models/moonshotai__kimi-k3.json", {"effort": {"standard": "low"}})
        entry = registry_lib.load(self.paths).get(self.MODEL)
        self.assertEqual(entry["effort"]["standard"], "low", "the project wins where both speak")
        self.assertEqual(entry["min_max_tokens"], 1000, "the user tier stands where it does not")

    def test_the_outermost_layer_is_the_root_the_manifest_records(self):
        self.workspace.user_override("models/moonshotai__kimi-k3.json", {"effort": {"standard": "max"}})
        registry_lib.load(self.paths)
        block = self.paths.roots_block()
        self.assertEqual(block["files"]["model:" + self.MODEL], self.workspace.user_root())
        self.assertIn("model:" + self.MODEL, block["user_overrides"])
        self.assertEqual(block["files"]["model:x-ai/grok-4.6"], paths_lib.SKILL_DIR,
                         "a model no outer root touched is still the package's")

    def test_a_model_only_an_outer_root_holds_joins_the_registry(self):
        self.workspace.user_override("models/acme__one.json", {
            "id": "acme/one", "connector": "openrouter", "family": "acme", "tiers": ["fast"],
            "effort": {"light": "low", "standard": "high", "deep": "max"},
            "effort_vocabulary": ["max", "high", "low"],
            "input_price_per_token": 1e-06, "output_price_per_token": 2e-06,
            "context_limit": 1000, "output_token_prior": 100})
        registry = registry_lib.load(self.paths)
        self.assertIsNotNone(registry.get("acme/one"))
        tiers = registry_lib.derive_tiers(registry, ["acme"], ["fast"], connector="openrouter")
        self.assertEqual(tiers["fast"]["acme"], "acme/one")

    def test_a_file_whose_id_contradicts_its_name_is_refused(self):
        self.workspace.user_override("models/acme__one.json", {"id": "acme/two"})
        with self.assertRaises(registry_lib.RegistryError) as caught:
            registry_lib.load(self.paths)
        self.assertIn("acme/two", str(caught.exception))

    def test_the_slug_rule_round_trips_and_refuses_what_it_could_not_invert(self):
        self.assertEqual(paths_lib.model_slug("moonshotai/kimi-k3"), "moonshotai__kimi-k3")
        self.assertEqual(paths_lib.model_id_for_slug("moonshotai__kimi-k3.json"), "moonshotai/kimi-k3")
        with self.assertRaises(paths_lib.PathError):
            paths_lib.model_slug("acme/one__two")


class DriverTest(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root, user=self.workspace.user_root())

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

        # The whole configuration comes from the outer roots: the project names its own connector
        # and its own families, and the packaged model files are pushed out of the tier map by the
        # project's `family_order`, which names only the two families this panel seats.
        self.workspace.override("config.json", {
            "default_connector": harness.CONNECTOR,
            "default_tier": "standard",
            "default_effort": "standard",
            "family_order": ["kimi", "xai"],
            "tier_order": ["standard"],
        })
        for model, record in harness.default_models().items():
            family = "kimi" if model == harness.SLOW_MODEL else "xai"
            self.workspace.override("models/" + paths_lib.model_slug(model) + ".json", dict(
                record, id=model, connector=harness.CONNECTOR, family=family, tiers=["standard"],
                effort=registry_lib.default_effort_map(record.get("effort_vocabulary"))))
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
        root = tempfile.mkdtemp(prefix="panel-review-readonly-")
        self.addCleanup(self._restore_and_remove, root)
        package = os.path.join(root, "panel-review")
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
             "--autonomous", "--reconcile", "off"],
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
        self.assertEqual(roots["files"]["model:" + harness.SLOW_MODEL], self.workspace.workspace_root(),
                         "the project's model file is the outermost layer for that id")
        self.assertEqual(roots["files"]["connector:" + harness.CONNECTOR], self.workspace.workspace_root(),
                         "and the connector came from the project root, whole")
        self.assertFalse(roots["user_root_present"], "no test reads the developer's own ~/.config")
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
             "--autonomous", "--reconcile", "off"],
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

#!/usr/bin/env python3
"""`install.sh`, run for real in a subprocess with `PATH` and the environment controlled.

**No network call is made, and that is arranged rather than hoped for.** `refresh_models.py` takes
the catalogue endpoint as a `--url`, and `install.sh` passes `$ENSEMBLE_REVIEW_CATALOGUE_URL`
through to it, so these tests point it at a `file://` fixture on disk. The proof that the fixture
was the thing read is in the registry afterwards: it carries the fixture's prices, which no live
catalogue would return.

Everything else is controlled the same way. `--workspace` puts the registry under a temp directory
so the package's own `templates/models.json` is never touched; a workspace `config.json` fragment
sets `api_key_secret: null`, which is the shipped opt-out of the vault leg, so no test shells out
to LastPass; and the key itself comes from `$OPENROUTER_API_KEY`.

    python3 scripts/tests/test_install.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
INSTALL = os.path.join(SKILL_DIR, "install.sh")

MODEL = "test/install-model"
SECRET = "sk-or-v1-this-value-must-never-be-printed"

# What the fixture catalogue says. Nothing live returns these, so finding them in the registry
# afterwards is the proof that the refresh read the fixture and made no request.
FIXTURE_PRICES = {"prompt": "0.0000123", "completion": "0.0000456"}
FIXTURE_CONTEXT = 424242


class InstallTestCase(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ensemble-review-install-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.workspace_root = os.path.join(self.root, ".agents", "ensemble-review")
        os.makedirs(self.workspace_root)

        # The shipped opt-out of the vault leg: a connector whose credential is not in the fleet
        # vault resolves from the environment alone. It deep-merges over the packaged config, so
        # every other cell — the tier map, the effort map — is the real one.
        _write_json(os.path.join(self.workspace_root, "config.json"),
                    {"openrouter": {"api_key_secret": None}})

        self.registry = os.path.join(self.workspace_root, "models.json")
        _write_json(self.registry, {
            "schema_version": "1",
            "models": {
                MODEL: {
                    "input_price_per_token": 1e-09,
                    "output_price_per_token": 2e-09,
                    "context_limit": 1000,
                    "effort_vocabulary": ["high", "low"],
                    "output_token_prior": 12345,
                    "prior_source": "test fixture",
                    "min_max_tokens": None,
                },
            },
        })

        self.catalogue = os.path.join(self.root, "catalogue.json")
        _write_json(self.catalogue, {"data": [{
            "id": MODEL,
            "pricing": FIXTURE_PRICES,
            "context_length": FIXTURE_CONTEXT,
            "reasoning": {"supported_efforts": ["max", "high", "low"]},
        }]})

    # --- running it ------------------------------------------------------------------------------

    def install(self, args=None, env=None, path_prefix=None):
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": self.root,
            "OPENROUTER_API_KEY": SECRET,
            "ENSEMBLE_REVIEW_CATALOGUE_URL": "file://" + self.catalogue,
        }
        environment.update(env or {})
        if path_prefix:
            environment["PATH"] = path_prefix + os.pathsep + environment["PATH"]
        command = [INSTALL, "--workspace", self.root] + list(args or [])
        return subprocess.run(command, capture_output=True, text=True, env=environment, cwd=self.root)

    def registry_entry(self):
        with open(self.registry, "r", encoding="utf-8") as handle:
            return json.load(handle)["models"][MODEL]


class HappyPathTest(InstallTestCase):

    def test_it_checks_python_the_key_and_the_registry_and_exits_zero(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("python:", result.stdout)
        self.assertIn("key:", result.stdout)
        self.assertIn("registry:", result.stdout)
        self.assertIn("ready.", result.stdout)

    def test_it_names_the_path_the_key_came_from_and_never_the_key(self):
        result = self.install()
        self.assertIn("env:OPENROUTER_API_KEY", result.stdout)
        self.assertNotIn(SECRET, result.stdout + result.stderr,
                         "the skills repo is public and a key echoed into a terminal is a key in a scrollback")

    def test_the_registry_is_seeded_from_the_catalogue_it_was_pointed_at(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.registry_entry()
        self.assertEqual(entry["input_price_per_token"], float(FIXTURE_PRICES["prompt"]))
        self.assertEqual(entry["output_price_per_token"], float(FIXTURE_PRICES["completion"]))
        self.assertEqual(entry["context_limit"], FIXTURE_CONTEXT)
        self.assertEqual(entry["effort_vocabulary"], ["max", "high", "low"])

    def test_it_never_touches_the_output_token_prior(self):
        """The catalogue knows what a token costs; only a run knows how many a lens spends."""
        self.install()
        self.assertEqual(self.registry_entry()["output_token_prior"], 12345)
        self.assertEqual(self.registry_entry()["prior_source"], "test fixture")

    def test_it_is_idempotent(self):
        self.install()
        after_first = _read(self.registry)
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing moved", result.stdout)
        self.assertEqual(_read(self.registry), after_first, "a refresh that changes nothing writes nothing")

    def test_the_packages_own_registry_is_left_alone_when_a_workspace_is_given(self):
        packaged = os.path.join(SKILL_DIR, "templates", "models.json")
        before = _read(packaged)
        self.install()
        self.assertEqual(_read(packaged), before)

    def test_dry_run_writes_nothing(self):
        before = _read(self.registry)
        result = self.install(args=["--dry-run"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing written", result.stdout)
        self.assertEqual(_read(self.registry), before)

    def test_check_only_makes_no_catalogue_call_at_all(self):
        before = _read(self.registry)
        result = self.install(args=["--check-only"], env={"ENSEMBLE_REVIEW_CATALOGUE_URL": ""})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not touched", result.stdout)
        self.assertEqual(_read(self.registry), before)


class FailureTest(InstallTestCase):

    def test_no_key_anywhere_exits_non_zero_and_names_both_paths_it_tried(self):
        result = self.install(env={"OPENROUTER_API_KEY": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No OpenRouter API key", result.stderr)
        self.assertIn("OPENROUTER_API_KEY", result.stderr)
        self.assertIn("api_key_secret is null", result.stderr,
                      "the vault leg says it was skipped rather than silently not tried")

    def test_a_python_older_than_3_10_exits_non_zero_before_anything_else(self):
        """`PATH` is the control: a stub `python3` that reports 3.9 and is found first."""
        stub_dir = os.path.join(self.root, "bin")
        os.makedirs(stub_dir)
        stub = os.path.join(stub_dir, "python3")
        _write(stub, "#!/bin/sh\necho 3.9\n")
        os.chmod(stub, 0o755)

        result = self.install(path_prefix=stub_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("too old", result.stderr)
        self.assertIn("3.9", result.stderr)
        self.assertNotIn("key:", result.stdout, "it stops at the first failure")

    def test_no_python_at_all_exits_non_zero_with_a_readable_message(self):
        result = self.install(env={"PATH": os.path.join(self.root, "empty"),
                                   "ENSEMBLE_REVIEW_PYTHON": "python-that-is-not-installed"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.10", result.stderr)

    def test_a_catalogue_that_does_not_answer_leaves_the_registry_untouched(self):
        before = _read(self.registry)
        result = self.install(env={"ENSEMBLE_REVIEW_CATALOGUE_URL": "file://" + os.path.join(self.root, "nope.json")})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("registry", result.stderr)
        self.assertEqual(_read(self.registry), before)

    def test_an_unknown_option_is_refused_rather_than_ignored(self):
        result = self.install(args=["--seed-everything"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown option", result.stderr)

    def test_a_workspace_that_does_not_exist_is_refused(self):
        result = subprocess.run(
            [INSTALL, "--workspace", os.path.join(self.root, "nowhere")],
            capture_output=True, text=True,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.root})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no such workspace", result.stderr)


class ShippedFileTest(unittest.TestCase):

    def test_install_sh_ships_executable(self):
        self.assertTrue(os.access(INSTALL, os.X_OK), "install.sh is not executable as it ships")


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _write_json(path, data):
    _write(path, json.dumps(data, indent=2) + "\n")


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Shared scaffolding for the no-network tests: a temp workspace, a scripted connector, a registry.

Not a test file — `unittest discover` collects `test*.py` and leaves this alone. Everything here
builds files on disk and hands back paths, so the scripts under test are exercised through their real
entry points rather than through seams opened for the tests.
"""

import json
import os
import shutil
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
FAKE_BACKEND = os.path.join(TESTS_DIR, "fake_backend.py")

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

SLOW_MODEL = "test/slow-model"
FAST_MODEL = "test/fast-model"

ARTIFACT_TEXT = """# A test artifact

## One
The config shape has no family dimension, so the seat constraints cannot resolve.

## Two
The budget is stated in one place and contradicted in another.
"""

REFERENCE_TEXT = """# A test reference

The source of truth says the config shape must carry a family dimension.
"""


def valid_report(verdict="fix-then-ship", summary="One defect found.", findings=None):
    """The reviewer-authored half of a report. The dispatcher stamps the audit fields over it."""
    if findings is None:
        findings = [{
            "id": "F1",
            "location": "One",
            "quote": "The config shape has no family dimension",
            "claim": "The config shape has no family dimension.",
            "citation": None,
            "severity": "should-fix",
            "reasoning": "A builder cannot resolve a seat constraint without one.",
            "suggested_change": "Add the family axis under the tier map.",
            "change_kind": "judgment-call",
            "literal_edit": None,
            "confidence": "high",
            "externally_verified": False,
            "tags": ["configuration"],
        }]
    return {"verdict": verdict, "summary": summary, "findings": findings, "method_notes": "scripted"}


class Workspace(object):
    """A throwaway directory holding an artifact, a reference, a config, a registry and a panel."""

    def __init__(self, models=None, tiers=None, effort=None):
        self.root = tempfile.mkdtemp(prefix="ensemble-review-test-")
        self.artifact = os.path.join(self.root, "artifact.md")
        self.reference = os.path.join(self.root, "reference.md")
        _write(self.artifact, ARTIFACT_TEXT)
        _write(self.reference, REFERENCE_TEXT)

        self.plan_path = os.path.join(self.root, "plan.json")
        self.log_path = os.path.join(self.root, "calls.jsonl")
        self.config = os.path.join(self.root, "config.json")
        self.registry = os.path.join(self.root, "models.json")
        self.panel = os.path.join(self.root, "panel.json")

        _write_json(self.config, {
            "openrouter": {
                "type": FAKE_BACKEND,
                "base_url": "https://example.invalid/api/v1",
                "api_key_secret": None,
                "api_key_env": "ENSEMBLE_REVIEW_TEST_KEY",
                "default_tier": "standard",
                "tiers": {"standard": dict(tiers or {"kimi": SLOW_MODEL, "xai": FAST_MODEL})},
                "effort": dict(effort or {FAST_MODEL: "high"}),
            }
        })
        _write_json(self.registry, {"schema_version": "1", "models": dict(models or default_models())})
        _write_json(self.panel, {
            "name": "test-panel",
            "requires_references": False,
            "min_families": 2,
            "tier": "standard",
            "seats": [
                {"lens": "consistency", "family": "kimi"},
                {"lens": "adversarial", "family": "xai"},
            ],
        })
        self.plan({})

    # --- scripting -------------------------------------------------------------------------------

    def plan(self, plan):
        _write_json(self.plan_path, plan)

    def env(self, extra=None):
        environment = dict(os.environ)
        environment["FAKE_BACKEND_PLAN"] = self.plan_path
        environment["FAKE_BACKEND_LOG"] = self.log_path
        environment["ENSEMBLE_REVIEW_TEST_KEY"] = "not-a-real-key"
        environment.update(extra or {})
        return environment

    def apply_env(self):
        """Set the fake connector's environment on this process, for an in-process entry point."""
        os.environ.update({k: v for k, v in self.env().items() if k.startswith("FAKE_BACKEND_") or k == "ENSEMBLE_REVIEW_TEST_KEY"})

    def calls(self, model=None):
        if not os.path.isfile(self.log_path):
            return []
        records = []
        with open(self.log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return [r for r in records if model is None or r["model"] == model]

    def reset_calls(self):
        if os.path.isfile(self.log_path):
            os.remove(self.log_path)

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def default_models():
    return {
        SLOW_MODEL: {
            "input_price_per_token": 2e-06,
            "output_price_per_token": 1e-05,
            "context_limit": 1000000,
            "effort_vocabulary": ["max", "high", "low"],
            "output_token_prior": 20000,
            "prior_source": "test fixture",
            "min_max_tokens": 64000,
        },
        FAST_MODEL: {
            "input_price_per_token": 1e-07,
            "output_price_per_token": 5e-07,
            "context_limit": 1000000,
            "effort_vocabulary": ["xhigh", "high", "medium", "low"],
            "output_token_prior": 10000,
            "prior_source": "test fixture",
            "min_max_tokens": None,
        },
    }


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _write_json(path, data):
    _write(path, json.dumps(data, indent=2) + "\n")

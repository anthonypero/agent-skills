"""Shared scaffolding for the no-network tests: a temp workspace, a scripted connector, a registry.

Not a test file — `unittest discover` collects `test*.py` and leaves this alone. Everything here
builds files on disk and hands back paths, so the scripts under test are exercised through their real
entry points rather than through seams opened for the tests.

**Three files rather than two, since the config restructure.** A workspace writes:

- `config.json` at the temp root, passed as `--config`, holding only the run-wide defaults and the
  name of the connector to use;
- `connectors/fake.json` under the workspace override root, resolved by that name through the
  cascade. It ships `billing: free`, so the spend gate is not in the way of every test that never
  meant to exercise it; `Workspace(billing="metered")` is how a test asks for the gate;
- `models/<slug>.json`, one file per model id, in a directory passed as `--models`. The directory
  is an operator path, which is what keeps the packaged model files out of a test's tier map.

Every model gets a full abstract-effort map derived from its own vocabulary unless the test says
otherwise. `effort={MODEL: "word"}` rebinds that model's `standard` level; `effort={MODEL: None}`
gives that model no effort map at all, which is the composition error a seat asking for a level
nobody mapped should raise.
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

from lib import judge as judge_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402

# The connector every test workspace names. Not `openrouter`: a test that resolved the packaged
# connector file would be pointed at the real endpoint by a typo in its own config.
CONNECTOR = "fake"

# Sentinel for `Workspace.edit_model`: remove the key rather than set it.
DROP = object()

SLOW_MODEL = "test/slow-model"
FAST_MODEL = "test/fast-model"
THIRD_MODEL = "test/third-model"

# Where a workspace override lives, relative to the project holding the artifact.
WORKSPACE_SUBDIR = os.path.join(".agents", "ensemble-review")

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
            "suggested_change": "Choose one and state it: carry the family axis under the tier map, or resolve families outside the tier map entirely.",
            "change_kind": "judgment-call",
            "literal_edit": None,
            "confidence": "high",
            "externally_verified": False,
            # `judgment-call` is a design fork and carries `fork`, which the ingest-time validator
            # requires. A scripted report that could not pass that check would test the repair
            # re-ask rather than the path every other test here is about.
            "tags": ["configuration", "fork"],
        }]
    return {"verdict": verdict, "summary": summary, "findings": findings, "method_notes": "scripted"}


class Workspace(object):
    """A throwaway directory holding an artifact, a reference, a config, a registry and a panel."""

    def __init__(self, models=None, tiers=None, effort=None, billing="free",
                 requires_approval=None, default_effort=None, provider_routing=None):
        self.root = tempfile.mkdtemp(prefix="ensemble-review-test-")
        self.artifact = os.path.join(self.root, "artifact.md")
        self.reference = os.path.join(self.root, "reference.md")
        _write(self.artifact, ARTIFACT_TEXT)
        _write(self.reference, REFERENCE_TEXT)

        self.plan_path = os.path.join(self.root, "plan.json")
        self.log_path = os.path.join(self.root, "calls.jsonl")
        self.config = os.path.join(self.root, "config.json")
        self.registry = os.path.join(self.root, "models")
        self.panel = os.path.join(self.root, "panel.json")

        self.tiers = dict(tiers or {"kimi": SLOW_MODEL, "xai": FAST_MODEL})
        self.connector = self.override("connectors/{0}.json".format(CONNECTOR), {
            "name": CONNECTOR,
            "type": FAKE_BACKEND,
            "base_url": "https://example.invalid/api/v1",
            "api_key_secret": None,
            "api_key_env": "ENSEMBLE_REVIEW_TEST_KEY",
            "catalogue_url": "https://example.invalid/api/v1/models",
            "billing": billing,
            "requires_approval": requires_approval,
            "provider_routing": dict(provider_routing or {}),
        })
        # The config names the connector **file**, not the bare name, because most of these tests
        # pass `--config` without a `--workspace` and the cascade would otherwise have no root that
        # holds it. An operator path is the same escape hatch a driver `type` has.
        _write_json(self.config, {
            "default_connector": self.connector,
            "default_tier": "standard",
            "default_effort": default_effort or "standard",
            # Declaration order is the tier map's own, which is what the seating passes walk.
            "family_order": list(self.tiers),
            "tier_order": ["standard"],
        })
        self.write_models(dict(models or default_models()), effort=effort)
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

    # --- the registry, as a directory of one file per model ----------------------------------------

    def write_models(self, models, effort=None, directory=None, tiers=None):
        """One model file per id, with the family and tier this workspace seats it at.

        `effort` is per model: a word rebinds that model's `standard` level, `None` leaves the
        model with no effort map at all. Anything the caller does not name gets the same default
        binding the migration used — top rung for `deep`, bottom for `light`, the middle of three
        or one below the top from four rungs up.
        """
        directory = directory or self.registry
        if not os.path.isdir(directory):
            os.makedirs(directory)
        effort = dict(effort or {})
        seats = dict(tiers if tiers is not None else self.tiers)
        family_of = {model: family for family, model in seats.items()}
        for model, entry in models.items():
            record = dict(entry)
            record["id"] = model
            record.setdefault("connector", CONNECTOR)
            record.setdefault("family", family_of.get(model))
            record.setdefault("tiers", ["standard"] if model in family_of else [])
            if model in effort:
                word = effort[model]
                record["effort"] = None if word is None else dict(
                    registry_lib.default_effort_map(entry.get("effort_vocabulary")), standard=word)
            else:
                record.setdefault(
                    "effort", registry_lib.default_effort_map(entry.get("effort_vocabulary")))
            _write_json(os.path.join(directory, paths_lib.model_slug(model) + ".json"), record)
        return directory

    def model_path(self, model, directory=None):
        return os.path.join(directory or self.registry, paths_lib.model_slug(model) + ".json")

    def edit_model(self, model, **fields):
        """Change one model file in place. A field set to `_DROP` is removed from the file."""
        path = self.model_path(model)
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        for key, value in fields.items():
            if value is DROP:
                record.pop(key, None)
            else:
                record[key] = value
        _write_json(path, record)
        return path

    def seat_model(self, model, family, tiers, **facts):
        """Put one model in one or more tier cells: write its file and widen the config's vocabularies.

        The tiers x families map is derived from the model files now, so "add a cell" means "say so
        in the model's own file" — and the config's `family_order` / `tier_order` are what fix the
        declaration order the seating passes walk, so a new family or tier is named there too.
        """
        record = dict(_two_models().get(model) or default_models(third=True).get(model) or {})
        record.update(facts)
        record.update({"id": model, "connector": CONNECTOR, "family": family, "tiers": list(tiers)})
        record.setdefault("effort", registry_lib.default_effort_map(record.get("effort_vocabulary")))
        record.setdefault("effort_vocabulary", ["max", "high", "low"])
        record.setdefault("input_price_per_token", 1e-06)
        record.setdefault("output_price_per_token", 2e-06)
        record.setdefault("context_limit", 1000000)
        record.setdefault("output_token_prior", 1000)
        if not record.get("effort"):
            record["effort"] = registry_lib.default_effort_map(record["effort_vocabulary"])
        _write_json(self.model_path(model), record)
        self.edit_config(lambda config: config.update({
            "family_order": list(config["family_order"]) + [family] if family not in config["family_order"] else config["family_order"],
            "tier_order": list(config["tier_order"]) + [t for t in tiers if t not in config["tier_order"]],
        }))
        return self.model_path(model)

    def edit_config(self, mutate):
        """Apply `mutate` to this workspace's `config.json` in place."""
        with open(self.config, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        mutate(config)
        _write_json(self.config, config)
        return config

    def remove_model(self, model):
        os.remove(self.model_path(model))

    # --- scripting -------------------------------------------------------------------------------

    def plan(self, plan):
        _write_json(self.plan_path, plan)

    def env(self, extra=None):
        environment = dict(os.environ)
        environment["FAKE_BACKEND_PLAN"] = self.plan_path
        environment["FAKE_BACKEND_LOG"] = self.log_path
        environment["ENSEMBLE_REVIEW_TEST_KEY"] = "not-a-real-key"
        # **Whether a harness is present is a property of the machine, and no test may read the
        # real one.** `judge_lib.harness_present()` asks whether `ensemble-judge` is installed in
        # the harness agents directory, and that decides the default reconciler for every
        # unattended run. Pointed at an empty directory under this workspace, the answer is a firm
        # "no" whether or not the developer running the suite has ever run `install.sh`. A test
        # that wants the other answer calls `install_harness_judge()`.
        environment[judge_lib.HARNESS_AGENTS_DIR_ENV] = self.agents_dir()
        # **No test reads the developer's own `~/.config`.** The cascade's middle root is pointed
        # at a directory under this workspace, which is absent until `user_override()` creates it.
        environment[paths_lib.USER_ROOT_ENV] = self.user_root()
        environment.update(extra or {})
        return environment

    def apply_env(self):
        """Set the fake connector's environment on this process, for an in-process entry point."""
        keys = ("FAKE_BACKEND_", "ENSEMBLE_REVIEW_TEST_KEY", judge_lib.HARNESS_AGENTS_DIR_ENV,
                paths_lib.USER_ROOT_ENV)
        os.environ.update({k: v for k, v in self.env().items() if k.startswith(keys)})

    def user_root(self):
        """This workspace's stand-in for `~/.config/ensemble-review`. Not created until used."""
        return os.path.join(self.root, "user-config")

    def user_override(self, relpath, data=None, text=None):
        """Write a file into the cascade's **middle** root, this machine's per-owner tier."""
        return _write_into(os.path.join(self.user_root(), relpath), data, text)

    def agents_dir(self):
        """This workspace's stand-in for `~/.claude/agents`. Not created until something needs it."""
        return os.path.join(self.root, "harness-agents")

    def install_harness_judge(self):
        """Put the shipped judge agent where `judge_lib.harness_present()` looks. Returns the path."""
        directory = self.agents_dir()
        if not os.path.isdir(directory):
            os.makedirs(directory)
        destination = os.path.join(directory, judge_lib.HARNESS_JUDGE_AGENT + ".md")
        shutil.copyfile(os.path.join(SKILL_DIR, "agents", judge_lib.HARNESS_JUDGE_AGENT_FILE),
                        destination)
        return destination

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

    # --- the workspace override root ---------------------------------------------------------------

    def workspace_root(self):
        return os.path.join(self.root, WORKSPACE_SUBDIR)

    def override(self, relpath, data=None, text=None):
        """Write a file into `<root>/.agents/ensemble-review/`, the cascade's first root."""
        return _write_into(os.path.join(self.workspace_root(), relpath), data, text)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def shipped(workspace=None, user=None):
    """The packaged config, connector, registry and resolved connector entry, with no outer root.

    `workspace` and `user` default to directories that do not exist, so what comes back is the
    package's own answer and nothing a developer happens to keep in `~/.config` can change it.
    """
    from lib import connectors as connectors_lib

    paths = paths_lib.Paths(
        workspace=workspace or os.path.join(tempfile.gettempdir(), "ensemble-review-no-such-workspace"),
        user=user or os.path.join(tempfile.gettempdir(), "ensemble-review-no-such-user"))
    config, config_path = paths.config()
    connector, connector_path = connectors_lib.load(paths, config["default_connector"])
    registry = registry_lib.load(paths)
    entry = connectors_lib.compose(config, connector, registry)
    return {
        "paths": paths,
        "config": config,
        "config_path": config_path,
        "connector": connector,
        "connector_path": connector_path,
        "registry": registry,
        "entry": entry,
    }


def default_models(third=False):
    models = _two_models()
    if third:
        models[THIRD_MODEL] = {
            "input_price_per_token": 1e-07,
            "output_price_per_token": 5e-07,
            "context_limit": 1000000,
            "effort_vocabulary": ["high", "medium", "low"],
            "output_token_prior": 10000,
            "prior_source": "test fixture",
            "min_max_tokens": None,
        }
    return models


def _two_models():
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


def _write_into(path, data=None, text=None):
    directory = os.path.dirname(path)
    if not os.path.isdir(directory):
        os.makedirs(directory)
    if text is not None:
        _write(path, text)
    else:
        _write_json(path, data)
    return path


def _write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _write_json(path, data):
    _write(path, json.dumps(data, indent=2) + "\n")

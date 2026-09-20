#!/usr/bin/env python3
"""Abstract effort, the spend gate, the user tier in a run's record, what a refresh may write, and
who wins a contested tier cell.

The parts of the config restructure that are not about resolution — `test_paths.py` covers the
cascade itself, and `test_seating.py` covers the derived tier map and the same-root collision. No
network and no paid call: the connector both `run_panel.py` and `dispatch.py` load is
`fake_backend.py`, and every test that exercises `refresh_models.py` hands it a catalogue in memory.

    python3 scripts/tests/test_config_restructure.py
"""

import io
import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import dispatch  # noqa: E402
import harness  # noqa: E402
import reconcile  # noqa: E402
import refresh_models  # noqa: E402
import run_panel  # noqa: E402
from lib import connectors as connectors_lib  # noqa: E402
from lib import judge as judge_lib  # noqa: E402
from lib import panels as panels_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import runs as runs_lib  # noqa: E402
from lib import seating as seating_lib  # noqa: E402

RUN_ID = "2026-09-19-1"


def _capture(fn, argv):
    out_stream, err_stream = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_stream, err_stream
    try:
        code = fn(argv)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return code, out_stream.getvalue(), err_stream.getvalue()


# --- the abstract levels themselves -----------------------------------------------------------------

class AbstractLevelTest(unittest.TestCase):
    """Three levels, and the names are not any vendor's rungs."""

    def test_there_are_exactly_three_and_none_of_them_is_a_vendor_word(self):
        self.assertEqual(registry_lib.EFFORT_LEVELS, ("light", "standard", "deep"))
        shipped = harness.shipped()["registry"]
        rungs = {rung for entry in shipped.models.values()
                 for rung in (entry.get("effort_vocabulary") or [])}
        self.assertTrue(rungs, "the shipped registry records vocabularies to compare against")
        for level in registry_lib.EFFORT_LEVELS:
            self.assertNotIn(level, rungs,
                             "{0!r} is also a vendor rung, so a reader cannot tell them apart".format(level))

    def test_three_because_the_narrowest_shipped_ladder_has_three_rungs(self):
        """A five-level abstraction would bind two levels to one rung on every three-rung model,
        and the manifest would then record two intentions for one parameter."""
        shipped = harness.shipped()["registry"]
        widths = sorted(len(entry.get("effort_vocabulary") or []) for entry in shipped.models.values())
        self.assertEqual(widths[0], len(registry_lib.EFFORT_LEVELS))
        for model, entry in shipped.models.items():
            bound = {registry_lib.effort_binding(entry, level, model)[1]
                     for level in registry_lib.EFFORT_LEVELS}
            self.assertEqual(len(bound), len(registry_lib.EFFORT_LEVELS),
                             "{0} binds two levels to one rung".format(model))

    def test_the_default_map_takes_the_top_the_bottom_and_one_below_top_from_four_rungs_up(self):
        self.assertEqual(registry_lib.default_effort_map(["max", "high", "low"]),
                         {"deep": "max", "standard": "high", "light": "low"})
        self.assertEqual(registry_lib.default_effort_map(["max", "xhigh", "high", "medium", "low"]),
                         {"deep": "max", "standard": "xhigh", "light": "low"})
        self.assertEqual(registry_lib.default_effort_map(["xhigh", "high", "medium", "low"]),
                         {"deep": "xhigh", "standard": "high", "light": "low"})


# --- the resolution order ---------------------------------------------------------------------------

class EffortResolutionOrderTest(unittest.TestCase):
    """`--effort` -> the seat -> the panel -> the config -> the persona, and abstract at every level."""

    CONFIG = {"default_effort": "deep"}

    def resolve(self, cli=None, seat=None, panel=None, config=None, persona=None):
        seats = seating_lib.resolve(
            {"name": "p", "effort": panel,
             "seats": [dict({"lens": "consistency", "family": "kimi"}, **({"effort": seat} if seat else {}))]},
            {"tiers": {"standard": {"kimi": "m/one"}}, "default_tier": "standard",
             "default_effort": config},
            cli_effort=cli,
            frontmatter_fn=(lambda _lens: {"effort": persona}) if persona else None)
        return seats[0]["effort_level"], seats[0]["effort_source"]

    def test_the_command_line_beats_everything(self):
        self.assertEqual(self.resolve(cli="light", seat="deep", panel="deep", config="deep",
                                      persona="deep"), ("light", "--effort"))

    def test_the_seat_beats_the_panel(self):
        self.assertEqual(self.resolve(seat="light", panel="deep", config="deep"), ("light", "seat"))

    def test_the_panel_beats_the_config(self):
        self.assertEqual(self.resolve(panel="light", config="deep"), ("light", "panel"))

    def test_the_config_beats_the_persona(self):
        """The inversion of framework §8, and the same one tier makes: the depth a seat thinks at is
        a property of the run's stakes, not of the lens."""
        self.assertEqual(self.resolve(config="light", persona="deep"), ("light", "config"))

    def test_the_persona_answers_when_nothing_above_it_does(self):
        self.assertEqual(self.resolve(persona="deep"), ("deep", "persona"))

    def test_the_hard_default_is_standard(self):
        self.assertEqual(self.resolve(), (registry_lib.DEFAULT_EFFORT_LEVEL, "default"))

    def test_every_shipped_persona_carries_an_abstract_level(self):
        paths = harness.shipped()["paths"]
        from lib import report as report_lib
        for name in sorted(os.listdir(os.path.join(paths_lib.SKILL_DIR, "agents"))):
            if not name.startswith("lens-") and name != "synthesis.md":
                continue
            frontmatter, _body = report_lib.parse_agent_file(paths.persona(name[:-3]))
            self.assertIn(frontmatter.get("effort"), registry_lib.EFFORT_LEVELS,
                          "{0} carries {1!r}, which is not an abstract level".format(
                              name, frontmatter.get("effort")))


class JudgeEffortOrderTest(unittest.TestCase):
    """The judgment call's own order, which is not the seat's and diverges at the top.

    `reconcile.py` has no `--effort` and a per-seat knob is not about the judge, so those two levels
    are replaced by the one that is: the template's `synthesis.effort`, beside the `synthesis.family`
    and `synthesis.tier` that template block already carries.
    """

    def resolve(self, pinned=None, run=None, config=None, persona=None):
        manifest = {"seats": [{"effort_level": run}] if run else []}
        panel = {"synthesis": {"effort": pinned}} if pinned else {}
        return judge_lib.synthesis_effort(
            manifest, {"default_effort": config}, {"effort": persona} if persona else None,
            panel=panel)

    def test_the_template_pin_beats_the_level_the_run_actually_dispatched_at(self):
        self.assertEqual(self.resolve(pinned="deep", run="light", config="light", persona="light"),
                         ("deep", "synthesis"))

    def test_without_a_pin_the_run_still_decides(self):
        self.assertEqual(self.resolve(run="deep", config="light"), ("deep", "run"))

    def test_the_order_is_exhaustive_and_every_source_is_named(self):
        for source in (self.resolve(pinned="deep")[1], self.resolve(run="deep")[1],
                       self.resolve(config="deep")[1], self.resolve(persona="deep")[1],
                       self.resolve()[1]):
            self.assertIn(source, judge_lib.EFFORT_SOURCES)
        self.assertEqual(judge_lib.EFFORT_SOURCES[0], "synthesis", "the top of the judge's order")

    def test_a_template_may_carry_effort_in_its_synthesis_block(self):
        panel = {"name": "t", "seats": [{"lens": "consistency", "family": "kimi"}],
                 "synthesis": {"family": "openai", "tier": "frontier", "effort": "deep"}}
        self.assertEqual(panels_lib.unknown_keys(panel), [],
                         "a template that pins the judge's depth is not a typo")


class AbstractLevelTypoTest(unittest.TestCase):
    """A level that is not a level names the typo and where it was typed — not the model file.

    Only `--effort` is checked by a parser. `standrd` in a persona, a template seat, a panel or
    `config.json` used to travel all the way down to `effort_binding` and surface as "this model has
    no 'standrd' rung", which sends the reader to the one file the mistake is not in.
    """

    SEAT = {"reviewer_id": "consistency-kimi", "model": "m/one", "effort_level": "standrd"}
    ENTRY = {"id": "m/one", "effort_vocabulary": ["max", "high", "low"],
             "effort": {"light": "low", "standard": "high", "deep": "max"}}

    def errors(self, source):
        registry = registry_lib.Registry({"models": {"m/one": dict(self.ENTRY)}}, "inline")
        return seating_lib.effort_errors([dict(self.SEAT, effort_source=source)], registry)

    def test_each_source_is_named_in_its_own_words(self):
        """The three sources that are one file each. The persona rung has its own test below,
        because it is only reachable through a real cascade and naming it is half the point."""
        for source, phrase in (("seat", "seat in the panel template"),
                               ("panel", "panel template's own `effort`"),
                               ("config", "config.json's `default_effort`")):
            errors = self.errors(source)
            self.assertEqual(len(errors), 1, source)
            self.assertIn("standrd", errors[0], "the typo itself")
            self.assertIn(phrase, errors[0], "and where it was typed")
            self.assertNotIn("rung:", errors[0], "not reported against the model file")

    def test_a_persona_typo_is_named_against_that_persona_file(self):
        """The persona rung, reached the only way it can be: a config that names no level.

        Stubbing `effort_source="persona"` onto a seat proves the message and not the rung, and the
        rung was in fact dead — `connectors.compose` substituted `standard` for a null
        `default_effort`, so the config level always answered. This drives `run_panel.py` over a
        workspace persona carrying the typo, with `default_effort` nulled, and the run refuses at
        the effort gate naming that persona's own file.
        """
        workspace = harness.Workspace()
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.edit_config(lambda config: config.update({"default_effort": None}))
        workspace.override("agents/lens-consistency.md", text=(
            "---\nname: lens-consistency\ndescription: A workspace lens.\n"
            "model: frontier\neffort: standrd\n---\n\n"
            "# Role\n\nRead the artifact and file what does not hold together.\n"))
        workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}],
                        harness.FAST_MODEL: [{"body": harness.valid_report()}]})

        code, _out, err = _capture(run_panel.main, [
            "--panel", workspace.panel, "--artifact", workspace.artifact,
            "--out", workspace.path("reviews", RUN_ID), "--workspace", workspace.root,
            "--config", workspace.config, "--models", workspace.registry,
            "--tier", "standard", "--autonomous", "--reconcile", "off"])

        self.assertEqual(code, run_panel.EXIT_COMPOSITION, err)
        self.assertIn("standrd", err, "the typo itself")
        self.assertIn("agents/lens-consistency.md", err, "and the file it was typed in")
        self.assertNotIn("rung:", err, "not reported against the model file")
        self.assertEqual(workspace.calls(), [], "nothing was dispatched")

    def test_the_persona_rung_answers_when_the_config_names_no_level(self):
        """The same arrangement with a level that is real: it resolves, and says where it came from."""
        workspace = harness.Workspace()
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.edit_config(lambda config: config.update({"default_effort": None}))
        workspace.override("agents/lens-consistency.md", text=(
            "---\nname: lens-consistency\ndescription: A workspace lens.\n"
            "model: frontier\neffort: light\n---\n\n"
            "# Role\n\nRead the artifact and file what does not hold together.\n"))
        workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}],
                        harness.FAST_MODEL: [{"body": harness.valid_report()}]})
        run_dir = workspace.path("reviews", RUN_ID)

        code, _out, err = _capture(run_panel.main, [
            "--panel", workspace.panel, "--artifact", workspace.artifact,
            "--out", run_dir, "--workspace", workspace.root,
            "--config", workspace.config, "--models", workspace.registry,
            "--tier", "standard", "--autonomous", "--reconcile", "off"])

        self.assertEqual(code, 0, err)
        seat = next(s for s in runs_lib.read_manifest(run_dir)["seats"]
                    if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual((seat["effort_level"], seat["effort_source"]), ("light", "persona"))

    def test_the_message_lists_the_levels_that_do_exist(self):
        self.assertIn("light/standard/deep", self.errors("config")[0])

    def test_a_real_level_still_binds(self):
        registry = registry_lib.Registry({"models": {"m/one": dict(self.ENTRY)}}, "inline")
        seat = dict(self.SEAT, effort_level="deep", effort_source="config")
        self.assertEqual(seating_lib.effort_errors([seat], registry), [])
        self.assertEqual(seat["effort"], "max")

    def test_dispatch_refuses_the_same_typo_against_the_level_rather_than_the_model(self):
        with self.assertRaises(dispatch.EffortRefused) as caught:
            dispatch.resolve_effort("m/one", dict(self.ENTRY), "standrd")
        self.assertIn("standrd", str(caught.exception))
        self.assertIn("default_effort", str(caught.exception),
                      "the two places a level can reach a dispatch from")


class EffortBindingErrorTest(unittest.TestCase):
    """The two composition errors, at the library level. Their CLI halves live in `test_dispatch_retry`."""

    VOCAB = ["max", "high", "low"]

    def entry(self, effort):
        return {"id": "m/one", "effort_vocabulary": list(self.VOCAB), "effort": effort}

    def test_a_level_the_model_does_not_map_names_the_model_and_the_levels_it_does(self):
        with self.assertRaises(registry_lib.EffortError) as caught:
            registry_lib.effort_binding(self.entry({"light": "low", "standard": "high"}), "deep")
        message = str(caught.exception)
        self.assertIn("m/one", message)
        self.assertIn("deep", message)
        self.assertIn("light, standard", message, "the message lists what the file does map")

    def test_a_mapped_word_outside_the_vocabulary_names_the_vocabulary(self):
        with self.assertRaises(registry_lib.EffortError) as caught:
            registry_lib.effort_binding(self.entry({"standard": "xhigh"}), "standard")
        message = str(caught.exception)
        self.assertIn("xhigh", message)
        self.assertIn("max/high/low", message)
        self.assertIn("refresh_models.py", message)

    def test_a_model_with_no_recorded_vocabulary_sends_no_parameter_rather_than_refusing(self):
        """No ladder means nothing to choose, which is not the same as a ladder nobody has indexed."""
        kind, value = registry_lib.effort_binding({"id": "m/one", "effort_vocabulary": None}, "deep")
        self.assertEqual((kind, value), ("none", None))

    def test_a_reasoning_token_budget_is_accepted_beside_the_word_form(self):
        self.assertEqual(registry_lib.effort_binding(self.entry({"deep": {"max_tokens": 8000}}), "deep"),
                         ("tokens", 8000))
        self.assertEqual(registry_lib.effort_binding(self.entry({"deep": 8000}), "deep"),
                         ("tokens", 8000))
        with self.assertRaises(registry_lib.EffortError):
            registry_lib.effort_binding(self.entry({"deep": {"max_tokens": 0}}), "deep")


# --- the flag, end to end ----------------------------------------------------------------------------

class PanelTestCase(unittest.TestCase):

    BILLING = "free"
    REQUIRES_APPROVAL = None

    def setUp(self):
        self.workspace = harness.Workspace(billing=self.BILLING,
                                           requires_approval=self.REQUIRES_APPROVAL)
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        self.run_dir = self.workspace.path("reviews", RUN_ID)

    def panel(self, extra=None):
        argv = ["--panel", self.workspace.panel, "--artifact", self.workspace.artifact,
                "--out", self.run_dir, "--workspace", self.workspace.root,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--tier", "standard", "--autonomous", "--reconcile", "off"] + list(extra or [])
        return _capture(run_panel.main, argv)

    def manifest(self):
        return runs_lib.read_manifest(self.run_dir)

    def seat(self, reviewer_id):
        return next(s for s in self.manifest()["seats"] if s["reviewer_id"] == reviewer_id)


class EffortFlagTest(PanelTestCase):

    def test_the_flag_sets_every_seats_level_and_each_model_binds_its_own_rung(self):
        code, _out, err = self.panel(extra=["--effort", "deep"])
        self.assertEqual(code, 0, err)
        for reviewer_id in ("consistency-kimi", "adversarial-xai"):
            record = self.seat(reviewer_id)
            self.assertEqual(record["effort_level"], "deep")
            self.assertEqual(record["effort_source"], "--effort")
        # The two test models have different ladders, and `deep` is the top of each.
        self.assertEqual(self.seat("consistency-kimi")["effort"], "max", "max/high/low")
        self.assertEqual(self.seat("adversarial-xai")["effort"], "xhigh", "xhigh/high/medium/low")

    def test_the_parameter_that_was_sent_is_the_parameter_on_the_wire(self):
        code, _out, err = self.panel(extra=["--effort", "light"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.workspace.calls(harness.SLOW_MODEL)[0]["effort"], "low")
        self.assertEqual(self.workspace.calls(harness.FAST_MODEL)[0]["effort"], "low")

    def test_without_the_flag_the_config_default_decides_and_says_so(self):
        code, _out, err = self.panel()
        self.assertEqual(code, 0, err)
        record = self.seat("consistency-kimi")
        self.assertEqual((record["effort_level"], record["effort_source"]), ("standard", "config"))

    def test_a_level_no_model_maps_refuses_the_run_before_any_call(self):
        self.workspace.edit_model(harness.SLOW_MODEL, effort={"light": "low", "standard": "high"})
        code, _out, err = self.panel(extra=["--effort", "deep"])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION, err)
        self.assertIn("consistency-kimi", err)
        self.assertIn(harness.SLOW_MODEL, err)
        self.assertEqual(self.workspace.calls(), [], "nothing was dispatched")

    def test_an_unknown_level_is_refused_by_the_parser_rather_than_sent(self):
        with self.assertRaises(SystemExit):
            _capture(run_panel.main, ["--panel", self.workspace.panel,
                                      "--artifact", self.workspace.artifact,
                                      "--out", self.run_dir, "--effort", "maximum"])


# --- the spend gate -----------------------------------------------------------------------------------

class SpendGateFreeConnectorTest(PanelTestCase):
    """A `free` connector is never gated, and the manifest still says so."""

    def test_it_runs_with_no_flag_and_records_that_nothing_was_required(self):
        code, _out, err = self.panel()
        self.assertEqual(code, 0, err)
        block = self.manifest()["spend_approval"]
        self.assertEqual(block["billing"], "free")
        self.assertFalse(block["required"])
        self.assertIsNone(block["granted"], "nothing to grant, so not a yes and not a no")


class SpendGateSubscriptionTest(PanelTestCase):
    """A plan already paid for. The marginal call is free, so there is nothing to approve."""

    BILLING = "subscription"

    def test_a_subscription_connector_is_unaffected_by_the_gate(self):
        code, _out, err = self.panel()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.workspace.calls()), 2)
        block = self.manifest()["spend_approval"]
        self.assertEqual(block["billing"], "subscription")
        self.assertFalse(block["required"])

    def test_requires_approval_on_a_subscription_connector_is_ignored(self):
        """The field answers "may this bill an account", and a subscription does not bill one."""
        self.assertFalse(connectors_lib.requires_approval(
            {"billing": "subscription", "requires_approval": True}))


class SpendGateMeteredTest(PanelTestCase):
    """`metered` means every call costs money on an account, so somebody has to say yes."""

    BILLING = "metered"

    def test_an_autonomous_run_refuses_with_exit_four_and_makes_no_call(self):
        code, _out, err = self.panel()
        self.assertEqual(code, run_panel.EXIT_BUDGET, err)
        self.assertEqual(self.workspace.calls(), [], "no paid call was made")
        self.assertIn(harness.CONNECTOR, err, "the message names the connector")
        self.assertIn("consistency-kimi", err, "and the seats bound to it")
        self.assertIn("adversarial-xai", err)
        self.assertIn("--approve-spend", err, "and the flag to pass")
        self.assertRegex(err, r"projected spend: \$\d", "and the projected spend")

    def test_the_refusal_is_recorded_rather_than_only_printed(self):
        self.panel()
        block = self.manifest()["spend_approval"]
        self.assertTrue(block["required"])
        self.assertFalse(block["granted"])
        self.assertEqual(block["source"], "autonomous-refusal")
        self.assertEqual(block["seats"], ["adversarial-xai", "consistency-kimi"])
        self.assertEqual(self.manifest()["projection"]["decision"], "refused-spend-not-approved")

    def test_the_flag_lets_it_through_and_the_yes_is_in_the_audit_trail(self):
        code, _out, err = self.panel(extra=["--approve-spend"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.workspace.calls()), 2)
        block = self.manifest()["spend_approval"]
        self.assertTrue(block["required"])
        self.assertTrue(block["granted"])
        self.assertEqual(block["source"], "--approve-spend")

    def test_it_is_a_different_question_from_the_budget_gate(self):
        """`--approve-budget` approves an overrun and says nothing about whether paying is allowed."""
        code, _out, err = self.panel(extra=["--approve-budget"])
        self.assertEqual(code, run_panel.EXIT_BUDGET, err)
        self.assertEqual(self.workspace.calls(), [])

    def test_every_seat_is_still_pending_when_the_spend_refusal_fires(self):
        self.panel()
        self.assertEqual({s["status"] for s in self.manifest()["seats"]}, {"pending"})

    def test_a_smoke_test_is_gated_too_because_it_also_spends(self):
        code, _out, err = self.panel(extra=["--smoke-test", harness.FAST_MODEL])
        self.assertEqual(code, run_panel.EXIT_BUDGET, err)
        self.assertEqual(self.workspace.calls(), [])

    def test_the_connector_block_records_the_endpoint_the_run_billed(self):
        self.panel(extra=["--approve-spend"])
        block = self.manifest()["connector"]
        self.assertEqual(block["name"], harness.CONNECTOR)
        self.assertEqual(block["billing"], "metered")
        self.assertTrue(block["requires_approval"])
        self.assertEqual(block["path"], self.workspace.connector)


class SpendGateTrustedEndpointTest(PanelTestCase):
    """An outer root may switch the gate off for an endpoint it has decided to trust."""

    BILLING = "metered"
    REQUIRES_APPROVAL = False

    def test_a_metered_connector_that_opts_out_needs_no_flag(self):
        code, _out, err = self.panel()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.workspace.calls()), 2)

    def test_the_manifest_records_that_the_gate_was_off_and_which_root_turned_it_off(self):
        """"This machine trusts this endpoint" is a fact about one laptop, and a reader of the run
        should not have to diff two directories to find it."""
        self.panel()
        block = self.manifest()["spend_approval"]
        self.assertEqual(block["billing"], "metered")
        self.assertFalse(block["required"])
        self.assertEqual(block["gate_disabled_path"], self.workspace.connector)
        self.assertEqual(block["gate_disabled_by"], os.path.dirname(self.workspace.connector),
                         "the root the file came from, so a reader can see which tier trusts it")


class SpendGateOnTheJudgmentCallTest(unittest.TestCase):
    """`reconcile.py`'s synthesis call is a paid call, so it is behind the same gate.

    A reconcile typed on its own, hours after the panel, is the case this exists for: the panel's
    own approval covered the seats it dispatched, and this is a new call on the same endpoint.
    """

    def setUp(self):
        self.workspace = harness.Workspace(billing="metered")
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.run_dir = self.workspace.path("reviews", RUN_ID)
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, _out, err = _capture(run_panel.main, [
            "--panel", self.workspace.panel, "--artifact", self.workspace.artifact,
            "--ref", self.workspace.reference, "--out", self.run_dir,
            "--config", self.workspace.config, "--models", self.workspace.registry,
            "--tier", "standard", "--autonomous", "--reconcile", "off", "--approve-spend"])
        self.assertEqual(code, 0, err)
        self.workspace.reset_calls()

    def judge(self, extra=None):
        self.workspace.plan({harness.SLOW_MODEL: [{"body": self._patch()}],
                             harness.FAST_MODEL: [{"body": self._patch()}]})
        return _capture(reconcile.main, [
            "--run-dir", self.run_dir, "--config", self.workspace.config,
            "--models", self.workspace.registry, "--reconciler", "synthesis", "--autonomous",
            "--approve-budget"] + list(extra or []))

    def _patch(self):
        return {
            "schema_version": "1", "run_id": RUN_ID, "author": "synthesis",
            "generated_at": "2026-09-19T09:00:00-04:00",
            "dispositions": [{"cluster": "P-1", "disposition": "flag-for-human",
                              "disposition_reason": "Both seats file it as a design fork."}],
            "method_caveat": "Two families reported; no reference was withheld.",
        }

    def forget_the_approval(self):
        """Make this a run that carries no yes at all: the shape of a manifest written before the
        gate existed, or of a run whose seats went out on a connector that needed no approval and
        has since been tightened. It is the only state in which nothing can satisfy the gate."""
        manifest = runs_lib.read_manifest(self.run_dir)
        manifest["spend_approval"] = None
        runs_lib.write_manifest(self.run_dir, manifest)

    def judge_record(self):
        return runs_lib.read_manifest(self.run_dir)["judge"]

    def test_it_refuses_with_exit_four_and_makes_no_call(self):
        self.forget_the_approval()
        code, _out, err = self.judge()
        self.assertEqual(code, reconcile.EXIT_BUDGET, err)
        self.assertEqual(self.workspace.calls(), [], "no judgment call was made")
        self.assertIn("--approve-spend", err)
        self.assertIn(harness.CONNECTOR, err)
        self.assertIn("no yes anywhere to honour", err,
                      "the refusal says the run's own record was consulted and was empty")
        self.assertFalse(os.path.isfile(os.path.join(self.run_dir, "reconciliation.json")))

    def test_it_refuses_rather_than_prompting_even_with_a_tty(self):
        """`run_panel.py` is the only place in this skill that reads a tty. A reconcile typed into
        a pipe has a stdin that says nothing about whether a person is waiting."""
        self.forget_the_approval()
        code, _out, _err = self.judge(extra=[])
        self.assertEqual(code, reconcile.EXIT_BUDGET)

    def test_a_refusal_recorded_on_the_run_is_not_a_yes(self):
        """`granted: false` is an answer, and the answer is no."""
        manifest = runs_lib.read_manifest(self.run_dir)
        manifest["spend_approval"] = dict(manifest["spend_approval"], granted=False,
                                          source="declined-interactively")
        runs_lib.write_manifest(self.run_dir, manifest)
        code, _out, _err = self.judge()
        self.assertEqual(code, reconcile.EXIT_BUDGET)
        self.assertEqual(self.workspace.calls(), [])

    def test_the_flag_lets_the_judgment_call_through(self):
        code, _out, err = self.judge(extra=["--approve-spend"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.workspace.calls()), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "reconciliation.json")))
        self.assertEqual(self.judge_record()["spend_approval_source"], "--approve-spend")

    def test_the_runs_own_recorded_yes_is_honoured_rather_than_asked_for_twice(self):
        """The panel recorded a person's yes for this run and this endpoint. A reconcile pointed at
        that run directory reads it: re-asking would mean a run cannot finish its own judgment."""
        code, _out, err = self.judge()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.workspace.calls()), 1, "the judgment call was made")
        self.assertEqual(self.judge_record()["spend_approval_source"], "manifest",
                         "and the manifest says which yes let it happen")
        self.assertTrue(runs_lib.read_manifest(self.run_dir)["spend_approval"]["granted"])


class MeteredPipelineTest(unittest.TestCase):
    """The one-command autonomous run, on the shipped connector's own billing posture.

    The gap this closes cost a whole panel: `--approve-spend` was not forwarded to the reconcile
    child, so `--autonomous --approve-spend` dispatched and billed every seat and then exited 4 at
    the judgment call, on the grounds that nobody had said it could spend — in the same invocation
    that had just recorded a person saying so. Every spend-gate test until this one ran the panel
    with `--reconcile off`, and the one pipeline test ran on a `free` connector, so nothing crossed
    the two.
    """

    def setUp(self):
        self.workspace = harness.Workspace(billing="metered")
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.run_dir = self.workspace.path("reviews", RUN_ID)
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}, {"body": self._patch()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })

    def _patch(self):
        return {
            "schema_version": "1", "run_id": RUN_ID, "author": "synthesis",
            "generated_at": "2026-09-19T09:00:00-04:00",
            "dispositions": [{"cluster": "P-1", "disposition": "flag-for-human",
                              "disposition_reason": "Both seats file it as a design fork."}],
            "method_caveat": "Two families reported; no reference was withheld.",
        }

    def panel(self, extra=None):
        return _capture(run_panel.main, [
            "--panel", self.workspace.panel, "--artifact", self.workspace.artifact,
            "--ref", self.workspace.reference, "--out", self.run_dir,
            "--config", self.workspace.config, "--models", self.workspace.registry,
            "--tier", "standard", "--autonomous"] + list(extra or []))

    def test_one_command_reconciles_itself_on_a_metered_connector(self):
        code, out, err = self.panel(extra=["--approve-spend"])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "reconciliation.json")), out)
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "reconciliation.md")))
        manifest = runs_lib.read_manifest(self.run_dir)
        self.assertEqual(manifest["spend_approval"]["source"], "--approve-spend")
        self.assertEqual(manifest["judge"]["spend_approval_source"], "--approve-spend",
                         "the child inherited the flag rather than falling back to the record")
        self.assertEqual(len(self.workspace.calls()), 3, "two seats and one judgment call")

    def test_without_the_flag_nothing_is_dispatched_at_all(self):
        code, _out, err = self.panel()
        self.assertEqual(code, run_panel.EXIT_BUDGET, err)
        self.assertEqual(self.workspace.calls(), [])
        self.assertFalse(os.path.isfile(os.path.join(self.run_dir, "reconciliation.json")))

    def test_the_printed_host_line_does_not_carry_a_pre_typed_approval(self):
        """`--reconcile off` prints a command for a person to run. The run's own recorded yes is
        what lets that command spend; handing them a flag to paste would teach the wrong habit."""
        code, out, err = self.panel(extra=["--approve-spend", "--reconcile", "off"])
        self.assertEqual(code, 0, err)
        printed = [line for line in out.splitlines() if "reconcile.py --run-dir" in line]
        self.assertTrue(printed, out)
        self.assertNotIn("--approve-spend", printed[0])


# --- the user tier in a run's record --------------------------------------------------------------------

class UserTierInTheManifestTest(PanelTestCase):

    def test_the_search_order_the_user_root_and_its_presence_are_all_recorded(self):
        code, _out, err = self.panel()
        self.assertEqual(code, 0, err)
        roots = self.manifest()["roots"]
        self.assertEqual(len(roots["search"]), 3)
        self.assertEqual(roots["search"][1], self.workspace.user_root())
        self.assertEqual(roots["user_root"], self.workspace.user_root())
        self.assertFalse(roots["user_root_present"])
        self.assertEqual(roots["user_overrides"], [])

    def test_a_user_tier_override_is_recorded_apart_from_a_project_one(self):
        self.workspace.user_override("agents/lens-consistency.md", text=(
            "---\nname: lens-consistency\nmodel: standard\neffort: standard\noutput_type: json_report\n"
            "context:\n  - finding-schema.md\n---\n\n# Role\n\nFrom the user tier.\n"))
        code, _out, err = self.panel()
        self.assertEqual(code, 0, err)
        roots = self.manifest()["roots"]
        self.assertTrue(roots["user_root_present"])
        self.assertIn("persona:lens-consistency", roots["user_overrides"])
        self.assertNotIn("persona:lens-consistency", roots["workspace_overrides"])
        self.assertEqual(roots["files"]["persona:lens-consistency"], self.workspace.user_root())


# --- what a refresh may write --------------------------------------------------------------------------

class RefreshWritesFactsOnlyTest(unittest.TestCase):
    """`refresh_models.py` owns the catalogue's facts and nothing a person decided."""

    MODEL = "test/refreshable"

    CHOICES = {
        "connector": "fake",
        "family": "kimi",
        "tiers": ["standard"],
        "effort": {"light": "low", "standard": "high", "deep": "max"},
        "output_token_prior": 12345,
        "prior_source": "measured — a frozen run",
        "min_max_tokens": 64000,
        "measured_output_price": 1.5e-05,
        "measured_output_price_source": "a frozen run",
    }

    FACTS_BEFORE = {
        "input_price_per_token": 1e-09,
        "output_price_per_token": 2e-09,
        "context_limit": 1000,
        "effort_vocabulary": ["high", "low"],
    }

    CATALOGUE = {MODEL: {
        "id": MODEL,
        "pricing": {"prompt": "0.0000123", "completion": "0.0000456"},
        "context_length": 424242,
        "reasoning": {"supported_efforts": ["max", "high", "low"]},
    }}

    def test_every_fact_moves_and_no_choice_does(self):
        before = dict(self.CHOICES, id=self.MODEL, **self.FACTS_BEFORE)
        models = {self.MODEL: dict(before)}
        changes, unknown, added = refresh_models.refresh(models, self.CATALOGUE)
        after = models[self.MODEL]

        self.assertEqual(unknown, [])
        self.assertEqual(added, [])
        self.assertEqual(sorted({change["field"] for change in changes}),
                         sorted(registry_lib.FACT_FIELDS))
        self.assertEqual(after["input_price_per_token"], 1.23e-05)
        self.assertEqual(after["output_price_per_token"], 4.56e-05)
        self.assertEqual(after["context_limit"], 424242)
        self.assertEqual(after["effort_vocabulary"], ["max", "high", "low"])
        for field, value in self.CHOICES.items():
            self.assertEqual(after[field], value,
                             "{0} is a decision a person made, not a catalogue fact".format(field))

    def test_the_effort_map_survives_a_vocabulary_that_moved_under_it(self):
        """The vocabulary grew a `max` rung and the map still says `standard` is `high`. Widening
        the map is a decision, and a refresh that made it would move every seat's depth."""
        models = {self.MODEL: dict(self.CHOICES, id=self.MODEL, **self.FACTS_BEFORE)}
        refresh_models.refresh(models, self.CATALOGUE)
        self.assertEqual(models[self.MODEL]["effort"], self.CHOICES["effort"])

    def test_an_added_model_gets_the_catalogues_facts_and_null_choices(self):
        models = {}
        _changes, _unknown, added = refresh_models.refresh(
            models, self.CATALOGUE, add=[self.MODEL], connector="fake")
        self.assertEqual(added, [self.MODEL])
        entry = models[self.MODEL]
        self.assertEqual(entry["connector"], "fake", "the endpoint it was found on is knowable")
        for field in ("family", "tiers", "effort", "min_max_tokens", "measured_output_price"):
            self.assertIsNone(entry[field], "{0} is a decision the catalogue cannot make".format(field))
        self.assertEqual(entry["input_price_per_token"], 1.23e-05)
        self.assertEqual(entry["output_token_prior"], refresh_models.DEFAULT_PRIOR)

    def test_it_writes_each_model_back_into_the_root_that_held_it(self):
        workspace = harness.Workspace()
        self.addCleanup(workspace.close)
        paths = paths_lib.Paths(workspace.root, user=workspace.user_root())
        workspace.user_override("models/moonshotai__kimi-k3.json", {"effort": {"standard": "max"}})
        registry = registry_lib.load(paths)
        self.assertEqual(
            registry.file_for("moonshotai/kimi-k3"),
            os.path.join(workspace.user_root(), "models", "moonshotai__kimi-k3.json"),
            "the outermost layer is where a refresh writes")
        self.assertTrue(registry.file_for("x-ai/grok-4.6").startswith(paths_lib.SKILL_DIR),
                        "a model no outer root touched is refreshed in the package")

    def test_a_refresh_that_changes_nothing_writes_nothing(self):
        workspace = harness.Workspace()
        self.addCleanup(workspace.close)
        registry = registry_lib.load_dir(workspace.registry)
        written = refresh_models.write_back(dict(registry.models), registry.files, workspace.registry)
        self.assertEqual(written, [], "identical bytes are not rewritten, so no mtime moves")


class RefreshKeepsAFragmentAFragmentTest(unittest.TestCase):
    """What lands in the outermost file, which is usually two keys and not a copy of the package's.

    `registry.models` holds the **merged** entries and `registry.files` points at the outermost
    layer. Writing the first into the second turns a user fragment into a full copy on its first
    refresh — and the copy then freezes every fact it absorbed, because the refresh keeps writing to
    the outer file and the package's base layer is never read for that model again. That is the
    deep-merge promise cancelled by the one script the promise exists to survive.
    """

    MODEL = "moonshotai/kimi-k3"
    SLUG = "moonshotai__kimi-k3.json"

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root, user=self.workspace.user_root())
        self.fragment = self.workspace.user_override(
            "models/" + self.SLUG, {"effort": {"standard": "max"}})

    def read(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def catalogue(self, entry, **fields):
        """A catalogue record that agrees with the registry except where `fields` says otherwise."""
        prices = {"prompt": str(fields.get("input", entry["input_price_per_token"])),
                  "completion": str(fields.get("output", entry["output_price_per_token"]))}
        return {self.MODEL: {"id": self.MODEL, "pricing": prices,
                             "context_length": entry["context_limit"],
                             "reasoning": {"supported_efforts": entry["effort_vocabulary"]}}}

    def test_a_user_fragment_survives_a_refresh_that_moved_nothing(self):
        before = self.read(self.fragment)
        registry = registry_lib.load(self.paths)
        models = dict(registry.models)
        changes, _unknown, _added = refresh_models.refresh(
            models, self.catalogue(registry.get(self.MODEL)))
        self.assertEqual([c for c in changes if c["model"] == self.MODEL], [])
        written = refresh_models.write_back(models, registry.files, self.workspace.registry,
                                            changes=changes)
        self.assertNotIn(self.fragment, written)
        self.assertEqual(self.read(self.fragment), before, "byte for byte the file it was")

    def test_a_moved_price_is_the_only_fact_added_to_the_fragment(self):
        registry = registry_lib.load(self.paths)
        models = dict(registry.models)
        changes, _unknown, _added = refresh_models.refresh(
            models, self.catalogue(registry.get(self.MODEL), input=3.3e-06))
        self.assertEqual([c["field"] for c in changes if c["model"] == self.MODEL],
                         ["input_price_per_token"])
        refresh_models.write_back(models, registry.files, self.workspace.registry, changes=changes)

        after = self.read(self.fragment)
        self.assertEqual(after["effort"], {"standard": "max"}, "the choice it was written for")
        self.assertEqual(after["input_price_per_token"], 3.3e-06, "and the fact that moved")
        for field in ("output_price_per_token", "context_limit", "effort_vocabulary",
                      "connector", "family", "tiers", "output_token_prior", "min_max_tokens"):
            self.assertNotIn(field, after,
                             "{0} did not move and is not this file's to hold".format(field))
        # The provenance the refresh stamps goes where the number goes: a price in a file with no
        # date beside it is a number nobody can age.
        self.assertEqual(sorted(after), sorted(["effort", "input_price_per_token",
                                                "price_source", "source", "refreshed_at"]))

    def test_the_package_file_receives_the_price_when_it_is_the_outermost_holder(self):
        registry = registry_lib.load(self.paths)
        models = dict(registry.models)
        other = "x-ai/grok-4.6"
        entry = registry.get(other)
        catalogue = {other: {"id": other, "pricing": {"prompt": "0.0000044",
                                                      "completion": str(entry["output_price_per_token"])},
                             "context_length": entry["context_limit"],
                             "reasoning": {"supported_efforts": entry["effort_vocabulary"]}}}
        changes, _unknown, _added = refresh_models.refresh(models, catalogue)
        written = refresh_models.write_back(models, registry.files, self.workspace.registry,
                                            changes=changes, dry_run=True)
        self.assertEqual(written, [registry.file_for(other)])
        self.assertTrue(written[0].startswith(paths_lib.SKILL_DIR),
                        "no outer root holds that model, so the package is the outermost holder")

    def test_the_merged_entry_is_never_what_lands_in_an_outer_file(self):
        """The regression itself: after a refresh the fragment must not have grown a family, a
        connector, a tier list or a price it never declared and nobody chose to put there."""
        registry = registry_lib.load(self.paths)
        models = dict(registry.models)
        changes, _unknown, _added = refresh_models.refresh(
            models, self.catalogue(registry.get(self.MODEL), output=9.9e-06))
        refresh_models.write_back(models, registry.files, self.workspace.registry, changes=changes)
        after = self.read(self.fragment)
        self.assertNotIn("family", after)
        self.assertNotIn("tiers", after)
        self.assertNotIn("connector", after)
        # And the merge still answers with the package's facts for everything the fragment is silent
        # about, which is the promise the whole-file write was cancelling.
        merged = registry_lib.load(paths_lib.Paths(self.workspace.root,
                                                   user=self.workspace.user_root())).get(self.MODEL)
        self.assertEqual(merged["family"], "kimi")
        self.assertEqual(merged["effort"]["standard"], "max")


# --- a contested tier cell ------------------------------------------------------------------------------

class TierCellOverrideTest(unittest.TestCase):
    """Two model files, one cell: the outermost root that declares `tiers` wins it.

    The alternative was two edits for what the old written map did in one — drop your model file,
    *and* write `"tiers": []` into an outer copy of the packaged model's file to get its cell out of
    the way. The cascade already answers this question for every other key, and this is the same
    answer one level down.
    """

    PACKAGED = "z-ai/glm-5.3-flash"          # holds glm@standard and glm@fast in the package
    MINE = "acme/one"

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(self.workspace.root, user=self.workspace.user_root())

    def mine(self, root="user", **fields):
        record = dict({
            "id": self.MINE, "connector": "openrouter", "family": "glm", "tiers": ["standard"],
            "effort": {"light": "low", "standard": "high", "deep": "max"},
            "effort_vocabulary": ["max", "high", "low"],
            "input_price_per_token": 1e-06, "output_price_per_token": 2e-06,
            "context_limit": 1000, "output_token_prior": 100}, **fields)
        write = self.workspace.user_override if root == "user" else self.workspace.override
        return write("models/acme__one.json", record)

    def derive(self, registry=None):
        registry = registry or registry_lib.load(self.paths)
        config, _path = self.paths.config()
        return registry_lib.derive_tiers(
            registry, config.get("family_order"), config.get("tier_order"), connector="openrouter")

    def test_a_user_model_file_takes_the_cell_from_the_packaged_one(self):
        self.mine()
        tiers = self.derive()
        self.assertEqual(tiers["standard"]["glm"], self.MINE, "the outer declaration wins")
        self.assertEqual(tiers["fast"]["glm"], self.PACKAGED,
                         "and takes only the cell it declared: the packaged model keeps the rest")

    def test_the_manifest_names_the_model_that_was_displaced(self):
        self.mine()
        self.derive()
        overrides = self.paths.roots_block()["tier_map_overrides"]
        self.assertEqual(overrides, [{"tier": "standard", "family": "glm", "model": self.MINE,
                                      "displaced": self.PACKAGED,
                                      "root": self.workspace.user_root()}])

    def test_a_project_file_outranks_a_user_one(self):
        self.mine(root="user")
        self.workspace.override("models/acme__two.json", {
            "id": "acme/two", "connector": "openrouter", "family": "glm", "tiers": ["standard"],
            "effort": {"light": "low", "standard": "high", "deep": "max"},
            "effort_vocabulary": ["max", "high", "low"],
            "input_price_per_token": 1e-06, "output_price_per_token": 2e-06,
            "context_limit": 1000, "output_token_prior": 100})
        self.assertEqual(self.derive()["standard"]["glm"], "acme/two")
        # Three files, one cell: every record names the model that actually took it, not the one
        # that held it for the length of one loop iteration.
        overrides = self.paths.roots_block()["tier_map_overrides"]
        self.assertEqual({r["model"] for r in overrides}, {"acme/two"})
        self.assertEqual({r["displaced"] for r in overrides}, {self.PACKAGED, self.MINE})

    def test_a_fragment_that_sets_only_effort_claims_no_cell(self):
        """A user file with one rung in it is a choice about depth, not about seating, and the
        packaged model it merges into keeps every cell it had."""
        self.workspace.user_override("models/z-ai__glm-5.3-flash.json",
                                     {"effort": {"standard": "max"}})
        tiers = self.derive()
        self.assertEqual(tiers["standard"]["glm"], self.PACKAGED)
        self.assertEqual(self.paths.roots_block()["tier_map_overrides"], [])

    def test_a_run_records_the_override_in_its_manifest(self):
        """End to end, because `roots` is written from the run's own `Paths` and the derivation
        happens two modules away from it."""
        workspace = harness.Workspace()
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}],
                        harness.FAST_MODEL: [{"body": harness.valid_report()}],
                        harness.THIRD_MODEL: [{"body": harness.valid_report()}]})
        # The workspace registry is an operator directory, so the contest is staged inside it: two
        # files at two roots is what the cascade tests above cover, and this one is about the record.
        run_dir = workspace.path("reviews", RUN_ID)
        code, _out, err = _capture(run_panel.main, [
            "--panel", workspace.panel, "--artifact", workspace.artifact, "--out", run_dir,
            "--config", workspace.config, "--models", workspace.registry,
            "--tier", "standard", "--autonomous", "--reconcile", "off"])
        self.assertEqual(code, 0, err)
        self.assertEqual(runs_lib.read_manifest(run_dir)["roots"]["tier_map_overrides"], [],
                         "an uncontested map records an empty list rather than nothing at all")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Seat resolution: the tier order, the family constraints, and the two re-seat paths.

No network and no paid call. Most of this is `lib/seating.py` on its own, because the orderings are
the contract; the runtime re-seat runs a whole panel through `run_panel.py` against the scripted
connector, because the thing being tested is what the script does with a 404.

    python3 scripts/tests/test_seating.py
"""

import io
import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import harness  # noqa: E402
import run_panel  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402
from lib import runs as runs_lib  # noqa: E402
from lib import seating as seating_lib  # noqa: E402


def entry_with(tiers, default_tier=None, effort=None):
    config = {"type": "openai_compat", "tiers": tiers}
    if default_tier:
        config["default_tier"] = default_tier
    if effort:
        config["effort"] = effort
    return config


def panel_with(seats, tier=None, name="test-panel"):
    panel = {"name": name, "seats": list(seats)}
    if tier:
        panel["tier"] = tier
    return panel


# --- the tier order ---------------------------------------------------------------------------------

class TierOrderTest(unittest.TestCase):
    """`--model` → `--tier` → the seat → the panel → the config → the persona frontmatter.

    Every level names a different tier in this fixture, so each assertion below is a claim about
    precedence rather than about a value that two levels happen to share.
    """

    LEVELS = ("cli", "seat", "panel", "config", "persona", seating_lib.DEFAULT_TIER)

    def setUp(self):
        self.tiers = {name: {"kimi": "model/" + name} for name in self.LEVELS}

    def resolve(self, seat_tier=None, panel_tier=None, default_tier=None, cli_tier=None,
                frontmatter=None, pinned=None):
        seat = {"lens": "consistency", "family": "kimi"}
        if seat_tier:
            seat["tier"] = seat_tier
        seats = seating_lib.resolve(
            panel_with([seat], tier=panel_tier),
            entry_with(self.tiers, default_tier=default_tier),
            cli_tier=cli_tier, pinned=pinned,
            frontmatter_fn=lambda _lens: frontmatter)
        return seats[0]

    def test_cli_model_pins_the_seat_and_beats_everything(self):
        seat = self.resolve(seat_tier="seat", panel_tier="panel", default_tier="config", cli_tier="cli",
                            pinned={"consistency-kimi": "pinned/model"})
        self.assertEqual(seat["model"], "pinned/model")
        self.assertEqual(seat["tier_source"], "--model")

    def test_cli_tier_beats_the_seat_the_panel_and_the_config(self):
        seat = self.resolve(seat_tier="seat", panel_tier="panel", default_tier="config", cli_tier="cli")
        self.assertEqual((seat["tier"], seat["tier_source"], seat["model"]), ("cli", "--tier", "model/cli"))

    def test_the_seats_own_tier_beats_the_panel_and_the_config(self):
        seat = self.resolve(seat_tier="seat", panel_tier="panel", default_tier="config")
        self.assertEqual((seat["tier"], seat["tier_source"], seat["model"]), ("seat", "seat", "model/seat"))

    def test_the_panels_tier_beats_the_config(self):
        seat = self.resolve(panel_tier="panel", default_tier="config")
        self.assertEqual((seat["tier"], seat["tier_source"], seat["model"]), ("panel", "panel", "model/panel"))

    def test_the_configs_default_tier_beats_the_persona_frontmatter(self):
        """The inversion of framework §8: the tier is a property of the run's stakes, not the lens."""
        seat = self.resolve(default_tier="config", frontmatter={"model": "persona"})
        self.assertEqual((seat["tier"], seat["tier_source"], seat["model"]), ("config", "config", "model/config"))

    def test_the_persona_frontmatter_is_the_last_source_that_can_decide(self):
        seat = self.resolve(frontmatter={"model": "persona"})
        self.assertEqual((seat["tier"], seat["tier_source"], seat["model"]), ("persona", "persona", "model/persona"))

    def test_a_frontmatter_model_that_names_no_tier_contributes_nothing(self):
        """A value naming no key in this config's tier map — `model: high`, what the personas carried through stage 2b."""
        seat = self.resolve(frontmatter={"model": "high"})
        self.assertEqual((seat["tier"], seat["tier_source"]), (seating_lib.DEFAULT_TIER, "default"))

    def test_a_pin_naming_a_seat_the_panel_does_not_have_is_a_composition_error(self):
        with self.assertRaises(seating_lib.SeatingError) as caught:
            self.resolve(pinned={"fidelity-openai": "x/y"})
        self.assertIn("fidelity-openai", str(caught.exception))
        self.assertIn("consistency-kimi", str(caught.exception), "the message lists the seats there are")


class ModelPinParsingTest(unittest.TestCase):

    def test_repeatable_seat_equals_model(self):
        self.assertEqual(
            run_panel.parse_model_pins(["fidelity-openai=openai/gpt-6-astra", "consistency-kimi=x/y"]),
            {"fidelity-openai": "openai/gpt-6-astra", "consistency-kimi": "x/y"})

    def test_a_value_with_no_equals_sign_is_refused(self):
        with self.assertRaises(seating_lib.SeatingError):
            run_panel.parse_model_pins(["openai/gpt-6-astra"])


# --- family constraints ------------------------------------------------------------------------------

DECLARED = ("claude", "openai", "glm", "kimi", "xai")


def five_family_entry():
    return entry_with({"standard": {family: "m/" + family for family in DECLARED}}, default_tier="standard")


class ConstraintTest(unittest.TestCase):

    def assignment(self, seats):
        resolved = seating_lib.resolve(panel_with(seats), five_family_entry())
        return {seat["lens"]: seat["family"] for seat in resolved}

    def test_named_non_claude_and_distinct_resolve_the_same_whatever_the_template_order(self):
        seats = [
            {"lens": "fidelity", "family": "openai"},
            {"lens": "buildability", "family": "non-claude"},
            {"lens": "adversarial", "family": "distinct"},
        ]
        expected = {"fidelity": "openai", "buildability": "glm", "adversarial": "claude"}
        for order in ([0, 1, 2], [2, 1, 0], [1, 2, 0], [2, 0, 1]):
            self.assertEqual(self.assignment([seats[i] for i in order]), expected,
                             "template order {0} changed the assignment".format(order))

    def test_non_claude_never_takes_claude_and_never_takes_a_held_family(self):
        resolved = seating_lib.resolve(panel_with([
            {"lens": "fidelity", "family": "openai"},
            {"lens": "buildability", "family": "non-claude"},
            {"lens": "consistency", "family": "non-claude"},
        ]), five_family_entry())
        families = [seat["family"] for seat in resolved]
        self.assertEqual(families, ["openai", "glm", "kimi"])
        self.assertNotIn("claude", families)

    def test_distinct_never_duplicates_a_family_another_seat_holds(self):
        resolved = seating_lib.resolve(panel_with([
            {"lens": "fidelity", "family": "distinct"},
            {"lens": "buildability", "family": "distinct"},
            {"lens": "consistency", "family": "distinct"},
            {"lens": "adversarial", "family": "kimi"},
        ]), five_family_entry())
        families = [seat["family"] for seat in resolved]
        self.assertEqual(len(set(families)), 4, families)
        self.assertNotIn(None, families)
        self.assertEqual([seat["substitution"] for seat in resolved], [None] * 4)

    def test_distinct_reserves_against_a_named_seat_that_comes_after_it(self):
        """`distinct` resolves last whatever the template says, so it reserves in both directions."""
        resolved = seating_lib.resolve(panel_with([
            {"lens": "fidelity", "family": "distinct"},
            {"lens": "buildability", "family": "claude"},
        ]), five_family_entry())
        self.assertEqual(resolved[0]["family"], "openai", "claude was taken by a seat further down")
        self.assertEqual(resolved[1]["family"], "claude")

    def test_an_unsatisfiable_constraint_falls_back_and_records_it(self):
        two = entry_with({"standard": {"claude": "m/claude", "openai": "m/openai"}}, default_tier="standard")
        resolved = seating_lib.resolve(panel_with([
            {"lens": "fidelity", "family": "openai"},
            {"lens": "buildability", "family": "distinct"},
            {"lens": "consistency", "family": "distinct"},
        ]), two)
        self.assertIsNone(resolved[0]["substitution"], "the named seat is satisfied")
        self.assertEqual(resolved[1]["family"], "claude", "the first distinct still finds a free family")
        self.assertIsNone(resolved[1]["substitution"])

        stranded = resolved[2]
        self.assertIsNotNone(stranded["substitution"], "the run does not fail; the seat is substituted")
        self.assertEqual(stranded["substitution"]["kind"], "constraint_unsatisfied")
        self.assertEqual(stranded["substitution"]["requested"], "distinct")
        self.assertEqual(stranded["substitution"]["resolved"], stranded["family"])
        self.assertEqual(stranded["family"], "openai",
                         "a fallback is a re-seat, so it doubles up on a non-claude family rather "
                         "than taking the one free family, which is claude")

    def test_an_unsatisfiable_non_claude_still_prefers_a_non_claude_family(self):
        two = entry_with({"standard": {"claude": "m/claude", "openai": "m/openai"}}, default_tier="standard")
        resolved = seating_lib.resolve(panel_with([
            {"lens": "fidelity", "family": "openai"},
            {"lens": "buildability", "family": "non-claude"},
        ]), two)
        self.assertEqual(resolved[1]["family"], "openai",
                         "the fallback keeps the constraint's own predicate where it can")
        self.assertEqual(resolved[1]["substitution"]["kind"], "constraint_unsatisfied")

    def test_the_reviewer_id_of_a_constrained_seat_comes_from_the_constraint_not_the_family(self):
        """It has to be stable across runs: a constrained seat's family depends on what else is seated."""
        resolved = seating_lib.resolve(panel_with([
            {"lens": "buildability", "family": "non-claude"},
            {"lens": "adversarial", "family": "distinct"},
        ]), five_family_entry())
        self.assertEqual([seat["reviewer_id"] for seat in resolved],
                         ["buildability-non-claude", "adversarial-distinct"])
        self.assertEqual([seat["family"] for seat in resolved], ["openai", "claude"])


class MissingCellTest(unittest.TestCase):
    """A family with no model at the resolved tier is unreachable, and takes the re-seat-once path."""

    def setUp(self):
        self.paths = paths_lib.Paths(os.devnull + "-no-such-workspace")
        config, _path = self.paths.config()
        self.entry = harness.shipped()["entry"]

    def test_google_at_frontier_re_seats_and_records_the_substitution(self):
        """The spec's own worked example: the shipped config leaves the Google frontier cell empty."""
        self.assertNotIn("google", self.entry["tiers"]["frontier"],
                         "the shipped config must leave google's frontier cell empty")
        resolved = seating_lib.resolve(
            panel_with([{"lens": "fidelity", "family": "google"}]), self.entry, cli_tier="frontier")
        seat = resolved[0]
        self.assertEqual(seat["reviewer_id"], "fidelity-google", "the id names what the seat asked for")
        self.assertNotEqual(seat["family"], "google")
        self.assertEqual(seat["family"], "openai",
                         "claude is first in declaration order and is excluded from every re-seat, "
                         "so the seat lands on the first non-claude family instead")
        self.assertEqual(seat["model"], "openai/gpt-6-astra")
        self.assertEqual(seat["substitution"]["kind"], "missing_cell")
        self.assertEqual(seat["substitution"]["requested"], "google")
        self.assertEqual(seat["substitution"]["resolved"], "openai")

    def test_the_two_orders_of_a_missing_cell_and_a_named_claude_seat_agree(self):
        """Named families reserve across the whole template before anything re-seats.

        Resolving the missing cell inside pass 1 let `[google, claude]` put two seats on claude while
        openai sat free, and `[claude, google]` re-seat onto openai — the same panel, two answers,
        decided by template order.
        """
        google = {"lens": "fidelity", "family": "google"}
        claude = {"lens": "adversarial", "family": "claude"}
        for order in ([google, claude], [claude, google]):
            resolved = seating_lib.resolve(panel_with(order), self.entry, cli_tier="frontier")
            assignment = {seat["lens"]: seat["family"] for seat in resolved}
            self.assertEqual(assignment, {"fidelity": "openai", "adversarial": "claude"},
                             "template order {0}".format([s["family"] for s in order]))
            families = [seat["family"] for seat in resolved]
            self.assertEqual(len(set(families)), len(families),
                             "two seats on one family while a free family exists")

    def test_no_re_seat_lands_on_claude_unless_the_seat_asked_for_claude(self):
        """Owner ruling, 2026-09-18. Claude is first in declaration order, so without the rule every
        first re-seat lands on it — and the default panel seats no Claude family on purpose."""
        resolved = seating_lib.resolve(panel_with([
            {"lens": "fidelity", "family": "google"},
            {"lens": "buildability", "family": "google"},
            {"lens": "consistency", "family": "google"},
        ]), self.entry, cli_tier="frontier")
        families = [seat["family"] for seat in resolved]
        self.assertNotIn("claude", families, "no re-seat may add a Claude seat nobody asked for")
        self.assertEqual(families, ["openai", "kimi", "glm"])
        for seat in resolved:
            self.assertEqual(seat["substitution"]["kind"], "missing_cell")

    def test_a_seat_that_asked_for_claude_may_still_be_re_seated_onto_claude(self):
        """The exclusion is about substitutions nobody chose, not about the claude family itself."""
        fast_only_claude = {"tiers": {"t": {"claude": "anthropic/claude-sonnet-5"}}, "type": "openai_compat"}
        resolved = seating_lib.resolve(
            panel_with([{"lens": "fidelity", "family": "claude"}]), fast_only_claude, cli_tier="t")
        self.assertEqual(resolved[0]["family"], "claude")
        self.assertIsNone(resolved[0]["substitution"])

    def test_a_tier_with_nothing_but_claude_refuses_a_seat_that_needs_re_seating(self):
        only_claude = {"tiers": {"t": {"claude": "anthropic/claude-sonnet-5"}}, "type": "openai_compat"}
        with self.assertRaises(seating_lib.SeatingError) as caught:
            seating_lib.resolve(panel_with([{"lens": "fidelity", "family": "google"}]), only_claude, cli_tier="t")
        self.assertIn("no other family to re-seat onto", str(caught.exception))

    def test_google_at_standard_has_a_cell_and_is_not_re_seated(self):
        resolved = seating_lib.resolve(
            panel_with([{"lens": "fidelity", "family": "google"}]), self.entry, cli_tier="standard")
        self.assertEqual(resolved[0]["family"], "google")
        self.assertIsNone(resolved[0]["substitution"])

    def test_a_missing_cell_does_not_take_a_family_another_seat_already_holds(self):
        resolved = seating_lib.resolve(panel_with([
            {"lens": "fidelity", "family": "claude"},
            {"lens": "buildability", "family": "google"},
        ]), self.entry, cli_tier="frontier")
        self.assertEqual(resolved[1]["family"], "openai")
        self.assertEqual(resolved[1]["substitution"]["kind"], "missing_cell")

    def test_a_pinned_model_is_never_re_seated_over_a_missing_cell(self):
        resolved = seating_lib.resolve(
            panel_with([{"lens": "fidelity", "family": "google"}]), self.entry, cli_tier="frontier",
            pinned={"fidelity-google": "google/gemini-3.8-flash"})
        self.assertEqual(resolved[0]["family"], "google")
        self.assertEqual(resolved[0]["model"], "google/gemini-3.8-flash")
        self.assertIsNone(resolved[0]["substitution"])


# --- the shipped files must agree with each other ------------------------------------------------------

class ShippedConfigTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shipped = harness.shipped()
        cls.paths = shipped["paths"]
        cls.entry = shipped["entry"]
        cls.connector = shipped["connector"]
        cls.registry = shipped["registry"]

    def test_the_registry_prices_every_model_the_config_names_at_any_tier(self):
        """The two files cannot drift: an unpriced seat is a budget gate that does not gate, and the
        registry gate would refuse a run the shipped config composed."""
        named = []
        for cells in self.entry["tiers"].values():
            for model in cells.values():
                if model and model not in named:
                    named.append(model)
        self.assertEqual(self.registry.covers(named), [],
                         "every model the shipped config names must be in the shipped registry, priced")

    def test_every_shipped_model_with_a_ladder_binds_all_three_levels_inside_its_own_vocabulary(self):
        """The effort gate, run over the whole shipped registry rather than over one panel.

        Every model file that records an effort ladder has to answer `light`, `standard` and `deep`
        with a word its own vocabulary contains, or the first run that asks for that level on that
        model is a composition error nobody saw coming.

        A model with **no** recorded vocabulary is the separate case the next test covers: there is
        no ladder to index, so there is nothing to bind and nothing to send.
        """
        for model in sorted(self.registry.models):
            entry = self.registry.get(model)
            if not entry.get("effort_vocabulary"):
                continue
            for level in registry_lib.EFFORT_LEVELS:
                kind, value = registry_lib.effort_binding(entry, level, model)
                self.assertEqual(kind, "word", "{0} binds {1} to something other than a rung".format(model, level))
                self.assertEqual(registry_lib.effort_is_supported(entry, value), True,
                                 "effort {0!r} on {1} is outside {2}".format(
                                     value, model, entry.get("effort_vocabulary")))

    def test_a_model_with_no_ladder_is_sent_no_reasoning_parameter_and_says_why(self):
        """The harness models. Effort is set in an installed agent file and there is no per-spawn
        parameter, so the file records neither a vocabulary nor a map — and the binding has to come
        back `none` rather than raising, or a draft run would fail its own effort gate."""
        laddered = [m for m in self.registry.models if self.registry.get(m).get("effort_vocabulary")]
        bare = [m for m in self.registry.models if not self.registry.get(m).get("effort_vocabulary")]
        self.assertTrue(laddered, "the shipped registry has models with ladders")
        for model in sorted(bare):
            entry = self.registry.get(model)
            self.assertIsNone(entry.get("effort"), "{0} maps levels it has no rungs for".format(model))
            self.assertTrue(entry.get("effort_source"), "{0} does not say why it has no map".format(model))
            for level in registry_lib.EFFORT_LEVELS:
                self.assertEqual(registry_lib.effort_binding(entry, level, model), ("none", None))

    def test_every_model_file_names_a_connector_that_this_package_ships(self):
        """A model nothing serves is a model no run can seat. The connector need not be the run's —
        the harness leg's models name `harness` and are seated only by `--draft` — but it has to be
        a file, or a tier cell resolves to a model with nowhere to go."""
        from lib import connectors as connectors_lib
        available = set(connectors_lib.available(self.paths))
        for model in sorted(self.registry.models):
            name = self.registry.get(model).get("connector")
            self.assertTrue(name, "{0} names no connector, so no endpoint claims it".format(model))
            self.assertIn(name, available,
                          "{0} names connector {1!r} and no root holds that file".format(model, name))

    def test_the_run_connector_serves_every_model_its_own_tier_map_seats(self):
        """The derived map is filtered by connector, so this is the statement that the filter works:
        nothing the default endpoint's map seats is served by a different endpoint."""
        for cells in self.entry["tiers"].values():
            for model in cells.values():
                self.assertEqual(self.registry.get(model).get("connector"), self.connector["name"])

    def test_the_derived_tier_map_is_the_map_the_spec_prints(self):
        """The migration's acceptance condition: the tiers x families map is no longer written
        anywhere, it is derived from the model files' own `family` and `tiers`, and the derivation
        has to reproduce the map the spec prints — key order included, because declaration order is
        what the `non-claude` pass and every re-seat walk."""
        expected = {
            "frontier": {
                "claude": "anthropic/claude-sonnet-5",
                "openai": "openai/gpt-6-astra",
                "kimi": "moonshotai/kimi-k3",
                "glm": "z-ai/glm-5.3",
                "xai": "x-ai/grok-4.6",
                "deepseek": "deepseek/deepseek-v4-pro-0813",
            },
            "standard": {
                "claude": "anthropic/claude-sonnet-5",
                "openai": "openai/gpt-5.6-sol",
                "kimi": "moonshotai/kimi-k3",
                "glm": "z-ai/glm-5.3-flash",
                "xai": "x-ai/grok-4.6",
                "google": "google/gemini-3.8-flash",
                "deepseek": "deepseek/deepseek-v4-pro-0813",
            },
            "fast": {
                "claude": "anthropic/claude-sonnet-5",
                "openai": "openai/gpt-5.6-luna",
                "kimi": "moonshotai/kimi-k3",
                "glm": "z-ai/glm-5.3-flash",
                "xai": "x-ai/grok-4.6",
                "google": "google/gemini-3.6-flash",
                "deepseek": "deepseek/deepseek-v4.1-flash",
            },
        }
        self.assertEqual(self.entry["tiers"], expected)
        self.assertEqual(list(self.entry["tiers"]), list(expected), "tier declaration order")
        for tier in expected:
            self.assertEqual(list(self.entry["tiers"][tier]), list(expected[tier]),
                             "family declaration order at {0} — the order every re-seat walks".format(tier))

    def test_two_models_at_one_root_cannot_claim_one_family_at_one_tier(self):
        """A derived map has a failure mode a written one did not: two files, one cell, one root.

        From two *different* roots this is not an error at all — the outer one wins, which is what
        `test_config_restructure.TierCellOverrideTest` covers. Here both are declared at the same
        layer, so there is no outer and no inner and nothing to prefer.
        """
        registry = registry_lib.Registry({"models": {
            "a/one": {"id": "a/one", "family": "glm", "tiers": ["standard"]},
            "a/two": {"id": "a/two", "family": "glm", "tiers": ["standard"]},
        }}, "inline")
        with self.assertRaises(registry_lib.RegistryError) as caught:
            registry_lib.derive_tiers(registry, ["glm"], ["standard"])
        self.assertIn("a/one", str(caught.exception))
        self.assertIn("a/two", str(caught.exception))

    def test_the_collision_message_says_the_package_is_read_only_and_how_to_empty_a_cell(self):
        """"Drop the tier from one of the two model files" is not actionable against the packaged
        one: the package is read-only during a run and editing it is nobody's fix."""
        registry = registry_lib.Registry({"models": {
            "a/one": {"id": "a/one", "family": "glm", "tiers": ["standard"]},
            "a/two": {"id": "a/two", "family": "glm", "tiers": ["standard"]},
        }}, "inline")
        with self.assertRaises(registry_lib.RegistryError) as caught:
            registry_lib.derive_tiers(registry, ["glm"], ["standard"])
        message = str(caught.exception)
        self.assertIn("read-only", message)
        self.assertIn('"tiers": []', message, "the recipe for emptying a cell from an outer layer")
        self.assertIn("~/.config/ensemble-review/models/", message, "and where to put the file")

    def test_provider_routing_is_keyed_by_concrete_model_id(self):
        seated = {model for cells in self.entry["tiers"].values() for model in cells.values() if model}
        routing = self.connector.get("provider_routing") or {}
        self.assertTrue(routing, "the shipped connector file carries the spec's provider_routing map")
        for model in routing:
            self.assertIn(model, seated)

    def test_the_shipped_panel_resolves_to_the_models_the_spec_tables_name(self):
        with open(self.paths.panel("spec-review"), "r", encoding="utf-8") as handle:
            panel = json.load(handle)

        def frontmatter(lens):
            return report_lib.parse_agent_file(self.paths.persona("lens-" + lens))[0]

        expected = {
            "frontier": {
                "fidelity-openai": "openai/gpt-6-astra",
                "buildability-glm": "z-ai/glm-5.3",
                "consistency-kimi": "moonshotai/kimi-k3",
                "adversarial-xai": "x-ai/grok-4.6",
            },
            "standard": {
                "fidelity-openai": "openai/gpt-5.6-sol",
                "buildability-glm": "z-ai/glm-5.3-flash",
                "consistency-kimi": "moonshotai/kimi-k3",
                "adversarial-xai": "x-ai/grok-4.6",
            },
            "fast": {
                "fidelity-openai": "openai/gpt-5.6-luna",
                "buildability-glm": "z-ai/glm-5.3-flash",
                "consistency-kimi": "moonshotai/kimi-k3",
                "adversarial-xai": "x-ai/grok-4.6",
            },
        }
        for tier, wanted in expected.items():
            seats = seating_lib.resolve(panel, self.entry, cli_tier=tier, frontmatter_fn=frontmatter)
            self.assertEqual({seat["reviewer_id"]: seat["model"] for seat in seats}, wanted, tier)
            self.assertEqual([seat["substitution"] for seat in seats], [None] * 4,
                             "the shipped panel needs no substitution at any tier")
            self.assertEqual(seating_lib.effort_errors(seats, self.registry), [], tier)


# --- the runtime re-seat -------------------------------------------------------------------------------

class RuntimeReseatUnitTest(unittest.TestCase):
    """`reseat()` answers the same question as the composition-time fallback, the same way."""

    def test_a_non_claude_seat_re_seated_at_runtime_never_lands_on_claude(self):
        """It is re-seated against what it *asked for*, not against the family it happened to hold."""
        entry = five_family_entry()
        seats = seating_lib.resolve(panel_with([{"lens": "buildability", "family": "non-claude"}]), entry)
        seat = seats[0]
        self.assertEqual(seat["family"], "openai")

        moved = seating_lib.reseat(seat, seats, entry)
        self.assertIsNotNone(moved)
        self.assertNotEqual(seat["family"], "claude",
                            "the constraint binds at dispatch time as it does at composition time")
        self.assertEqual(seat["family"], "glm")
        self.assertEqual(seat["substitution"]["kind"], "model_unavailable")
        self.assertEqual(seat["substitution"]["requested"], "openai")

    def test_a_named_seat_re_seated_at_runtime_never_lands_on_claude_either(self):
        entry = five_family_entry()
        seats = seating_lib.resolve(panel_with([{"lens": "fidelity", "family": "openai"}]), entry)
        seating_lib.reseat(seats[0], seats, entry)
        self.assertEqual(seats[0]["family"], "glm", "claude is excluded from every re-seat")

    def test_a_claude_seat_re_seated_at_runtime_leaves_claude_behind(self):
        entry = five_family_entry()
        seats = seating_lib.resolve(panel_with([{"lens": "fidelity", "family": "claude"}]), entry)
        seating_lib.reseat(seats[0], seats, entry)
        self.assertEqual(seats[0]["family"], "openai", "the family that failed is the one dropped")

    def test_a_seat_already_substituted_is_not_substituted_again(self):
        entry = five_family_entry()
        seats = seating_lib.resolve(panel_with([{"lens": "fidelity", "family": "openai"}]), entry)
        seating_lib.reseat(seats[0], seats, entry)
        self.assertIsNone(seating_lib.reseat(seats[0], seats, entry), "once is the whole point")


class ModelUnavailableTest(unittest.TestCase):
    """A 404 naming the model is the same condition as a missing cell, and takes the same path."""

    def setUp(self):
        models = harness.default_models(third=True)
        self.workspace = harness.Workspace(
            models=models,
            tiers={"kimi": harness.SLOW_MODEL, "xai": harness.FAST_MODEL, "glm": harness.THIRD_MODEL},
            effort={harness.FAST_MODEL: "high"})
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.run_dir = self.workspace.path("reviews", "2026-09-18-1")

    def run_panel(self, extra=None):
        argv = [
            "--panel", self.workspace.panel,
            "--artifact", self.workspace.artifact,
            "--out", self.run_dir,
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
            "--tier", "standard",
            "--autonomous",
            # The Judge stage is pinned off: these tests are about dispatch, and an autonomous run
            # would otherwise finish the pipeline by asking the fake backend for a judgment patch.
            "--reconcile", "off",
        ] + list(extra or [])
        out, err = io.StringIO(), io.StringIO()
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            code = run_panel.main(argv)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        return code, out.getvalue(), err.getvalue()

    def test_a_refused_model_re_seats_the_lens_once_onto_the_next_family(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"raise": "unavailable"}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
            harness.THIRD_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, out + err)
        self.assertIn("re-seated consistency-kimi", out)

        manifest = runs_lib.read_manifest(self.run_dir)
        seat = next(s for s in manifest["seats"] if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(seat["reviewer_id"], "consistency-kimi", "the id does not move when the family does")
        self.assertEqual(seat["family"], "glm")
        self.assertEqual(seat["model"], harness.THIRD_MODEL)
        self.assertEqual(seat["status"], "ok")
        self.assertEqual(seat["substitution"]["kind"], "model_unavailable")
        self.assertEqual(seat["substitution"]["requested"], "kimi")
        self.assertEqual(seat["substitution"]["resolved"], "glm")
        self.assertEqual(len(self.workspace.calls(harness.THIRD_MODEL)), 1)

    def test_it_re_seats_exactly_once(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"raise": "unavailable"}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
            harness.THIRD_MODEL: [{"raise": "unavailable"}],
        })
        code, out, err = self.run_panel()
        self.assertEqual(code, 3, "the seat is missing and the run is under-seated, not re-seated twice")
        manifest = runs_lib.read_manifest(self.run_dir)
        seat = next(s for s in manifest["seats"] if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(seat["status"], "failed")
        self.assertEqual(seat["substitution"]["resolved"], "glm", "one substitution, not two")
        self.assertEqual(len(self.workspace.calls(harness.THIRD_MODEL)), 1)
        del out, err

    def test_a_resume_dispatches_the_substituted_family_not_the_refused_one(self):
        """Seats re-resolve from the panel every run, and the panel still names the refused family.

        Without adopting the recorded substitution, a resume pays for the refusal again and redoes a
        re-seat the manifest already has — one wasted call per resume.
        """
        self.workspace.plan({
            harness.SLOW_MODEL: [{"raise": "unavailable"}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
            harness.THIRD_MODEL: [{"body": {"verdict": "nonsense", "summary": "x", "findings": []}}],
        })
        self.assertEqual(self.run_panel()[0], 3, "the re-seated seat lands but does not validate")
        manifest = runs_lib.read_manifest(self.run_dir)
        seat = next(s for s in manifest["seats"] if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(seat["substitution"]["resolved"], "glm")

        self.workspace.reset_calls()
        self.workspace.plan({
            harness.SLOW_MODEL: [{"raise": "unavailable"}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
            harness.THIRD_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.run_panel()
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.workspace.calls(harness.SLOW_MODEL), [],
                         "the resume never touches the family the provider already refused")
        self.assertEqual(len(self.workspace.calls(harness.THIRD_MODEL)), 1)
        resumed = next(s for s in runs_lib.read_manifest(self.run_dir)["seats"]
                       if s["reviewer_id"] == "consistency-kimi")
        self.assertEqual(resumed["status"], "ok")
        self.assertEqual(resumed["family"], "glm")
        self.assertEqual(resumed["substitution"]["kind"], "model_unavailable")

    def test_an_ordinary_provider_error_is_not_re_seated(self):
        """Re-seating spends a second seat's money, so it is not a guess about what went wrong."""
        self.workspace.plan({
            harness.SLOW_MODEL: [{"raise": "error"}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, _err = self.run_panel()
        self.assertEqual(code, 3, out)
        manifest = runs_lib.read_manifest(self.run_dir)
        seat = next(s for s in manifest["seats"] if s["reviewer_id"] == "consistency-kimi")
        self.assertIsNone(seat["substitution"])
        self.assertEqual(self.workspace.calls(harness.THIRD_MODEL), [])


class MethodCaveatTest(unittest.TestCase):
    """Every substitution reaches the reconciliation's method caveat, because it changes what the
    agreement counts mean: the panel that ran is not quite the panel that was composed."""

    def test_a_substitution_is_appended_to_the_suppliers_caveat(self):
        manifest = {"seats": [
            {"reviewer_id": "fidelity-google", "substitution": {
                "kind": "missing_cell", "requested": "google", "resolved": "claude",
                "reason": "family 'google' has no model at tier 'frontier'"}},
            {"reviewer_id": "adversarial-xai", "substitution": None},
        ]}
        caveat = core.method_caveat("Four families ran.", manifest)
        self.assertTrue(caveat.startswith("Four families ran."))
        self.assertIn("fidelity-google", caveat)
        self.assertIn("missing_cell", caveat)
        self.assertIn("asked for google and ran on claude", caveat)
        self.assertNotIn("adversarial-xai", caveat)

    def test_a_run_with_no_substitution_leaves_the_caveat_exactly_as_supplied(self):
        self.assertEqual(core.method_caveat("Four families ran.", {"seats": [{"reviewer_id": "a"}]}),
                         "Four families ran.")


if __name__ == "__main__":
    unittest.main(verbosity=2)

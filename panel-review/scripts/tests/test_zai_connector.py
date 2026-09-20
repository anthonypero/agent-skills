#!/usr/bin/env python3
"""The z.ai coding-plan connector: a shipped `subscription` endpoint that is not the harness leg.

Everything here resolves through the **real cascade against the real package**, so what is tested is
what ships. **No network and no paid call**: the one thing this connector is for — calling z.ai — is
the one thing these tests never do. The endpoint facts they encode (the `/models` listing carries no
prices, `reasoning.effort` does not order the reasoning-token count) were measured once, on
2026-09-20, and are recorded in the model file's own `effort_source` and in the v4 spec; a test that
re-measured them would bill the owner's plan on every suite run.

Four claims, one per class:

- **The files load.** The connector and its model file validate through the strict loader and the
  three-root cascade, and the connector points at the *coding* path rather than z.ai's general API —
  the distinction the whole billing posture rests on.
- **A zero price is a price.** `registry.require` accepts an explicit `0` on a subscription
  connector, where a `null` would be the composition error that keeps an unpriced seat out of the
  projection. This is the `claude-opus-5` rule applied to a second, dispatched (non-harness) leg.
- **The tier map is filtered by connector, so nothing is displaced.** The packaged OpenRouter map is
  unchanged, the `zai-coding` map is the single `frontier/glm` cell, and `tier_map_overrides` stays
  empty — the two GLM model files never contest a cell, because the connector filter removes one of
  them first.
- **The spend gate does not ask.** A run whose every dispatched seat lands here is not gated, and the
  gate's manifest block still records the posture.

Plus the refresh guard: a price-less catalogue must leave a subscription model's zero prices alone
rather than nulling them.

    python3 scripts/tests/test_zai_connector.py
"""

import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import harness  # noqa: E402
import refresh_models  # noqa: E402
from backends import openai_compat  # noqa: E402
from lib import connectors as connectors_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402

CONNECTOR = "zai-coding"
MODEL = "glm-5.3"
OPENROUTER_GLM = "z-ai/glm-5.3"

# The path that bills the coding plan. z.ai's general endpoint is the same host one segment shorter
# and refuses the plan token outright, so the segment is load-bearing rather than cosmetic.
CODING_URL = "https://api.z.ai/api/coding/paas/v4"
GENERAL_URL = "https://api.z.ai/api/paas/v4"


def _shipped():
    """The package's own answer, with both outer roots pointed at directories that do not exist."""
    return harness.shipped()


def _load(name):
    bundle = _shipped()
    connector, path = connectors_lib.load(bundle["paths"], name)
    return bundle, connector, path


# --- the files load ---------------------------------------------------------------------------------

class LoaderTest(unittest.TestCase):
    """The connector and the model file, through the strict loader and the cascade."""

    def test_the_connector_resolves_through_the_cascade_and_validates(self):
        bundle, connector, path = _load(CONNECTOR)
        self.assertTrue(os.path.isfile(path))
        # `load` raises on an unclassified billing posture, a missing driver `type`, and a file whose
        # own `name` disagrees with the name it was asked for. Reaching here is those three passing.
        self.assertEqual(connector["name"], CONNECTOR)
        self.assertEqual(connector["type"], "openai_compat")
        self.assertEqual(connector["billing"], connectors_lib.SUBSCRIPTION)
        self.assertIs(connector["requires_approval"], False)
        self.assertTrue(connector["packaged"], "it ships in the package, not in a developer's ~/.config")

    def test_it_points_at_the_coding_path_and_not_the_general_api(self):
        """The general endpoint refuses the plan token, so a file pointing there would be a metered
        endpoint wearing a `subscription` posture — the one mislabelling the spend gate cannot
        survive."""
        _bundle, connector, _path = _load(CONNECTOR)
        self.assertEqual(connector["base_url"], CODING_URL)
        self.assertNotEqual(connector["base_url"], GENERAL_URL)
        self.assertIn("/coding/", connector["base_url"])
        self.assertIn(GENERAL_URL, connector["description"],
                      "the file has to say in as many words which URL it is NOT")

    def test_the_key_comes_from_the_vault_under_its_own_name(self):
        _bundle, connector, _path = _load(CONNECTOR)
        self.assertEqual(connector["api_key_secret"], "global/ZAI_CODING_API_KEY")
        self.assertEqual(connector["api_key_env"], "ZAI_CODING_API_KEY")
        self.assertNotIn("OPENROUTER", connector["api_key_secret"])

    def test_the_catalogue_url_is_null_because_the_listing_carries_no_facts(self):
        """Measured 2026-09-20: the plan's `/models` answers 200 with eleven bare ids — no pricing,
        no context length, no effort vocabulary, which is every field a refresh refreshes — and it
        is bearer-gated, while `fetch_catalogue` resolves no connector key. A URL here would give a
        refresh that exits 1 every time rather than one that quietly did nothing."""
        _bundle, connector, _path = _load(CONNECTOR)
        self.assertIsNone(connectors_lib.catalogue_url(connector))

    def test_the_model_file_loads_through_the_cascade_with_its_id_checked(self):
        registry = _shipped()["registry"]
        entry = registry.models.get(MODEL)
        self.assertIsNotNone(entry, "glm-5.3.json did not resolve through the model cascade")
        # `read_model_file` raises when the declared id and the filename disagree.
        self.assertEqual(entry["id"], MODEL)
        self.assertEqual(entry["connector"], CONNECTOR)
        self.assertEqual(entry["family"], "glm")
        self.assertEqual(paths_lib.model_slug(MODEL) + ".json", "glm-5.3.json")

    def test_the_two_glm_files_are_different_models_on_different_endpoints(self):
        """The plan spells the id `glm-5.3` and OpenRouter spells it `z-ai/glm-5.3`, and a model file
        names exactly one connector — which is why this is a second file rather than an alias."""
        registry = _shipped()["registry"]
        self.assertEqual(registry.models[MODEL]["connector"], CONNECTOR)
        self.assertEqual(registry.models[OPENROUTER_GLM]["connector"], "openrouter")
        self.assertEqual(registry.models[MODEL]["family"],
                         registry.models[OPENROUTER_GLM]["family"])


# --- a zero price is a price ------------------------------------------------------------------------

class ZeroPriceTest(unittest.TestCase):
    """An explicit `0` on a subscription connector is a fact about the plan, not a missing price."""

    def setUp(self):
        self.registry = _shipped()["registry"]

    def test_require_accepts_the_zero_priced_model(self):
        entry = self.registry.require(MODEL)          # raises MissingModel if it does not
        self.assertEqual(registry_lib.prices(entry), (0.0, 0.0))

    def test_zero_is_not_the_unpriced_composition_error(self):
        self.assertIsNone(registry_lib.unpriced_reason(self.registry.models[MODEL]))
        self.assertEqual(self.registry.covers([MODEL]), [])

    def test_a_null_price_would_still_be_the_composition_error(self):
        """The guard that makes the assertion above mean something: `covers` is not simply lenient."""
        entry = dict(self.registry.models[MODEL], input_price_per_token=None)
        self.assertIsNotNone(registry_lib.unpriced_reason(entry))

    def test_price_source_says_subscription_rather_than_leaving_a_bare_zero(self):
        entry = self.registry.models[MODEL]
        self.assertIn("subscription", (entry.get("price_source") or "").lower())
        self.assertIsNone(entry.get("source"))

    def test_no_effort_ladder_means_no_reasoning_parameter_is_sent(self):
        """Measured 2026-09-20: the endpoint accepts `reasoning.effort` but does not order the
        reasoning-token count by it (low 769, high 1150, max 712, none 403 — max below low), so the
        file binds nothing and `effort_binding` sends nothing. Thinking stays on, which is the
        endpoint's default."""
        entry = self.registry.models[MODEL]
        self.assertIsNone(entry["effort"])
        self.assertIsNone(entry["effort_vocabulary"])
        for level in registry_lib.EFFORT_LEVELS:
            self.assertEqual(registry_lib.effort_binding(entry, level, MODEL), ("none", None))

    def test_the_overflow_check_is_disabled_and_the_file_says_so(self):
        entry = self.registry.models[MODEL]
        self.assertIsNone(registry_lib.context_limit(entry))
        self.assertTrue((entry.get("context_limit_source") or "").strip(),
                        "a null context limit has to carry its own reason, as claude-opus-5's does")


# --- the tier map is filtered by connector ----------------------------------------------------------

class TierMapTest(unittest.TestCase):
    """Selecting the subscription is selecting the **endpoint**; no tier cell is ever contested."""

    def setUp(self):
        self.bundle = _shipped()

    def _map_for(self, name):
        connector, _path = connectors_lib.load(self.bundle["paths"], name)
        return connectors_lib.registry_tiers(self.bundle["config"], connector, self.bundle["registry"])

    def test_the_packaged_openrouter_map_still_points_glm_at_the_metered_model(self):
        """The package default stays metered: a run the owner does not own must not depend on the
        owner's personal subscription."""
        tiers = self._map_for("openrouter")
        for tier in ("frontier", "standard", "fast"):
            self.assertIn("glm", tiers[tier])
            self.assertTrue(tiers[tier]["glm"].startswith("z-ai/"))
            self.assertNotEqual(tiers[tier]["glm"], MODEL)
        self.assertEqual(tiers["frontier"]["glm"], OPENROUTER_GLM)

    def test_the_subscription_map_is_the_single_frontier_glm_cell(self):
        self.assertEqual(self._map_for(CONNECTOR), {"frontier": {"glm": MODEL}})

    def test_a_run_on_this_connector_is_single_family_by_construction(self):
        """One connector serves one run and this one has one cell.

        A multi-family template does **not** refuse against it — the re-seat rule fires first and
        collapses every seat onto `glm`, keeping reviewer ids that name families the run never used
        (measured 2026-09-20 with `spec-review`; see the v4 spec under Config shape). What this
        asserts is the cause: the map itself offers exactly one family."""
        tiers = self._map_for(CONNECTOR)
        families = {family for row in tiers.values() for family in row}
        self.assertEqual(families, {"glm"})

    def test_nothing_is_displaced_so_tier_map_overrides_stays_empty(self):
        """The brief for this change expected `tier_map_overrides` to record the substitution. It
        does not: `derive_tiers` filters to the resolved connector's models *before* any cell is
        contested, so the two GLM files never meet."""
        self._map_for("openrouter")
        self._map_for(CONNECTOR)
        self.assertEqual(self.bundle["paths"].roots_block()["tier_map_overrides"], [])


# --- the spend gate does not ask --------------------------------------------------------------------

class SpendGateTest(unittest.TestCase):
    """A dispatched — not harness — seat on a subscription endpoint is still ungated."""

    def setUp(self):
        _bundle, self.connector, _path = _load(CONNECTOR)
        self.seats = [{"reviewer_id": "buildability-glm"}, {"reviewer_id": "adversarial-glm"}]

    def test_requires_approval_is_false(self):
        self.assertFalse(connectors_lib.requires_approval(self.connector))

    def test_the_gate_does_not_fire_over_dispatched_seats(self):
        block = connectors_lib.spend_gate(self.connector, seats=self.seats, judgment=True)
        self.assertFalse(block["required"])
        self.assertIsNone(block["granted"])
        self.assertEqual(block["connector"], CONNECTOR)
        self.assertEqual(block["billing"], "subscription")
        self.assertEqual(block["seats"], ["adversarial-glm", "buildability-glm"])

    def test_the_gate_records_the_posture_even_though_it_never_asked(self):
        """"This endpoint bills nothing" is as much part of the audit trail as an approved $2.75."""
        block = connectors_lib.spend_gate(self.connector, seats=self.seats)
        self.assertIn("billing", block)
        self.assertNotIn("gate_disabled_by", block,
                         "that key is for a metered endpoint somebody switched the gate off for, "
                         "which is a different fact from an endpoint that bills nothing")

    def test_the_same_seats_on_the_metered_connector_are_gated(self):
        """The guard: the assertions above are about the posture, not about an inert gate."""
        bundle = _shipped()
        metered, _path = connectors_lib.load(bundle["paths"], "openrouter")
        self.assertTrue(connectors_lib.spend_gate(metered, seats=self.seats)["required"])


# --- the refresh leaves a subscription model's zero prices alone -------------------------------------

class RefreshGuardTest(unittest.TestCase):
    """A price-less catalogue must not null what it cannot answer for."""

    def _entry(self):
        return {
            "id": MODEL, "connector": CONNECTOR, "family": "glm", "tiers": ["frontier"],
            "input_price_per_token": 0, "output_price_per_token": 0,
            "context_limit": None, "effort_vocabulary": None,
            "price_source": "subscription — the coding plan bills nothing per call",
            "source": None, "refreshed_at": None,
        }

    def test_a_price_less_catalogue_leaves_the_zero_prices_standing(self):
        """The plan's `/models` shape, measured 2026-09-20: `id`, `object`, `created`, `owned_by` and
        nothing else. Every refreshable field reads None, and a None is skipped rather than written."""
        models = {MODEL: self._entry()}
        catalogue = {MODEL: {"id": MODEL, "object": "model", "created": 1786636800, "owned_by": "z-ai"}}
        changes, unknown, added = refresh_models.refresh(
            models, catalogue, connector=CONNECTOR, url="https://api.z.ai/api/coding/paas/v4/models")
        self.assertEqual(changes, [])
        self.assertEqual(unknown, [])
        self.assertEqual(added, [])
        self.assertEqual(models[MODEL]["input_price_per_token"], 0)
        self.assertEqual(models[MODEL]["output_price_per_token"], 0)
        self.assertIn("subscription", models[MODEL]["price_source"])

    def test_an_explicit_zero_in_a_catalogue_is_not_written_over_a_zero_either(self):
        """`_price` reads any non-positive number as "no price", so a catalogue that did quote 0
        cannot turn a sourced zero into a null."""
        self.assertIsNone(refresh_models._price(0))
        self.assertIsNone(refresh_models._price("0"))

    def test_an_openrouter_refresh_skips_it_as_another_connector_s_model(self):
        """It is not "missing from the catalogue" — it was never that catalogue's to answer for."""
        models = {MODEL: self._entry(),
                  OPENROUTER_GLM: {"id": OPENROUTER_GLM, "connector": "openrouter"}}
        foreign = refresh_models.foreign_models(models, "openrouter")
        self.assertEqual(foreign, [MODEL])
        changes, unknown, _added = refresh_models.refresh(
            models, {OPENROUTER_GLM: {"id": OPENROUTER_GLM}}, connector="openrouter", skip=foreign)
        self.assertEqual(changes, [])
        self.assertNotIn(MODEL, unknown, "a skipped model must not be reported as unknown")
        self.assertEqual(models[MODEL]["input_price_per_token"], 0)

    def test_the_shipped_file_is_the_one_this_guard_describes(self):
        """The guard above is written against a hand-built entry; this pins it to what ships."""
        entry = _shipped()["registry"].models[MODEL]
        self.assertEqual(entry["input_price_per_token"], 0)
        self.assertEqual(entry["output_price_per_token"], 0)
        self.assertEqual(entry["connector"], CONNECTOR)


# --- the driver reads both spellings of the reasoning field -------------------------------------------

class ReasoningContentFallbackTest(unittest.TestCase):
    """`reasoning` is OpenRouter's name for it; `reasoning_content` is z.ai's, and only z.ai's.

    A fallback that knew one name would raise "no content" on a response that carried the whole
    answer — the case a seat hits when the completion cap went on reasoning. This is a field-name
    difference in the OpenAI-compatible wire format, not a vendor special case: the driver reads
    both and names which one answered, and nothing in it branches on the endpoint.
    """

    ENTRY = {"base_url": CODING_URL, "api_key": "not-a-real-key", "max_tokens": 4000}

    def _dispatch(self, message):
        body = {"id": "x", "choices": [{"message": message, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        import json as _json
        calls = []

        def http_request_fn(url, headers, payload, timeout):
            calls.append(url)
            return 200, _json.dumps(body)

        result = openai_compat.dispatch_detailed(
            "sys", "user", MODEL, dict(self.ENTRY), None, http_request_fn)
        self.assertEqual(calls, [CODING_URL + "/chat/completions"])
        return result

    def test_empty_content_falls_back_to_reasoning_content(self):
        result = self._dispatch({"content": "", "reasoning_content": "the answer lived here"})
        self.assertEqual(result["text"], "the answer lived here")
        self.assertIn("content was empty; fell back to the `reasoning_content` field", result["notes"])

    def test_the_openrouter_spelling_still_wins_when_both_are_present(self):
        result = self._dispatch({"content": "", "reasoning": "openrouter",
                                 "reasoning_content": "zai"})
        self.assertEqual(result["text"], "openrouter")
        self.assertIn("content was empty; fell back to the `reasoning` field", result["notes"])

    def test_real_content_is_never_displaced_by_either_field(self):
        result = self._dispatch({"content": "the real answer", "reasoning_content": "thinking"})
        self.assertEqual(result["text"], "the real answer")
        self.assertEqual([n for n in result["notes"] if "fell back" in n], [])

    def test_neither_field_leaves_an_empty_string_and_no_note(self):
        """Unchanged from before the second spelling was added: whitespace-only content blanks."""
        result = self._dispatch({"content": "   "})
        self.assertEqual(result["text"], "")
        self.assertEqual([n for n in result["notes"] if "fell back" in n], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""The shipped catalog: every persona file and every panel template, checked as data.

No network and no paid call. These are the tests that fail when a new lens is added and its body
drifts from the house shape, or when a panel template names a lens nobody wrote. Everything is
resolved through the real cascade against the real package, so what is tested is what ships.

Three claims:

- **Every persona is the same shape.** Framework §6 frontmatter with `model: frontier`, `tools: []`
  and the finding schema in `context`; the four body sections; and the three sentences the spec
  requires in every lens body — the severity calibration, the `judgment-call` rule, and the
  verbatim-quote rule. They are asserted **byte-identical across the nine personas**, because a
  reviewer calibrated differently from its neighbours makes the panel's agreement counts mean
  something different per seat, which is the one thing this skill cannot tolerate.
- **Every panel template is the same shape**, and names only lenses that exist. A template naming a
  missing lens fails at Resolve with a `PathError`, after the run directory is claimed and long
  after the operator has stopped watching.
- **Every shipped template resolves at all three tiers** against the shipped config: seats take
  families, families take concrete models, and nothing raises.

    python3 scripts/tests/test_catalog.py
"""

import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import run_panel  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402
from lib import seating as seating_lib  # noqa: E402

AGENTS_DIR = os.path.join(SKILL_DIR, "agents")
PANELS_DIR = os.path.join(SKILL_DIR, "templates", "panels")

BODY_SECTIONS = ("# Role", "# Instructions", "# Rubric", "# Output")

# The three sentences the v3 spec requires in every lens body. Asserted as exact substrings, and
# asserted equal across personas, so a lens cannot be calibrated differently from its neighbours.
CALIBRATION = "**Severity is rated by consequence, not by how much of the artifact is unspecified.**"
JUDGMENT_RULE = "**`judgment-call` means a design fork, not a thin spot.**"
VERBATIM_RULE = "**Quote verbatim.**"

# Every lens the catalog table names. `synthesis` is not a lens and is not written yet.
EXPECTED_LENSES = (
    "fidelity", "buildability", "consistency", "adversarial", "completeness",
    "source-credibility", "security", "alternatives", "second-order",
)

# The shipped templates that actually seat a panel, and the one that ships deferred.
LIVE_PANELS = ("spec-review", "research-report", "design-decision")
DEFERRED_PANELS = ("code-review",)

TIERS = ("frontier", "standard", "fast")


def persona_files():
    return sorted(name for name in os.listdir(AGENTS_DIR) if name.endswith(".md"))


def panel_files():
    return sorted(name for name in os.listdir(PANELS_DIR) if name.endswith(".json"))


def read_panel(name):
    with open(os.path.join(PANELS_DIR, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def shipped_config():
    with open(os.path.join(SKILL_DIR, "templates", "config.json"), "r", encoding="utf-8") as handle:
        return json.load(handle)["openrouter"]


# --- personas ----------------------------------------------------------------------------------------

class PersonaFileTest(unittest.TestCase):
    """Every file in `agents/`, one subtest each, so a failure names the persona."""

    def test_every_persona_parses_and_carries_the_house_frontmatter(self):
        for name in persona_files():
            with self.subTest(persona=name):
                frontmatter, body = report_lib.parse_agent_file(os.path.join(AGENTS_DIR, name))
                self.assertEqual(frontmatter.get("name"), name[:-3],
                                 "the frontmatter name is the filename, or the cascade resolves one file under another's name")
                self.assertTrue((frontmatter.get("description") or "").strip(), "a persona with no description")
                self.assertEqual(frontmatter.get("model"), "frontier",
                                 "framework §6 wants a value that names a key in the tiers map, and `frontier` is one")
                self.assertEqual(frontmatter.get("tools"), [],
                                 "personas are single-turn and tool-less; the artifact is inlined for them")
                self.assertEqual(frontmatter.get("context"), ["finding-schema.md"])
                self.assertEqual(frontmatter.get("output_type"), "json_report")
                self.assertTrue(body.strip(), "no body after the frontmatter")

    def test_every_persona_carries_the_four_body_sections_in_order(self):
        for name in persona_files():
            with self.subTest(persona=name):
                _frontmatter, body = report_lib.parse_agent_file(os.path.join(AGENTS_DIR, name))
                # `parse_agent_file` strips the leading newlines, so `# Role` opens the body with no
                # newline in front of it; one is prepended here so every heading matches the same way.
                text = "\n" + body
                positions = []
                for section in BODY_SECTIONS:
                    heading = "\n" + section + "\n"
                    self.assertEqual(text.count(heading), 1, "expected exactly one {0} section".format(section))
                    positions.append(text.index(heading))
                self.assertEqual(positions, sorted(positions), "the four sections are out of order")

    def test_every_persona_carries_the_three_required_sentences(self):
        """Calibration and the `judgment-call` rule in the rubric; the verbatim rule in the body."""
        for name in persona_files():
            with self.subTest(persona=name):
                _frontmatter, body = report_lib.parse_agent_file(os.path.join(AGENTS_DIR, name))
                rubric = body.split("\n# Rubric\n", 1)[1].split("\n# Output\n", 1)[0]
                self.assertIn(CALIBRATION, rubric, "the severity calibration belongs in the rubric")
                self.assertIn(JUDGMENT_RULE, rubric, "the sharpened `judgment-call` rule belongs in the rubric")
                self.assertIn(VERBATIM_RULE, body)

    def test_the_three_sentences_are_byte_identical_across_the_catalog(self):
        """A lens calibrated differently from its neighbours makes the agreement counts mean two things."""
        for marker in (CALIBRATION, JUDGMENT_RULE, VERBATIM_RULE):
            paragraphs = {}
            for name in persona_files():
                _frontmatter, body = report_lib.parse_agent_file(os.path.join(AGENTS_DIR, name))
                start = body.index(marker)
                paragraphs.setdefault(body[start:body.index("\n", start)], []).append(name)
            self.assertEqual(len(paragraphs), 1,
                             "{0!r} is worded differently across personas: {1}".format(
                                 marker, {text[:60]: names for text, names in paragraphs.items()}))

    def test_the_catalog_holds_exactly_the_lenses_the_spec_names(self):
        self.assertEqual(sorted(persona_files()),
                         sorted("lens-{0}.md".format(lens) for lens in EXPECTED_LENSES))

    def test_every_persona_resolves_through_the_cascade(self):
        paths = paths_lib.Paths(workspace=SKILL_DIR)
        for lens in EXPECTED_LENSES:
            with self.subTest(lens=lens):
                self.assertTrue(os.path.isfile(paths.persona("lens-" + lens)))


class PersonaFrontmatterTierTest(unittest.TestCase):
    """Scope 1's point: `model: frontier` names a key in the tiers map, so the level is live."""

    def frontmatter_fn(self):
        return run_panel.persona_frontmatter(paths_lib.Paths(workspace=SKILL_DIR))

    def test_with_no_other_tier_source_a_seat_takes_the_personas_frontmatter(self):
        entry = {"type": "openai_compat", "tiers": {"frontier": {"openai": "m/frontier"},
                                                    "standard": {"openai": "m/standard"}}}
        seats = seating_lib.resolve(
            {"name": "bare", "seats": [{"lens": "fidelity", "family": "openai"}]},
            entry, frontmatter_fn=self.frontmatter_fn())
        self.assertEqual((seats[0]["tier"], seats[0]["tier_source"], seats[0]["model"]),
                         ("frontier", "persona", "m/frontier"),
                         "no --model, no --tier, no seat tier, no panel tier, no config default_tier")

    def test_the_configs_default_tier_still_beats_it(self):
        """The framework §8 inversion survives scope 1: the run's stakes outrank the lens."""
        entry = {"type": "openai_compat", "default_tier": "standard",
                 "tiers": {"frontier": {"openai": "m/frontier"}, "standard": {"openai": "m/standard"}}}
        seats = seating_lib.resolve(
            {"name": "bare", "seats": [{"lens": "fidelity", "family": "openai"}]},
            entry, frontmatter_fn=self.frontmatter_fn())
        self.assertEqual((seats[0]["tier"], seats[0]["tier_source"]), ("standard", "config"))

    def test_every_persona_in_the_catalog_resolves_the_same_way(self):
        entry = {"type": "openai_compat", "tiers": {"frontier": {"openai": "m/frontier"}}}
        frontmatter_fn = self.frontmatter_fn()
        for lens in EXPECTED_LENSES:
            with self.subTest(lens=lens):
                seats = seating_lib.resolve(
                    {"name": "bare", "seats": [{"lens": lens, "family": "openai"}]},
                    entry, frontmatter_fn=frontmatter_fn)
                self.assertEqual(seats[0]["tier_source"], "persona")


# --- panel templates ---------------------------------------------------------------------------------

class PanelTemplateShapeTest(unittest.TestCase):
    """One shape for every template in `templates/panels/`, deferred stubs included."""

    STRINGS = ("name", "description")
    LIVE_FIELDS = {
        "requires_references": bool,
        "min_families": int,
        "tier": str,
        "reconciler": str,
        "auto_apply": bool,
        "verify_web": bool,
    }

    def test_the_directory_holds_exactly_the_four_templates_the_spec_ships(self):
        self.assertEqual(sorted(panel_files()),
                         sorted(name + ".json" for name in LIVE_PANELS + DEFERRED_PANELS))

    def test_every_template_names_itself_and_says_what_it_is_for(self):
        for name in panel_files():
            with self.subTest(panel=name):
                panel = read_panel(name)
                for field in self.STRINGS:
                    self.assertIsInstance(panel.get(field), str)
                    self.assertTrue(panel[field].strip())
                self.assertEqual(panel["name"], name[:-5],
                                 "the template's name is its filename, or `--panel <name>` and the manifest disagree")
                self.assertIsInstance(panel.get("seats"), list)

    def test_every_live_template_carries_the_full_field_set(self):
        for name in LIVE_PANELS:
            with self.subTest(panel=name):
                panel = read_panel(name + ".json")
                self.assertNotIn("deferred", panel)
                for field, kind in self.LIVE_FIELDS.items():
                    self.assertIsInstance(panel.get(field), kind, "missing or mistyped `{0}`".format(field))
                self.assertGreaterEqual(panel["min_families"], 1)
                self.assertIn(panel["tier"], TIERS)
                self.assertIn(panel["reconciler"], ("host", "synthesis", "default"))
                self.assertTrue(panel["seats"], "a live template seats nobody")

    def test_every_seat_names_a_lens_that_exists_and_a_family_or_a_constraint(self):
        installed = set(EXPECTED_LENSES)
        for name in panel_files():
            panel = read_panel(name)
            for seat in (panel.get("seats") or []) + (panel.get("optional_seats") or []):
                with self.subTest(panel=name, seat=seat.get("lens")):
                    self.assertIn(seat.get("lens"), installed,
                                  "a template naming a lens nobody wrote fails at Resolve, after the run directory is claimed")
                    self.assertTrue(seat.get("family"))

    def test_no_shipped_template_seats_a_claude_family(self):
        """The owner's 2026-09-18 decision of record, and the templates are where it is visible."""
        for name in panel_files():
            panel = read_panel(name)
            families = [seat.get("family") for seat in (panel.get("seats") or []) + (panel.get("optional_seats") or [])]
            with self.subTest(panel=name):
                self.assertNotIn("claude", families)

    def test_every_live_template_seats_distinct_named_families(self):
        for name in LIVE_PANELS:
            panel = read_panel(name + ".json")
            families = [seat["family"] for seat in panel["seats"]]
            with self.subTest(panel=name):
                self.assertEqual(len(families), len(set(families)), "two seats on one family")
                for family in families:
                    self.assertNotIn(family, seating_lib.CONSTRAINTS,
                                     "`distinct` and `non-claude` appear in no shipped template")

    def test_the_deferred_template_carries_its_route_and_no_seats(self):
        for name in DEFERRED_PANELS:
            panel = read_panel(name + ".json")
            with self.subTest(panel=name):
                self.assertIs(panel.get("deferred"), True)
                self.assertEqual(panel.get("routes_to"), "/code-review")
                self.assertEqual(panel.get("seats"), [])

    def test_the_reference_free_fallback_is_reference_free(self):
        """`run_panel.FALLBACK_PANEL` is where a run with no references lands; it must not need any."""
        panel = read_panel(run_panel.FALLBACK_PANEL + ".json")
        self.assertIs(panel["requires_references"], False)

    def test_no_shipped_template_turns_web_verification_on(self):
        """`verify_web` is off for the whole of v1: the citing lenses judge the sources supplied."""
        for name in LIVE_PANELS:
            with self.subTest(panel=name):
                self.assertIs(read_panel(name + ".json")["verify_web"], False)

    def test_only_reference_bearing_templates_seat_the_citing_lenses(self):
        citing = set(run_panel.REFERENCE_REQUIRED_LENSES)
        for name in LIVE_PANELS:
            panel = read_panel(name + ".json")
            seated = {seat["lens"] for seat in panel["seats"]} & citing
            with self.subTest(panel=name):
                if seated:
                    self.assertIs(panel["requires_references"], True,
                                  "{0} seats {1} and does not declare it needs references".format(name, sorted(seated)))

    def test_every_reference_bearing_template_seats_a_citing_lens(self):
        """The converse, and the half that catches a flag nothing behind it needs.

        `requires_references` is load-bearing only for inference (`run_panel.py` reads it to decide
        where a run with no references may land); the refusal keys on the lens. So a template that
        declares the flag and seats nobody who cites is a template that gets skipped by inference for
        no reason any seat on it could explain, and no runtime check would ever say so.
        """
        citing = set(run_panel.REFERENCE_REQUIRED_LENSES)
        for name in LIVE_PANELS:
            panel = read_panel(name + ".json")
            with self.subTest(panel=name):
                if panel["requires_references"]:
                    seated = {seat["lens"] for seat in panel["seats"]} & citing
                    self.assertTrue(seated,
                                    "{0} declares it needs references and seats no lens that cites".format(name))

    def test_an_optional_seat_never_doubles_a_family_the_template_already_holds(self):
        """Promoting one is a one-line edit, and it must not quietly seat two lenses on one mind."""
        for name in LIVE_PANELS:
            panel = read_panel(name + ".json")
            seated = {seat["family"] for seat in panel["seats"]}
            for seat in panel.get("optional_seats") or []:
                with self.subTest(panel=name, seat=seat.get("lens")):
                    self.assertNotIn(seat["family"], seated,
                                     "{0}'s optional `{1}` seat doubles the family `{2}` already at the table".format(
                                         name, seat.get("lens"), seat.get("family")))

    def test_every_optional_seats_family_resolves_at_every_tier(self):
        """A seat nobody can promote at `fast` is a note, not an option."""
        entry = shipped_config()
        for name in LIVE_PANELS:
            panel = read_panel(name + ".json")
            for seat in panel.get("optional_seats") or []:
                for tier in TIERS:
                    with self.subTest(panel=name, seat=seat.get("lens"), tier=tier):
                        self.assertTrue(entry["tiers"][tier].get(seat["family"]),
                                        "no cell for `{0}` at {1}".format(seat["family"], tier))


class PanelTemplateResolutionTest(unittest.TestCase):
    """Every shipped template, against the shipped config, at every tier."""

    def setUp(self):
        self.entry = shipped_config()
        self.frontmatter_fn = run_panel.persona_frontmatter(paths_lib.Paths(workspace=SKILL_DIR))

    def test_every_live_template_resolves_at_all_three_tiers(self):
        for name in LIVE_PANELS:
            panel = read_panel(name + ".json")
            for tier in TIERS:
                with self.subTest(panel=name, tier=tier):
                    seats = seating_lib.resolve(panel, self.entry, cli_tier=tier,
                                                frontmatter_fn=self.frontmatter_fn)
                    self.assertEqual(len(seats), len(panel["seats"]))
                    for seat in seats:
                        self.assertTrue(seat["family"], "{0} took no family".format(seat["reviewer_id"]))
                        self.assertTrue(seat["model"], "{0} resolved to no model".format(seat["reviewer_id"]))
                        self.assertEqual(seat["tier"], tier)
                        self.assertNotEqual(seat["family"], "claude",
                                            "no re-seat may add the family the default panels exclude")

    def test_every_live_template_clears_its_own_min_families_target_at_every_tier(self):
        for name in LIVE_PANELS:
            panel = read_panel(name + ".json")
            for tier in TIERS:
                with self.subTest(panel=name, tier=tier):
                    seats = seating_lib.resolve(panel, self.entry, cli_tier=tier,
                                                frontmatter_fn=self.frontmatter_fn)
                    self.assertGreaterEqual(len({seat["family"] for seat in seats}), panel["min_families"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

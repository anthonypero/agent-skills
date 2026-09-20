#!/usr/bin/env python3
"""The shipped catalog: every persona file and every panel template, checked as data.

No network and no paid call. These are the tests that fail when a new lens is added and its body
drifts from the house shape, or when a panel template names a lens nobody wrote. Everything is
resolved through the real cascade against the real package, so what is tested is what ships.

Three claims:

- **Every persona is the same shape.** Framework §6 frontmatter with `model: frontier`, `tools: []`
  and its own `context` list; the four body sections; and, in every **lens** body, the three
  sentences the spec requires — the severity calibration, the `judgment-call` rule, and the
  verbatim-quote rule. Those three are asserted **byte-identical across the nine lenses**, because a
  reviewer calibrated differently from its neighbours makes the panel's agreement counts mean
  something different per seat, which is the one thing this skill cannot tolerate.

  **`synthesis` is a persona and is not a lens**, and the shape assertions split there. It reads
  reports rather than the artifact-under-a-lens, so it carries `reconciliation.md` in `context` as
  well as the finding schema, it is never seated on a panel, and the verbatim-quote rule is not its
  rule to carry. What it must carry instead is the asymmetry that makes an unattended judge safe:
  the severity calibration, the all-`judgment-call` flag rule, and the `rulings` prohibition. The
  nine-lens assertion is deliberately **not** loosened to let it in: `agents/` holds exactly the
  nine lenses plus exactly this one non-lens persona.

  **`judge.md` is neither**, and it is checked by its own class. It is a **harness agent
  definition** rather than a persona: it is never dispatched through a connector, so it has no tier
  in its `model` field and no empty `tools` list — it carries a concrete frontier Claude model id,
  an effort, and the read tools it needs to go and get what `synthesis` is handed. What it is held
  to is the pair of rules `reconcile.py` enforces against its patch, byte-identical to
  `synthesis`'s, and the reference to `synthesis` in place of a second copy of the rubric.
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

import harness  # noqa: E402
import run_panel  # noqa: E402
from lib import judge as judge_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
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

# The two sentences the `synthesis` body must carry in its rubric. The persona is the one author
# `reconcile.py` enforces the judgment-call rule against and the one author forbidden `rulings`, so
# a body that does not say both is a body whose instructions and whose validator disagree.
SYNTHESIS_FLAG_RULE = "**A cluster whose findings are all `judgment-call` is always `flag-for-human`.**"
SYNTHESIS_RULINGS_RULE = "**You may not emit `rulings`.**"

# Every lens the catalog table names.
EXPECTED_LENSES = (
    "fidelity", "buildability", "consistency", "adversarial", "completeness",
    "source-credibility", "security", "alternatives", "second-order",
)

# The one persona in `agents/` that is not a lens: the judgment supplier for autonomous runs. It is
# never seated on a panel, so it is not in `EXPECTED_LENSES` and no template may name it as a lens.
NON_LENS_PERSONAS = ("synthesis",)

# The one file in `agents/` that is not a persona at all. `judge.md` is a harness agent definition:
# `install.sh` copies it into the harness agents directory, the orchestrating session spawns it by
# name, and nothing here ever composes a prompt from it — so the persona shape rules do not apply
# and it is excluded from `persona_files()` rather than loosening them.
HARNESS_AGENTS = ("judge.md",)

# Per persona, the `context` its frontmatter must name, in order. A lens is shown the output
# contract; `synthesis` is shown the algorithm it is supplying half of, and then the same contract,
# because it is arbitrating findings written against it.
LENS_CONTEXT = ["finding-schema.md"]
SYNTHESIS_CONTEXT = ["reconciliation.md", "finding-schema.md"]

# The shipped templates that actually seat a panel, and the one that ships deferred.
LIVE_PANELS = ("spec-review", "research-report", "design-decision")
DEFERRED_PANELS = ("code-review",)

# **The draft panel is live and is deliberately outside two of the rules below.** It seats four
# `claude` seats on one family, which is the whole point of it: `--draft` composes against the
# harness connector, so every seat is a subagent on the owner's plan and there is no second family
# to be had at any price. The no-Claude rule and the distinct-families rule are rules about the
# panels that **gate** a document, and this one is written down as not being one — its own
# description says so and its reconciliation opens by saying so. It is named here rather than
# quietly excluded, and `DraftPanelTest` below asserts the exception rather than only permitting it.
DRAFT_PANELS = ("draft-review",)

TIERS = ("frontier", "standard", "fast")


def agent_files():
    """Every markdown file in `agents/`: the personas and the harness agent definitions."""
    return sorted(name for name in os.listdir(AGENTS_DIR) if name.endswith(".md"))


def persona_files():
    """Every persona file in `agents/` — the nine lenses and the one non-lens."""
    return sorted(name for name in agent_files() if name not in HARNESS_AGENTS)


def lens_files():
    """Just the lenses. The shape rules that are about *reviewing* apply to exactly these."""
    return sorted(name for name in persona_files() if name.startswith("lens-"))


def panel_files():
    return sorted(name for name in os.listdir(PANELS_DIR) if name.endswith(".json"))


def read_panel(name):
    with open(os.path.join(PANELS_DIR, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


def shipped_config():
    """The resolved connector view: run-wide defaults, the connector file, the derived tier map.

    There is no single file to read any more — the tier map is derived from the model files — so
    this asks the same assembler every script asks, with no outer root in the cascade.
    """
    return harness.shipped()["entry"]


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
                self.assertEqual(frontmatter.get("context"),
                                 LENS_CONTEXT if name.startswith("lens-") else SYNTHESIS_CONTEXT,
                                 "a persona is shown exactly the references its job needs")
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

    def test_every_lens_carries_the_three_required_sentences(self):
        """Calibration and the `judgment-call` rule in the rubric; the verbatim rule in the body."""
        for name in lens_files():
            with self.subTest(persona=name):
                _frontmatter, body = report_lib.parse_agent_file(os.path.join(AGENTS_DIR, name))
                rubric = body.split("\n# Rubric\n", 1)[1].split("\n# Output\n", 1)[0]
                self.assertIn(CALIBRATION, rubric, "the severity calibration belongs in the rubric")
                self.assertIn(JUDGMENT_RULE, rubric, "the sharpened `judgment-call` rule belongs in the rubric")
                self.assertIn(VERBATIM_RULE, body)

    def test_the_three_sentences_are_byte_identical_across_the_lenses(self):
        """A lens calibrated differently from its neighbours makes the agreement counts mean two things."""
        for marker in (CALIBRATION, JUDGMENT_RULE, VERBATIM_RULE):
            paragraphs = {}
            for name in lens_files():
                _frontmatter, body = report_lib.parse_agent_file(os.path.join(AGENTS_DIR, name))
                start = body.index(marker)
                paragraphs.setdefault(body[start:body.index("\n", start)], []).append(name)
            self.assertEqual(len(paragraphs), 1,
                             "{0!r} is worded differently across personas: {1}".format(
                                 marker, {text[:60]: names for text, names in paragraphs.items()}))

    def test_the_catalog_holds_exactly_the_lenses_the_spec_names(self):
        """Still exactly nine lenses. `synthesis` does not widen this; it is checked separately."""
        self.assertEqual(lens_files(),
                         sorted("lens-{0}.md".format(lens) for lens in EXPECTED_LENSES))

    def test_the_agents_directory_holds_the_lenses_and_exactly_one_non_lens_persona(self):
        self.assertEqual(
            persona_files(),
            sorted(["lens-{0}.md".format(lens) for lens in EXPECTED_LENSES]
                   + ["{0}.md".format(name) for name in NON_LENS_PERSONAS]),
            "a file in agents/ that is neither a catalog lens nor a named non-lens persona")

    def test_the_agents_directory_holds_exactly_the_harness_agents_the_skill_installs(self):
        """The other half: a harness agent nobody installs is a file the package ships for nothing."""
        self.assertEqual(sorted(set(agent_files()) - set(persona_files())), sorted(HARNESS_AGENTS))

    def test_no_shipped_template_seats_the_non_lens_persona_as_a_lens(self):
        """`synthesis` supplies the judgment; a panel that seated it would review with the judge."""
        for name in panel_files():
            panel = read_panel(name)
            lenses = [seat.get("lens") for seat in (panel.get("seats") or []) + (panel.get("optional_seats") or [])]
            for non_lens in NON_LENS_PERSONAS:
                with self.subTest(panel=name, persona=non_lens):
                    self.assertNotIn(non_lens, lenses)


class SynthesisPersonaTest(unittest.TestCase):
    """The one non-lens persona, whose body is the other half of a rule the script enforces."""

    def setUp(self):
        self.frontmatter, self.body = report_lib.parse_agent_file(os.path.join(AGENTS_DIR, "synthesis.md"))
        self.rubric = self.body.split("\n# Rubric\n", 1)[1].split("\n# Output\n", 1)[0]

    def test_it_is_shown_the_algorithm_it_supplies_half_of(self):
        self.assertEqual(self.frontmatter.get("context"), SYNTHESIS_CONTEXT)
        for name in SYNTHESIS_CONTEXT:
            with self.subTest(reference=name):
                self.assertTrue(os.path.isfile(os.path.join(SKILL_DIR, "references", name)),
                                "the persona names a reference the package does not ship")

    def test_it_carries_the_severity_calibration_the_lenses_carry(self):
        """It arbitrates their severities, so it is calibrated the same way they are."""
        self.assertIn(CALIBRATION, self.rubric)

    def test_it_carries_the_two_rules_reconcile_py_enforces_against_it(self):
        """`reconcile_core` rejects both; the body is where the persona is told before it is rejected."""
        self.assertIn(SYNTHESIS_FLAG_RULE, self.rubric)
        self.assertIn(SYNTHESIS_RULINGS_RULE, self.rubric)

    def test_it_never_claims_to_write_the_reconciliation(self):
        """One writer of both files, in every mode — the defect the host/persona split exists to close."""
        self.assertIn("only writer of `reconciliation.json`", self.body)

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


class HarnessJudgeAgentTest(unittest.TestCase):
    """`agents/judge.md` — a harness agent definition, held to what makes its judgment safe."""

    def setUp(self):
        self.path = os.path.join(AGENTS_DIR, "judge.md")
        self.frontmatter, self.body = report_lib.parse_agent_file(self.path)
        self.rubric = self.body.split("\n# Rubric\n", 1)[1].split("\n# Output\n", 1)[0]

    def test_it_is_pinned_to_a_concrete_frontier_claude_model_at_high_effort(self):
        """Owner ruling, 2026-09-19. A tier name here would be meaningless: nothing seats this."""
        self.assertEqual(self.frontmatter.get("model"), "claude-fable-5-1")
        self.assertEqual(self.frontmatter.get("effort"), "high")

    def test_its_name_is_the_name_install_sh_installs_it_under(self):
        """The file is `judge.md` in the package and `ensemble-judge` in the harness, and the
        frontmatter is what the orchestrating session spawns by. The two must agree or the printed
        spawn instruction names an agent that is not there."""
        self.assertEqual(self.frontmatter.get("name"), judge_lib.HARNESS_JUDGE_AGENT)
        self.assertEqual(os.path.basename(self.path), judge_lib.HARNESS_JUDGE_AGENT_FILE)

    def tools(self):
        declared = self.frontmatter.get("tools")
        if isinstance(declared, list):
            return [str(tool).strip() for tool in declared]
        return [tool.strip() for tool in str(declared).split(",") if tool.strip()]

    def test_it_can_read_a_run_directory_and_write_exactly_one_file(self):
        """`Write` is not optional: the agent's whole output is a file, because an inter-agent
        message truncates near 5,500 characters and a judgment patch does not fit in one. The tool
        list is an allowlist, so an agent without it produces nothing and the stage deadlocks."""
        tools = self.tools()
        self.assertIn("Read", tools, "it cannot read the reports without this")
        self.assertIn("Write", tools, "it cannot produce a judgment patch at all without this")

    def test_it_gets_no_tool_that_could_edit_the_artifact_it_is_judging(self):
        """`Write` creates the one file it owns. `Edit`, a shell or a task tool would let the judge
        change the document under review, or a report, or the reconciliation — and the scoping of
        `Write` to one path is prompt-enforced, so the tool list is the only part that is not."""
        for tool in self.tools():
            with self.subTest(tool=tool):
                self.assertIn(tool, ("Read", "Grep", "Glob", "Write"))

    def test_the_one_path_it_may_write_is_stated_in_the_body(self):
        """The tool list cannot express "this one path", so the body has to, in terms a reader can
        check against `runs.staging_dir()`."""
        self.assertIn("one `Write` call", self.body)
        self.assertIn("exactly one file, at exactly the staging path you were given", self.body)

    def test_it_carries_the_four_body_sections_in_order(self):
        text = "\n" + self.body
        positions = []
        for section in BODY_SECTIONS:
            heading = "\n" + section + "\n"
            self.assertEqual(text.count(heading), 1, "expected exactly one {0} section".format(section))
            positions.append(text.index(heading))
        self.assertEqual(positions, sorted(positions), "the four sections are out of order")

    def test_it_carries_the_two_rules_reconcile_py_enforces_against_it(self):
        """Byte-identical to `synthesis`'s, because one validator enforces both against one floor."""
        self.assertIn(SYNTHESIS_FLAG_RULE, self.rubric)
        self.assertIn(SYNTHESIS_RULINGS_RULE, self.rubric)

    def test_it_points_at_the_synthesis_rubric_rather_than_restating_it(self):
        """One rubric for the judgment. Two copies of it would drift, and the drift would be
        invisible: nothing compares the judgment a harness agent makes with the judgment the
        persona would have made on the same clusters."""
        self.assertIn("agents/synthesis.md", self.body)
        self.assertIn("is not repeated here", self.body)

    def test_its_author_is_the_one_reconcile_py_holds_it_to(self):
        self.assertIn('"{0}"'.format(judge_lib.HARNESS_JUDGE_AUTHOR), self.body)
        self.assertIn(judge_lib.HARNESS_JUDGE_AUTHOR, core.AUTHORS)
        self.assertIn(judge_lib.HARNESS_JUDGE_AUTHOR, core.UNATTENDED_AUTHORS)

    def test_it_writes_to_staging_and_never_into_the_run_directory(self):
        """The harness-leg rule: no seat is *given* a path into a directory holding others' work."""
        self.assertIn("staging path", self.body)
        self.assertIn("Do not write into", self.body)


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

    def test_the_directory_holds_exactly_the_templates_the_spec_ships(self):
        self.assertEqual(sorted(panel_files()),
                         sorted(name + ".json" for name in LIVE_PANELS + DRAFT_PANELS + DEFERRED_PANELS))

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

    def test_no_gating_template_seats_a_claude_family(self):
        """The owner's 2026-09-18 decision of record, and the templates are where it is visible.

        It is a rule about the panels a document is **promoted** on, and the 2026-09-19 ruling that
        added the draft pass said so in as many words: Opus is allowed on subscription early-draft
        reviewer seats, and the once-per-stage gate still seats no Claude family. So every template
        but the draft one is held to it, and the draft one is held to the opposite assertion below.
        """
        for name in panel_files():
            if name[:-5] in DRAFT_PANELS:
                continue
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


class DraftPanelTest(unittest.TestCase):
    """The draft template, as data. It is the one live panel two of the rules above exempt, so what
    it is instead has to be asserted rather than merely allowed.

    Every clause here is a decision somebody made and could quietly undo: that the draft panel
    carries the measured lens set rather than a cheaper one, that every seat is `claude` because
    the harness leg has one family and nothing else, that its family target is 1 because a
    single-family pass is first-class, and that it declares itself reference-free although it seats
    a citing lens — which is only honest because a reference-free draft run **retires** that seat
    instead of refusing, and `test_draft_mode.py` is where that is proved end to end.
    """

    LIVE_FIELDS = PanelTemplateShapeTest.LIVE_FIELDS

    def setUp(self):
        self.name = DRAFT_PANELS[0]
        self.panel = read_panel(self.name + ".json")

    def test_it_is_a_live_template_and_carries_the_full_field_set(self):
        self.assertNotIn("deferred", self.panel)
        for field, kind in self.LIVE_FIELDS.items():
            self.assertIsInstance(self.panel.get(field), kind, "missing or mistyped `{0}`".format(field))
        self.assertIn(self.panel["tier"], TIERS)
        self.assertIn(self.panel["reconciler"], ("host", "synthesis", "default"))

    def test_it_seats_the_same_four_lenses_as_the_measured_panel(self):
        """The draft pass is a cheap *run*, not a cheap *review*: same lenses, different leg."""
        self.assertEqual([seat["lens"] for seat in self.panel["seats"]],
                         [seat["lens"] for seat in read_panel("spec-review.json")["seats"]])

    def test_every_seat_is_claude_and_the_family_target_is_one(self):
        self.assertEqual({seat["family"] for seat in self.panel["seats"]}, {"claude"})
        self.assertEqual(self.panel["min_families"], 1,
                         "the harness leg serves one family; a target of 2 would warn about a "
                         "shortfall the mode is defined by")

    def test_it_is_the_only_shipped_template_that_seats_claude(self):
        others = [name for name in panel_files() if name[:-5] not in DRAFT_PANELS]
        for name in others:
            families = [seat.get("family")
                        for seat in (read_panel(name).get("seats") or [])
                        + (read_panel(name).get("optional_seats") or [])]
            with self.subTest(panel=name):
                self.assertNotIn("claude", families)

    def test_it_declares_itself_reference_free_although_it_seats_a_citing_lens(self):
        citing = set(run_panel.REFERENCE_REQUIRED_LENSES)
        self.assertTrue({seat["lens"] for seat in self.panel["seats"]} & citing)
        self.assertIs(self.panel["requires_references"], False,
                      "the draft pass is the mode for a seed document with no source of truth")

    def test_its_description_says_it_is_not_a_gate(self):
        self.assertIn("--draft", self.panel["description"])
        self.assertIn("not a gate", self.panel["description"])


class RegistryFamilyTest(unittest.TestCase):
    """Each model file says which family that model belongs to, and it must agree with the derived map.

    The field is what relabels a `--model`-pinned seat, and the family is what every agreement
    count in a reconciliation is computed over — so a registry that disagreed with the tier map
    would produce cross-family clusters that are nothing of the kind, silently.
    """

    def setUp(self):
        shipped = harness.shipped()
        self.registry = shipped["registry"].models
        self.config = shipped["entry"]

    def test_every_shipped_model_names_its_family(self):
        for model, entry in sorted(self.registry.items()):
            with self.subTest(model=model):
                self.assertTrue(entry.get("family"), "{0} carries no `family`".format(model))

    def test_the_registrys_family_is_the_family_the_config_seats_it_as(self):
        for tier, cells in self.config["tiers"].items():
            for family, model in cells.items():
                with self.subTest(tier=tier, family=family):
                    entry = self.registry.get(model) or {}
                    self.assertEqual(entry.get("family"), family,
                                     "{0} is seated as {1!r} at {2} and the registry calls it "
                                     "{3!r}".format(model, family, tier, entry.get("family")))

    def test_the_lookup_falls_back_to_the_config_when_the_registry_is_silent(self):
        """A workspace registry written before the field existed still labels a pinned seat."""
        model = self.config["tiers"]["frontier"]["glm"]
        family, source = seating_lib.family_for_model(model, self.config, registry=None)
        self.assertEqual((family, source), ("glm", "config"))


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

#!/usr/bin/env python3
"""The Judge stage: the `synthesis` persona supplying the judgment patch, end to end.

No network and no paid call. `reconcile.py` dispatches the persona through `dispatch.py`'s own
machinery, and the connector that machinery loads is `fake_backend.py`, scripted through a plan
file — so the patch the "model" returns is whatever the test wrote down, and the whole path from
the provisional clusters to `reconciliation.json` is exercised for real.

    python3 scripts/tests/test_synthesis.py
"""

import io
import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import harness  # noqa: E402
import reconcile  # noqa: E402
import run_panel  # noqa: E402
import dispatch  # noqa: E402
from lib import budget as budget_lib  # noqa: E402
from lib import judge as judge_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import runs as runs_lib  # noqa: E402

RUN_ID = "2026-09-18-1"


def patch(dispositions=None, **overrides):
    """A judgment patch against the one provisional cluster the two scripted seats produce.

    Both seats file the same `location` and the same `quote`, so the mechanical pass joins them into
    `P-1` with two members from two families. Both expected seats are members and the families are
    two, so it tiers `unanimous` — the whole commissioned panel, across families — and needs no
    label. Both findings are `judgment-call`, so the only disposition this author may give it is
    `flag-for-human`.
    """
    document = {
        "schema_version": "1",
        "run_id": RUN_ID,
        "author": "synthesis",
        "generated_at": "2026-09-19T09:00:00-04:00",
        "dispositions": dispositions if dispositions is not None else [{
            "cluster": "P-1",
            "disposition": "flag-for-human",
            "disposition_reason": "Both seats file it as a design fork and neither reference settles it.",
        }],
        "method_caveat": "Two families reported; no reference was withheld.",
    }
    document.update(overrides)
    return document


class JudgeStageTestCase(unittest.TestCase):
    """A real run directory from a real panel, then the judge stage over it."""

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.run_dir = self.workspace.path("reviews", RUN_ID)

    def run_panel(self, extra=None):
        """A two-seat panel, with the Judge stage off: this test drives it itself."""
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        argv = [
            "--panel", self.workspace.panel,
            "--artifact", self.workspace.artifact,
            "--ref", self.workspace.reference,
            "--out", self.run_dir,
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
            "--tier", "standard",
            "--autonomous",
            "--reconcile", "off",
        ] + list(extra or [])
        code, _out, err = _capture(run_panel.main, argv)
        self.assertEqual(code, 0, err)
        return code

    def judge(self, plan, extra=None):
        """Script the judgment call and run `reconcile.py` over the finished run directory."""
        self.workspace.reset_calls()
        self.workspace.plan(plan)
        argv = [
            "--run-dir", self.run_dir,
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
        ] + list(extra or [])
        return _capture(reconcile.main, argv)

    # --- helpers -------------------------------------------------------------------------------

    def manifest(self):
        return runs_lib.read_manifest(self.run_dir)

    def wrote(self, name):
        return os.path.isfile(os.path.join(self.run_dir, name))

    def read(self, name):
        with open(os.path.join(self.run_dir, name), "r", encoding="utf-8") as handle:
            return json.load(handle)


# --- who judges, decided before anything is dispatched ----------------------------------------

class ReconcilerResolutionTest(unittest.TestCase):
    """`default` means host when a human is attached and `synthesis` when nobody is."""

    def test_an_explicit_choice_beats_everything(self):
        self.assertEqual(judge_lib.resolve_reconciler("host", "synthesis", autonomous=True), "host")
        self.assertEqual(judge_lib.resolve_reconciler("synthesis", "host", autonomous=False), "synthesis")

    def test_the_runs_own_setting_is_next(self):
        self.assertEqual(judge_lib.resolve_reconciler(None, "host", autonomous=True), "host")
        self.assertEqual(judge_lib.resolve_reconciler(None, "synthesis", autonomous=False), "synthesis")

    def test_default_splits_on_whether_anybody_is_attached(self):
        self.assertEqual(judge_lib.resolve_reconciler("default", "default", autonomous=True), "synthesis")
        self.assertEqual(judge_lib.resolve_reconciler("default", "default", autonomous=False), "host")
        self.assertEqual(judge_lib.resolve_reconciler(None, None, autonomous=True), "synthesis")
        self.assertEqual(judge_lib.resolve_reconciler(None, None, autonomous=False), "host")

    def test_an_unknown_reconciler_is_a_usage_error(self):
        with self.assertRaises(judge_lib.JudgeError):
            judge_lib.resolve_reconciler("nobody", None, autonomous=True)

    def test_a_run_records_what_it_resolved_so_reconcile_does_not_re_ask(self):
        """The panel answers the question once. A separate process's stdin is not evidence."""
        workspace = harness.Workspace()
        workspace.apply_env()
        self.addCleanup(workspace.close)
        workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        run_dir = workspace.path("reviews", RUN_ID)
        code, _out, err = _capture(run_panel.main, [
            "--panel", workspace.panel, "--artifact", workspace.artifact, "--ref", workspace.reference,
            "--out", run_dir, "--config", workspace.config, "--models", workspace.registry,
            "--tier", "standard", "--reconciler", "host", "--reconcile", "off"])
        self.assertEqual(code, 0, err)
        manifest = runs_lib.read_manifest(run_dir)
        self.assertEqual(manifest["reconciler"], "host")
        self.assertIn("autonomous", manifest)


class SynthesisSeatTest(unittest.TestCase):
    """Where the judgment call is seated: the tier order, the family order, and the exclusion."""

    ENTRY = {"default_tier": "standard",
             "tiers": {"frontier": {"claude": "a/c", "openai": "o/f", "glm": "z/f"},
                       "standard": {"claude": "a/c", "openai": "o/s", "glm": "z/s"},
                       "fast": {"claude": "a/c", "openai": "o/x", "glm": "z/x"}}}

    def seat(self, **kwargs):
        return judge_lib.synthesis_seat(self.ENTRY, **kwargs)

    # --- the tier order ---------------------------------------------------------------------------

    def test_with_nothing_above_it_the_tier_is_the_configs_default(self):
        seat = self.seat()
        self.assertEqual((seat["tier"], seat["tier_source"]), ("standard", "config"))

    def test_the_runs_tier_beats_the_config(self):
        """The judge follows the run. A `fast` panel with a frontier judge costs more on the judge
        than on all four reviewers, which is not what asking for `fast` meant."""
        seat = self.seat(cli_tier="fast")
        self.assertEqual((seat["tier"], seat["tier_source"]), ("fast", "--tier"))

    def test_the_panels_tier_sits_below_the_runs(self):
        self.assertEqual(self.seat(panel={"tier": "frontier"})["tier_source"], "panel")
        self.assertEqual(self.seat(panel={"tier": "frontier"}, cli_tier="fast")["tier"], "fast")

    def test_a_template_that_names_a_judge_tier_outranks_a_blanket_run_tier(self):
        """An explicit `synthesis.tier` is a choice about the judgment; `--tier` aimed at the seats."""
        seat = self.seat(panel={"tier": "standard", "synthesis": {"tier": "frontier"}}, cli_tier="fast")
        self.assertEqual((seat["tier"], seat["tier_source"]), ("frontier", "synthesis"))

    def test_the_persona_frontmatter_is_the_last_level(self):
        entry = {"tiers": self.ENTRY["tiers"]}   # no default_tier
        seat = judge_lib.synthesis_seat(entry, persona_tier="fast")
        self.assertEqual((seat["tier"], seat["tier_source"]), ("fast", "persona"))

    def test_a_frontmatter_value_that_names_no_tier_contributes_nothing(self):
        entry = {"tiers": self.ENTRY["tiers"]}
        seat = judge_lib.synthesis_seat(entry, persona_tier="high")
        self.assertEqual((seat["tier"], seat["tier_source"]), ("frontier", "default"))

    def test_a_tier_the_config_does_not_offer_is_refused_and_names_the_level(self):
        with self.assertRaises(judge_lib.JudgeError) as caught:
            self.seat(cli_tier="glacial")
        self.assertIn("from --tier", str(caught.exception))

    # --- the family order -------------------------------------------------------------------------

    def test_it_prefers_a_family_the_panel_already_seated(self):
        """A model the run has already priced and the operator already chose, over a corner of the
        tier map nothing else in this run touches."""
        seat = self.seat(seated_families=["glm"])
        self.assertEqual((seat["family"], seat["family_source"]), ("glm", "panel"))

    def test_among_several_seated_families_declaration_order_decides(self):
        seat = self.seat(seated_families=["glm", "openai"])
        self.assertEqual((seat["family"], seat["family_source"]), ("openai", "panel"))

    def test_a_seated_family_with_no_cell_at_the_resolved_tier_is_skipped(self):
        entry = {"default_tier": "standard",
                 "tiers": {"standard": {"openai": "o/s"}, "fast": {"openai": "o/x", "glm": "z/x"}}}
        seat = judge_lib.synthesis_seat(entry, seated_families=["glm"])
        self.assertEqual((seat["family"], seat["family_source"]), ("openai", "config"))

    def test_a_panel_of_claude_seats_falls_through_to_the_config_rather_than_seating_claude(self):
        seat = self.seat(seated_families=["claude"])
        self.assertEqual((seat["family"], seat["family_source"]), ("openai", "config"))

    def test_with_no_seated_families_it_takes_the_first_non_claude_in_the_config(self):
        seat = self.seat()
        self.assertEqual((seat["family"], seat["family_source"]), ("openai", "config"))

    def test_a_template_may_name_the_family_and_that_wins(self):
        seat = self.seat(panel={"synthesis": {"family": "glm"}}, seated_families=["openai"])
        self.assertEqual((seat["family"], seat["family_source"]), ("glm", "synthesis"))

    def test_a_template_naming_a_family_with_no_cell_is_refused_rather_than_re_seated(self):
        entry = {"default_tier": "standard", "tiers": {"standard": {"openai": "o/s"}}}
        with self.assertRaises(judge_lib.JudgeError) as caught:
            judge_lib.synthesis_seat(entry, panel={"synthesis": {"family": "glm"}})
        self.assertIn("has no model there", str(caught.exception))

    def test_a_tier_with_only_claude_refuses_rather_than_seating_claude(self):
        entry = {"default_tier": "standard", "tiers": {"standard": {"claude": "a/c"}}}
        with self.assertRaises(judge_lib.JudgeError) as caught:
            judge_lib.synthesis_seat(entry, seated_families=["claude"])
        self.assertIn("non-`claude`", str(caught.exception))


# --- the call itself ------------------------------------------------------------------------------

class ScriptedPatchTest(JudgeStageTestCase):

    def test_a_valid_patch_is_written_and_both_reconciliation_files_follow(self):
        self.run_panel()
        code, out, err = self.judge({harness.SLOW_MODEL: [{"body": patch()}]})
        self.assertEqual(code, 0, err)

        self.assertTrue(self.wrote("judgment.json"), out)
        written = self.read("judgment.json")
        self.assertEqual(written["author"], "synthesis")
        self.assertEqual(written["run_id"], RUN_ID)

        self.assertTrue(self.wrote("reconciliation.json"))
        self.assertTrue(self.wrote("reconciliation.md"))
        document = self.read("reconciliation.json")
        self.assertEqual(document["reconciler"], "synthesis")
        self.assertEqual(document["counts"]["clusters"], 1)
        self.assertEqual(document["clusters"][0]["tier"], "unanimous",
                         "both expected seats, two families — the top of the ordered table")
        self.assertEqual(document["clusters"][0]["disposition"], "flag-for-human")

    def test_the_judgment_call_is_one_call_on_a_non_claude_seat(self):
        self.run_panel()
        self.judge({harness.SLOW_MODEL: [{"body": patch()}]})
        calls = self.workspace.calls()
        self.assertEqual(len(calls), 1, "one judgment call, and only one")
        self.assertEqual(calls[0]["model"], harness.SLOW_MODEL,
                         "the first non-claude family in the config's declaration order")
        self.assertFalse(calls[0]["is_repair"])

    def test_the_persona_is_shown_the_reports_the_clusters_and_the_pinned_artifact(self):
        """Steps 4 to 7 are not possible against the reports alone, so all three are in the prompt."""
        self.run_panel()
        self.judge({harness.SLOW_MODEL: [{"body": patch()}]})
        prompt_chars = self.workspace.calls()[0]["prompt_chars"]
        artifact = len(harness.ARTIFACT_TEXT)
        self.assertGreater(prompt_chars, artifact * 2,
                           "the prompt carries the artifact, the references and both reports in full")

    def test_the_system_message_carries_both_of_the_personas_context_files(self):
        self.run_panel()
        self.judge({harness.SLOW_MODEL: [{"body": patch()}]})
        system_chars = self.workspace.calls()[0]["system_chars"]
        reconciliation = os.path.getsize(os.path.join(SCRIPTS_DIR, os.pardir, "references", "reconciliation.md"))
        finding = os.path.getsize(os.path.join(SCRIPTS_DIR, os.pardir, "references", "finding-schema.md"))
        self.assertGreater(system_chars, reconciliation + finding,
                           "the persona body plus reconciliation.md plus finding-schema.md")


class JudgeAccountingTest(JudgeStageTestCase):

    def test_the_call_is_recorded_in_the_manifest_with_its_model_cost_and_attempts(self):
        self.run_panel()
        seats_cost = sum(seat["cost_usd"] for seat in self.manifest()["seats"])
        self.judge({harness.SLOW_MODEL: [{"body": patch(), "cost": 0.25}]})

        manifest = self.manifest()
        record = manifest["judge"]
        self.assertEqual(record["reviewer_id"], "synthesis")
        self.assertEqual(record["role"], "judge")
        self.assertEqual(record["leg"], "openrouter")
        self.assertEqual(record["model"], harness.SLOW_MODEL)
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["cost_usd"], 0.25)
        self.assertEqual(len(record["attempts"]), 1)
        self.assertAlmostEqual(manifest["cost_usd_total"], seats_cost + 0.25, places=6)

    def test_the_judge_is_not_a_seat_so_the_run_is_not_reported_under_seated(self):
        """`seats` is the `unanimous` denominator and every entry in it owes a report."""
        self.run_panel()
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": patch()}]})
        self.assertEqual(code, 0, err)
        self.assertNotIn("synthesis", [seat["reviewer_id"] for seat in self.manifest()["seats"]])
        document = self.read("reconciliation.json")
        self.assertEqual(document["missing_seats"], [])
        self.assertNotIn("synthesis", document["seats_expected"])

    def test_a_failed_judgment_call_still_carries_its_spend(self):
        """A seat that failed and is accounted at $0.00 is the defect this skill has closed twice."""
        self.run_panel()
        code, _out, _err = self.judge({harness.SLOW_MODEL: [
            {"raw": "not json at all", "cost": 0.4},
            {"raw": "still not json", "cost": 0.4},
        ]})
        self.assertEqual(code, 3)
        record = self.manifest()["judge"]
        self.assertEqual(record["status"], "failed")
        self.assertAlmostEqual(record["cost_usd"], 0.8, places=6)


class RepairTest(JudgeStageTestCase):

    def test_one_invalid_patch_earns_one_repair_re_ask_and_then_succeeds(self):
        self.run_panel()
        code, _out, err = self.judge({harness.SLOW_MODEL: [
            {"body": patch(dispositions=[])},
            {"body": patch()},
        ]})
        self.assertEqual(code, 0, err)
        calls = self.workspace.calls()
        self.assertEqual(len(calls), 2)
        self.assertFalse(calls[0]["is_repair"])
        self.assertTrue(calls[1]["is_repair"], "the second call quotes the previous response back")
        self.assertTrue(self.wrote("reconciliation.json"))
        self.assertEqual(self.manifest()["judge"]["status"], "ok")

    def test_the_repair_prompt_names_the_entry_that_failed(self):
        self.run_panel()
        self.judge({harness.SLOW_MODEL: [{"body": patch(dispositions=[])}, {"body": patch()}]})
        self.assertGreater(self.workspace.calls()[1]["prompt_chars"],
                           self.workspace.calls()[0]["prompt_chars"],
                           "the repair carries the original message plus the response and the errors")

    def test_two_invalid_patches_exit_three_and_write_nothing(self):
        self.run_panel()
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": patch(dispositions=[])}]})
        self.assertEqual(code, 3)
        self.assertIn("no disposition", err)
        self.assertEqual(len(self.workspace.calls()), 2, "one call and one repair, and no third")
        self.assertFalse(self.wrote("judgment.json"), "an invalid patch never reaches disk")
        self.assertFalse(self.wrote("reconciliation.json"))
        self.assertFalse(self.wrote("reconciliation.md"))

    def test_a_patch_naming_a_finding_nobody_filed_is_a_patch_error(self):
        self.run_panel()
        bad = patch()
        bad["contradictions"] = [{"cluster": "P-1", "contradicted_by": [
            {"reviewer_id": "consistency-kimi", "finding_id": "F99"}]}]
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": bad}]})
        self.assertEqual(code, 3)
        self.assertIn("F99", err)
        self.assertFalse(self.wrote("reconciliation.json"))


class SynthesisMayNotSettleAForkTest(JudgeStageTestCase):
    """The one asymmetry between the two authors, enforced from two directions."""

    def test_rulings_from_synthesis_is_a_hard_error_with_no_re_ask(self):
        self.run_panel()
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": patch(rulings=[{
            "cluster": "P-1", "ruling": "Take the second option.", "owner_to_confirm": True}])}]})
        self.assertEqual(code, 3)
        self.assertIn("rulings", err)
        self.assertEqual(len(self.workspace.calls()), 1,
                         "a hard error earns no repair re-ask: the persona would only say it again")
        self.assertFalse(self.wrote("judgment.json"))
        self.assertFalse(self.wrote("reconciliation.json"))
        self.assertEqual(self.manifest()["judge"]["status"], "refused")

    def test_a_patch_claiming_author_host_cannot_borrow_the_hosts_latitude(self):
        """The attack the two enforcement paths both key on `author` to stop.

        `reconcile_core` rejects `rulings` from `synthesis` and forces an all-`judgment-call`
        cluster to `flag-for-human` **for that author alone**. A patch that wrote `author: "host"`
        would satisfy neither rule, exit 0, record `reconciler: host`, and leave a design fork
        disposed `fix-now` with auto-apply condition 1 open on it. The author is assigned from what
        this script dispatched, never taken from the response.
        """
        self.run_panel()
        disowned = patch(author="host", dispositions=[{
            "cluster": "P-1", "disposition": "fix-now",
            "disposition_reason": "Claiming to be the host so this goes on the fix list."}])
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": disowned}]})
        self.assertEqual(code, 3, err)
        self.assertIn("claims `author: 'host'`", err, "the attempt is named, not quietly corrected")
        self.assertIn("always flags a judgment call", err, "and the synthesis rule still bit")
        self.assertFalse(self.wrote("judgment.json"))
        self.assertFalse(self.wrote("reconciliation.json"))
        self.assertIn("claims `author:", " ".join(self.manifest()["judge"]["errors"]),
                      "the disowned author is in the run's own record of the call")

    def test_a_patch_claiming_author_host_cannot_smuggle_rulings_either(self):
        self.run_panel()
        disowned = patch(author="host", rulings=[{
            "cluster": "P-1", "ruling": "Take the second option.", "owner_to_confirm": True}])
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": disowned}]})
        self.assertEqual(code, 3)
        self.assertIn("rulings", err)
        self.assertEqual(len(self.workspace.calls()), 1, "still a hard error, still no re-ask")
        self.assertFalse(self.wrote("judgment.json"))

    def test_the_author_is_corrected_on_an_otherwise_valid_patch_too(self):
        """Assigned, not defaulted: the field is right in the product even when nothing else failed."""
        self.run_panel()
        code, _out, err = self.judge({harness.SLOW_MODEL: [
            {"body": patch(author="host")},
            {"body": patch()},
        ]})
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("judgment.json")["author"], "synthesis")
        self.assertEqual(self.read("reconciliation.json")["reconciler"], "synthesis")

    def test_an_all_judgment_call_cluster_disposed_anything_else_is_a_patch_error(self):
        self.run_panel()
        fix_now = [{"cluster": "P-1", "disposition": "fix-now",
                    "disposition_reason": "Two families agreed, so I am settling it."}]
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": patch(dispositions=fix_now)}]})
        self.assertEqual(code, 3)
        self.assertIn("always flags a judgment call", err)
        self.assertFalse(self.wrote("reconciliation.json"))

    def test_the_same_disposition_from_the_host_is_allowed(self):
        """The rule binds the persona and not a host with an owner to answer to."""
        self.run_panel()
        host_patch = patch(author="host", dispositions=[{
            "cluster": "P-1", "disposition": "fix-now",
            "disposition_reason": "The owner settled this fork on 2026-09-18."}])
        path = os.path.join(self.run_dir, "judgment.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(host_patch, handle)
        code, _out, err = self.judge({}, extra=["--judgment", path])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("reconciliation.json")["clusters"][0]["disposition"], "fix-now")
        self.assertEqual(self.workspace.calls(), [], "a patch on disk is never re-judged")


class JudgePricingTest(unittest.TestCase):
    """The pre-flight prices the judgment call on the model that will actually make it.

    Two things had to be true and only one of them was. The **model** must be the judge's own, not
    the dearest seated one; and the judge's **tier** must follow the run, or a `--tier fast` panel
    pays frontier prices for its judgment and the operator's cheap run is not cheap.
    """

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.workspace.apply_env()
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })

    def edit_config(self, mutate):
        with open(self.workspace.config, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        mutate(config["openrouter"])
        with open(self.workspace.config, "w", encoding="utf-8") as handle:
            json.dump(config, handle)

    def panel(self, extra=None, panel_path=None):
        argv = [
            "--panel", panel_path or self.workspace.panel,
            "--artifact", self.workspace.artifact,
            "--ref", self.workspace.reference,
            "--out", self.workspace.path("reviews", RUN_ID),
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
            "--tier", "standard",
            "--autonomous", "--reconcile", "off",
        ] + list(extra or [])
        return _capture(run_panel.main, argv)

    def charged_line(self, out):
        return next(line for line in out.splitlines() if "synthesis call on" in line)

    def test_the_judge_follows_the_runs_tier_and_a_family_the_panel_seated(self):
        """`default_tier: frontier`, run at `standard`: the judge is standard, on a seated family."""
        self.edit_config(lambda entry: entry.update({
            "default_tier": "frontier",
            "tiers": dict(entry["tiers"], frontier={"openai": "test/frontier-only"}),
        }))
        code, out, err = self.panel()
        self.assertEqual(code, 0, err)
        self.assertIn("synthesis call on {0}".format(harness.SLOW_MODEL), out,
                      "the run asked for standard, so the judgment call is standard too")
        self.assertNotIn("test/frontier-only", out)

        seat = runs_lib.read_manifest(self.workspace.path("reviews", RUN_ID))["judge_seat"]
        self.assertEqual((seat["tier"], seat["tier_source"]), ("standard", "--tier"))
        self.assertEqual((seat["family"], seat["family_source"]), ("kimi", "panel"))
        self.assertEqual(seat["model"], harness.SLOW_MODEL)

    def test_the_judges_price_is_the_judges_own_model(self):
        code, out, _err = self.panel()
        self.assertEqual(code, 0)
        charged = float(self.charged_line(out).split("$")[1].split()[0])
        registry = registry_lib.load(self.workspace.registry)
        on_slow, _d = budget_lib.project_synthesis([], registry, 1, model=harness.SLOW_MODEL)
        on_fast, _d = budget_lib.project_synthesis([], registry, 1, model=harness.FAST_MODEL)
        self.assertGreater(on_slow, on_fast)
        self.assertGreater(charged, 0.0)
        self.assertIn("tier standard (--tier)", out,
                      "the line states the seat that was chosen and the level that chose it")

    def test_a_template_that_names_a_judge_tier_is_priced_at_that_tier(self):
        self.edit_config(lambda entry: entry.update({
            "tiers": dict(entry["tiers"], frontier={"kimi": harness.THIRD_MODEL}),
        }))
        with open(self.workspace.registry, "r", encoding="utf-8") as handle:
            models = json.load(handle)
        models["models"].update({harness.THIRD_MODEL: harness.default_models(third=True)[harness.THIRD_MODEL]})
        with open(self.workspace.registry, "w", encoding="utf-8") as handle:
            json.dump(models, handle)

        path = self.workspace.path("judge-panel.json")
        with open(self.workspace.panel, "r", encoding="utf-8") as handle:
            panel = json.load(handle)
        panel["synthesis"] = {"tier": "frontier"}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(panel, handle)

        code, out, err = self.panel(panel_path=path)
        self.assertEqual(code, 0, err)
        self.assertIn("synthesis call on {0}".format(harness.THIRD_MODEL), out)
        seat = runs_lib.read_manifest(self.workspace.path("reviews", RUN_ID))["judge_seat"]
        self.assertEqual((seat["tier"], seat["tier_source"]), ("frontier", "synthesis"))

    def test_synthesis_model_pins_what_the_projection_prices(self):
        code, out, err = self.panel(extra=["--synthesis-model", harness.FAST_MODEL])
        self.assertEqual(code, 0, err)
        self.assertIn("synthesis call on {0}".format(harness.FAST_MODEL), out)
        seat = runs_lib.read_manifest(self.workspace.path("reviews", RUN_ID))["judge_seat"]
        self.assertEqual(seat["family_source"], "--synthesis-model")
        self.assertEqual(seat["model"], harness.FAST_MODEL)

    def test_an_unpriced_judge_model_is_a_composition_error_like_an_unpriced_seat(self):
        """A paid call left out of the projection is a budget gate that does not gate."""
        path = self.workspace.path("judge-panel.json")
        with open(self.workspace.panel, "r", encoding="utf-8") as handle:
            panel = json.load(handle)
        panel["synthesis"] = {"family": "ghost"}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(panel, handle)
        self.edit_config(lambda entry: entry["tiers"]["standard"].update({"ghost": "test/unpriced-judge"}))

        code, _out, err = self.panel(panel_path=path)
        self.assertEqual(code, 1, err)
        self.assertIn("the judgment call resolves to test/unpriced-judge", err)
        self.assertEqual(self.workspace.calls(), [], "nothing is dispatched")

    def test_the_projection_line_states_the_seat_and_the_level_that_chose_it(self):
        """It used to assert "at the config's default_tier", which the ruling made false everywhere
        but the default tier. Every part of the seat moves per run, so the line reports rather than
        asserts."""
        code, out, err = self.panel()
        self.assertEqual(code, 0, err)
        line = self.charged_line(out)
        self.assertIn("synthesis call on {0}".format(harness.SLOW_MODEL), line)
        self.assertIn("tier standard (--tier)", line)
        self.assertIn("family kimi (panel)", line)
        self.assertNotIn("default_tier", line)

    def test_the_line_reports_a_template_block_and_a_pin_by_name_too(self):
        code, out, err = self.panel(extra=["--synthesis-model", harness.FAST_MODEL])
        self.assertEqual(code, 0, err)
        self.assertIn("(--synthesis-model)", self.charged_line(out))

    def test_an_unresolved_seat_is_still_labelled_a_guess(self):
        registry = registry_lib.load(self.workspace.registry)
        seats = [{"reviewer_id": "consistency-kimi", "model": harness.SLOW_MODEL, "prompt_tokens": 100}]
        projection = budget_lib.project(seats, registry, 5.0, with_synthesis=True)
        self.assertIn("GUESS", budget_lib.describe_synthesis_seat(projection["synthesis"]))

    def test_a_host_run_prices_no_judge_and_records_no_judge_seat(self):
        code, out, err = self.panel(extra=["--reconciler", "host"])
        self.assertEqual(code, 0, err)
        self.assertIn("not charged", out)
        self.assertNotIn("synthesis call on", out)
        self.assertIsNone(runs_lib.read_manifest(self.workspace.path("reviews", RUN_ID))["judge_seat"])


class ProjectionPromptSizeTest(unittest.TestCase):
    """The projection measures the prompt the run will actually send, declared context included."""

    EXTRA = "house-style.md"

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.paths = paths_lib.Paths(workspace=self.workspace.root)

    def test_persona_chars_sums_every_reference_the_persona_declares(self):
        baseline = run_panel.persona_chars("consistency", self.paths)

        packaged = os.path.join(harness.SKILL_DIR, "agents", "lens-consistency.md")
        with open(packaged, "r", encoding="utf-8") as handle:
            text = handle.read()
        before, marker, after = text.partition("context:\n  - finding-schema.md\n")
        self.assertTrue(marker)
        body = "# House style\n\n" + ("A sentence that costs prompt tokens. " * 200) + "\n"
        self.workspace.override(os.path.join("references", self.EXTRA), text=body)
        self.workspace.override(os.path.join("agents", "lens-consistency.md"),
                                text=before + "context:\n  - finding-schema.md\n  - {0}\n".format(self.EXTRA) + after)

        widened = run_panel.persona_chars("consistency", paths_lib.Paths(workspace=self.workspace.root))
        self.assertEqual(widened - baseline, len(body),
                         "the second declared reference is in the projected prompt, to the character")


class PersonaContextDedupeTest(unittest.TestCase):
    """A reference named twice is inlined once, and the operator is told once."""

    def setUp(self):
        self.workspace = harness.Workspace()
        self.addCleanup(self.workspace.close)
        self.workspace.override(os.path.join("references", "house-style.md"), text="# House style\n")
        self.paths = paths_lib.Paths(workspace=self.workspace.root)

    def resolve(self, context):
        notes = []
        resolved = dispatch.persona_context_paths(
            self.paths, {"context": context}, warn=notes.append)
        return [os.path.basename(p) for p in resolved], notes

    def test_a_repeated_reference_is_loaded_once_in_first_occurrence_order(self):
        names, notes = self.resolve(["house-style.md", "finding-schema.md", "house-style.md"])
        self.assertEqual(names, ["house-style.md", "finding-schema.md"],
                         "first occurrence keeps its place; the repeat is dropped")
        self.assertEqual(len(notes), 1, "said once, not once per repeat")
        self.assertIn("more than once", notes[0])
        self.assertIn("house-style.md", notes[0])

    def test_a_reference_repeated_three_times_still_says_it_once(self):
        names, notes = self.resolve(["finding-schema.md"] * 3)
        self.assertEqual(names, ["finding-schema.md"])
        self.assertEqual(len(notes), 1)

    def test_nothing_is_said_when_nothing_repeats(self):
        names, notes = self.resolve(["finding-schema.md", "house-style.md"])
        self.assertEqual(names, ["finding-schema.md", "house-style.md"])
        self.assertEqual(notes, [])


class RecordedSeatTest(JudgeStageTestCase):
    """The judge calls the model the budget gate priced, even when the config has moved since."""

    def config_cell(self, family, model):
        with open(self.workspace.config, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        config["openrouter"]["tiers"]["standard"][family] = model
        with open(self.workspace.config, "w", encoding="utf-8") as handle:
            json.dump(config, handle)

    def test_the_recorded_model_is_the_one_called_after_the_config_moves(self):
        """A config edited between the panel and the judgment must not redirect the paid call.

        Resolve priced `kimi` at `test/slow-model` and the gate cleared that. If the judge stage
        re-derived the seat from the config it would now call `test/third-model` — a model the
        projection never weighed, at a price nobody approved.
        """
        self.run_panel()
        recorded = self.manifest()["judge_seat"]
        self.assertEqual(recorded["model"], harness.SLOW_MODEL)

        # Move the cell the judge's family resolves to, and price the newcomer so nothing else fails.
        with open(self.workspace.registry, "r", encoding="utf-8") as handle:
            models = json.load(handle)
        models["models"].update(harness.default_models(third=True))
        with open(self.workspace.registry, "w", encoding="utf-8") as handle:
            json.dump(models, handle)
        self.config_cell("kimi", harness.THIRD_MODEL)

        code, _out, err = self.judge({
            harness.SLOW_MODEL: [{"body": patch()}],
            harness.THIRD_MODEL: [{"body": patch()}],
        })
        self.assertEqual(code, 0, err)
        called = [call["model"] for call in self.workspace.calls()]
        self.assertEqual(called, [harness.SLOW_MODEL],
                         "the recorded model is a pin, not a starting point for a second derivation")
        self.assertEqual(self.manifest()["judge"]["model"], harness.SLOW_MODEL)

    def test_the_recorded_seat_is_preferred_over_re_deriving_it(self):
        """Mutating the record must change the call, or nothing is reading it."""
        self.run_panel()
        manifest = self.manifest()
        manifest["judge_seat"] = dict(manifest["judge_seat"],
                                      family="xai", model=harness.FAST_MODEL,
                                      family_source="panel", tier="standard")
        runs_lib.write_manifest(self.run_dir, manifest)

        code, _out, err = self.judge({harness.FAST_MODEL: [{"body": patch()}]})
        self.assertEqual(code, 0, err)
        self.assertEqual([c["model"] for c in self.workspace.calls()], [harness.FAST_MODEL])
        record = self.manifest()["judge"]
        self.assertEqual((record["family"], record["model"]), ("xai", harness.FAST_MODEL))
        self.assertEqual(record["family_source"], "panel")

    def test_an_explicit_pin_still_beats_the_recorded_seat(self):
        self.run_panel()
        code, _out, err = self.judge({harness.FAST_MODEL: [{"body": patch()}]},
                                     extra=["--synthesis-model", harness.FAST_MODEL])
        self.assertEqual(code, 0, err)
        self.assertEqual([c["model"] for c in self.workspace.calls()], [harness.FAST_MODEL])
        self.assertEqual(self.manifest()["judge"]["family_source"], "--synthesis-model")

    def test_a_manifest_with_no_recorded_seat_falls_back_to_resolving_one(self):
        """Runs made before `judge_seat` existed still reconcile."""
        self.run_panel()
        manifest = self.manifest()
        del manifest["judge_seat"]
        runs_lib.write_manifest(self.run_dir, manifest)

        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": patch()}]})
        self.assertEqual(code, 0, err)
        self.assertEqual([c["model"] for c in self.workspace.calls()], [harness.SLOW_MODEL])


class OnDiskPatchTest(JudgeStageTestCase):
    """The other door to the same end state: a `judgment.json` already sitting in the directory.

    `reconcile.py` skips the judgment call entirely when a patch is on disk — that is the resume
    path, and it is the right behaviour. It also meant the author assignment never ran, so a patch
    a host session had left behind was merged with the host's latitude by a run that has no host.
    """

    def write_patch(self, document):
        path = os.path.join(self.run_dir, "judgment.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        return path

    def test_a_host_authored_patch_on_disk_is_refused_on_an_autonomous_run(self):
        self.run_panel()
        self.assertEqual(self.manifest()["reconciler"], "synthesis")
        self.write_patch(patch(author="host", dispositions=[{
            "cluster": "P-1", "disposition": "fix-now",
            "disposition_reason": "A host wrote this, so the fork rule does not bind."}]))

        code, _out, err = self.judge({})
        self.assertEqual(code, 3)
        self.assertIn("says `author: 'host'`", err)
        self.assertIn("--reconciler host", err, "the message names the way to say a human wrote it")
        self.assertIn("--judgment", err)
        self.assertFalse(self.wrote("reconciliation.json"))
        self.assertFalse(self.wrote("reconciliation.md"))
        self.assertEqual(self.workspace.calls(), [], "refused before any paid call")

    def test_the_same_file_is_merged_when_the_operator_says_a_host_wrote_it(self):
        self.run_panel()
        self.write_patch(patch(author="host", dispositions=[{
            "cluster": "P-1", "disposition": "fix-now",
            "disposition_reason": "The owner settled this fork on 2026-09-18."}]))

        code, _out, err = self.judge({}, extra=["--reconciler", "host"])
        self.assertEqual(code, 0, err)
        document = self.read("reconciliation.json")
        self.assertEqual(document["reconciler"], "host")
        self.assertEqual(document["clusters"][0]["disposition"], "fix-now",
                         "a host may dispose a fork; it has an owner to answer to")

    def test_an_explicit_judgment_path_is_the_other_way_to_say_so(self):
        self.run_panel()
        path = self.write_patch(patch(author="host"))
        code, _out, err = self.judge({}, extra=["--judgment", path])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("reconciliation.json")["reconciler"], "host")

    def test_a_synthesis_authored_patch_on_disk_is_the_resume_path_and_costs_nothing(self):
        """Why the check is narrow: the ordinary resume must not re-dispatch a judgment already made."""
        self.run_panel()
        self.write_patch(patch())
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": patch()}]})
        self.assertEqual(code, 0, err)
        self.assertEqual(self.workspace.calls(), [], "a judgment already on disk is never re-bought")
        self.assertEqual(self.read("reconciliation.json")["reconciler"], "synthesis")

    def test_a_host_run_leaves_its_own_patch_alone(self):
        """The check keys on the run's judgment, so a host run is not held to an author it never had."""
        self.run_panel(extra=["--reconciler", "host"])
        self.write_patch(patch(author="host"))
        code, _out, err = self.judge({})
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("reconciliation.json")["reconciler"], "host")


class HostModeTest(JudgeStageTestCase):

    def test_a_host_run_with_no_patch_writes_the_worksheet_and_makes_no_call(self):
        self.run_panel(extra=["--reconciler", "host"])
        code, out, err = self.judge({harness.SLOW_MODEL: [{"body": patch()}]})
        self.assertEqual(code, 3)
        self.assertTrue(self.wrote("judgment-request.json"), out)
        self.assertFalse(self.wrote("judgment.json"))
        self.assertEqual(self.workspace.calls(), [], "nobody is dispatched for a host judgment")
        self.assertIn("a judgment patch is required", err)

    def test_an_explicit_reconciler_flag_overrides_what_the_run_recorded(self):
        self.run_panel(extra=["--reconciler", "host"])
        code, _out, err = self.judge({harness.SLOW_MODEL: [{"body": patch()}]},
                                     extra=["--reconciler", "synthesis"])
        self.assertEqual(code, 0, err)
        self.assertTrue(self.wrote("reconciliation.json"))


class PipelineTest(unittest.TestCase):
    """`run_panel.py` finishing the pipeline itself, which is the whole point of an unattended run."""

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.run_dir = self.workspace.path("reviews", RUN_ID)

    def panel(self, extra=None):
        argv = [
            "--panel", self.workspace.panel,
            "--artifact", self.workspace.artifact,
            "--ref", self.workspace.reference,
            "--out", self.run_dir,
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
            "--tier", "standard",
        ] + list(extra or [])
        return _capture(run_panel.main, argv)

    def test_an_autonomous_run_reconciles_itself_in_one_command(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}, {"body": patch()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.panel(extra=["--autonomous"])
        self.assertEqual(code, 0, err)
        self.assertIn("Judge and Reconcile", out)
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "judgment.json")), out)
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "reconciliation.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "reconciliation.md")))

    def test_a_host_run_stops_after_collect_and_prints_the_command(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.panel(extra=["--autonomous", "--reconciler", "host"])
        self.assertEqual(code, 0, err)
        self.assertIn("reconcile.py --run-dir", out)
        self.assertFalse(os.path.isfile(os.path.join(self.run_dir, "reconciliation.json")))

    def test_reconcile_off_stops_even_an_autonomous_run(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.panel(extra=["--autonomous", "--reconcile", "off"])
        self.assertEqual(code, 0, err)
        self.assertIn("reconcile.py --run-dir", out)
        self.assertFalse(os.path.isfile(os.path.join(self.run_dir, "reconciliation.json")))

    def test_auto_apply_is_off_by_default(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}, {"body": patch()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, _out, err = self.panel(extra=["--autonomous"])
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.isfile(os.path.join(self.run_dir, "applied.md")),
                         "the one thing that writes to the document under review is never on by default")

    def test_auto_apply_on_without_the_authorship_assertion_is_a_composition_error(self):
        """Refused before the claim, so an unarmed operator's typo costs nothing."""
        code, _out, err = self.panel(extra=["--autonomous", "--auto-apply", "on"])
        self.assertEqual(code, 1)
        self.assertIn("--i-authored-this", err)
        self.assertFalse(os.path.isdir(self.run_dir), "nothing was claimed")

    def test_auto_apply_on_arms_the_gate_after_the_reconciliation_is_written(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}, {"body": patch()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.panel(extra=["--autonomous", "--auto-apply", "on", "--i-authored-this"])
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "applied.md")), out)
        # The scripted cluster is all `judgment-call`, so `synthesis` flagged it and condition 1
        # keeps it out. The gate ran, weighed it and said so — which is the point of the log.
        self.assertIn("nothing was applied", out)
        with open(self.workspace.artifact, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), harness.ARTIFACT_TEXT, "the document is untouched")

    def test_the_pin_reaches_the_child_reconcile(self):
        """`run_panel` shells out to `reconcile.py`; the pin has to travel with it."""
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}, {"body": patch()}],
        })
        code, out, err = self.panel(extra=["--autonomous", "--synthesis-model", harness.FAST_MODEL])
        self.assertEqual(code, 0, err)
        manifest = runs_lib.read_manifest(self.run_dir)
        self.assertEqual(manifest["judge"]["model"], harness.FAST_MODEL, out)
        self.assertEqual(manifest["judge"]["family_source"], "--synthesis-model")


class ReconcileCommandTest(unittest.TestCase):
    """One builder for the command this run makes and the command it prints for a host.

    They had drifted once: the child carried `--synthesis-model` and the printed line did not, so an
    operator copying the line would have got a judgment call the projection never priced. Asserted
    on the builder rather than through a subprocess, because that is where the drift lives.
    """

    def args(self, extra=None):
        return run_panel.parse_args([
            "--artifact", "a.md", "--out", "run",
            "--config", "/tmp/c.json", "--models", "/tmp/m.json", "--workspace", "/tmp/ws",
        ] + list(extra or []))

    def test_the_pin_is_on_both_forms(self):
        args = self.args(["--synthesis-model", "vendor/judge"])
        for judge in (True, False):
            argv = run_panel._reconcile_argv(args, "/runs/1", judge=judge)
            with self.subTest(judge=judge):
                self.assertIn("--synthesis-model", argv)
                self.assertEqual(argv[argv.index("--synthesis-model") + 1], "vendor/judge")
                self.assertIn("--synthesis-model vendor/judge", run_panel._printable(argv))

    def test_the_operator_paths_are_on_both_forms(self):
        """Read as given and not recoverable from the run directory, so a copied line needs them."""
        argv = run_panel._reconcile_argv(self.args(), "/runs/1", judge=False)
        for flag, value in (("--config", "/tmp/c.json"), ("--models", "/tmp/m.json"),
                            ("--workspace", "/tmp/ws")):
            with self.subTest(flag=flag):
                self.assertEqual(argv[argv.index(flag) + 1], value)

    def test_only_the_judge_form_asks_for_a_synthesis_judgment(self):
        """The printed line is for a host about to write the patch itself."""
        args = self.args()
        self.assertIn("--reconciler", run_panel._reconcile_argv(args, "/runs/1", judge=True))
        self.assertNotIn("--reconciler", run_panel._reconcile_argv(args, "/runs/1", judge=False))
        self.assertNotIn("--autonomous", run_panel._reconcile_argv(args, "/runs/1", judge=False))

    def test_nothing_optional_appears_when_nothing_was_given(self):
        args = run_panel.parse_args(["--artifact", "a.md", "--out", "run"])
        argv = run_panel._reconcile_argv(args, "/runs/1", judge=False)
        self.assertEqual(argv[1:], [run_panel.RECONCILE, "--run-dir", "/runs/1"])

    def test_a_path_with_a_space_survives_the_printed_form(self):
        args = run_panel.parse_args(["--artifact", "a.md", "--out", "run",
                                     "--workspace", "/tmp/two words"])
        printed = run_panel._printable(run_panel._reconcile_argv(args, "/runs/1", judge=False))
        self.assertIn("'/tmp/two words'", printed)


class PrintedCommandTest(PipelineTest):
    """The same claim, once, through the real console output."""

    def test_the_printed_reconcile_command_carries_the_pin(self):
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, out, err = self.panel(extra=["--autonomous", "--reconcile", "off",
                                           "--synthesis-model", harness.FAST_MODEL])
        self.assertEqual(code, 0, err)
        command = next(line for line in out.splitlines() if "reconcile.py --run-dir" in line)
        self.assertIn("--synthesis-model {0}".format(harness.FAST_MODEL), command)

    def test_a_failed_judgment_carries_its_exit_code_out_of_the_panel(self):
        """A run whose reconciliation was never written must not exit 0."""
        self.workspace.plan({
            harness.SLOW_MODEL: [{"body": harness.valid_report()}, {"raw": "not a patch"}],
            harness.FAST_MODEL: [{"body": harness.valid_report()}],
        })
        code, _out, _err = self.panel(extra=["--autonomous"])
        self.assertEqual(code, 3)
        self.assertFalse(os.path.isfile(os.path.join(self.run_dir, "reconciliation.json")))


def _capture(entry_point, argv):
    out_stream, err_stream = io.StringIO(), io.StringIO()
    saved_out, saved_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_stream, err_stream
    try:
        code = entry_point(argv)
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
    return code, out_stream.getvalue(), err_stream.getvalue()


if __name__ == "__main__":
    unittest.main(verbosity=2)

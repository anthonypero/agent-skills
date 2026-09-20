#!/usr/bin/env python3
"""`--smoke-test MODEL`: prove the pipeline for cents, and say loudly that it proves nothing else.

The owner's 2026-09-19 ruling asked for one run with every seat on a flash-class model, to exercise
the harness judge and the strict loader end to end before shipping. What makes that safe is not the
flag but the four things around it: the family target drops to 1 so the run does not warn about a
shortfall the operator asked for, the manifest is marked, the console says so before anything is
dispatched, and the reconciliation's method caveat opens by saying the run is not evidence.

**It is still priced and still gated, by both gates.** A smoke test that skipped the budget gate —
or the connector's spend gate, which is the other control in front of the money — would be a flag
that turns off exactly what it is there to run behind.

No network and no paid call: the connector is `fake_backend.py` throughout.

    python3 scripts/tests/test_smoke_test.py
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
import run_panel  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
from lib import runs as runs_lib  # noqa: E402
from lib import seating as seating_lib  # noqa: E402

RUN_ID = "2026-09-19-1"


def _help_text():
    """`run_panel.py --help`, captured. argparse writes it and then exits, so both are caught."""
    stream = io.StringIO()
    saved = sys.stdout
    sys.stdout = stream
    try:
        run_panel.main(["--help"])
    except SystemExit:
        pass
    finally:
        sys.stdout = saved
    return stream.getvalue()


class SmokeTestCase(unittest.TestCase):

    def setUp(self):
        # `kimi` and `xai` are the two families the test panel seats, and the registry knows the
        # third model as `glm` — so pinning every seat to it is a real relabel across two seats.
        self.workspace = harness.Workspace(models=harness.default_models(third=True))
        self.workspace.apply_env()
        self.addCleanup(self.workspace.close)
        self.workspace.edit_model(harness.THIRD_MODEL, family="glm")
        self.workspace.plan({harness.THIRD_MODEL: [
            {"body": harness.valid_report()}, {"body": harness.valid_report()}]})
        self.run_dir = self.workspace.path("reviews", RUN_ID)

    def run_panel(self, extra=None, out=None):
        argv = ["--panel", self.workspace.panel, "--artifact", self.workspace.artifact,
                "--out", out or self.run_dir, "--workspace", self.workspace.root,
                "--config", self.workspace.config, "--models", self.workspace.registry,
                "--tier", "standard", "--autonomous", "--reconcile", "off"] + list(extra or [])
        out_stream, err_stream = io.StringIO(), io.StringIO()
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_stream, err_stream
        try:
            code = run_panel.main(argv)
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        return code, out_stream.getvalue(), err_stream.getvalue()

    def manifest(self, out=None):
        return runs_lib.read_manifest(out or self.run_dir)


class PinningTest(SmokeTestCase):

    def test_every_seat_runs_on_the_one_model(self):
        code, _out, err = self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        self.assertEqual(code, 0, err)
        models = {seat["reviewer_id"]: seat["model"] for seat in self.manifest()["seats"]}
        self.assertEqual(set(models.values()), {harness.THIRD_MODEL}, models)
        self.assertEqual(sorted(call["model"] for call in self.workspace.calls()),
                         [harness.THIRD_MODEL, harness.THIRD_MODEL])

    def test_every_seats_family_is_relabelled_from_the_registry(self):
        """The label is what agreement counts are computed over. A seat reading `family: kimi`
        while running a GLM model manufactures cross-family clusters that do not exist."""
        self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        for seat in self.manifest()["seats"]:
            with self.subTest(seat=seat["reviewer_id"]):
                self.assertEqual(seat["family"], "glm")
                self.assertEqual(seat["family_relabel"]["source"], "registry")
                self.assertEqual(seat["family_relabel"]["to"], "glm")
                self.assertTrue(seat["model_pinned"])

    def test_the_reviewer_ids_do_not_move(self):
        """`reviewer_id` is minted from what the seat *asked for*, so a pin must not rename it —
        the report filename and the manifest key would change halfway through a run."""
        self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        self.assertEqual(sorted(seat["reviewer_id"] for seat in self.manifest()["seats"]),
                         ["adversarial-xai", "consistency-kimi"])

    def test_the_family_target_drops_to_one(self):
        self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        block = self.manifest()["min_families"]
        self.assertEqual(block["target"], 1)
        self.assertEqual(block["reporting"], 1)
        self.assertEqual(block["families_reporting"], ["glm"])

    def test_an_explicit_min_families_still_wins(self):
        """The flag lowers a default; it does not overrule an operator who said a number."""
        self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL, "--min-families", "2"])
        self.assertEqual(self.manifest()["min_families"]["target"], 2)

    def test_the_manifest_is_marked_at_the_top_level(self):
        self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        manifest = self.manifest()
        self.assertIs(manifest["smoke_test"], True)
        self.assertEqual(manifest["smoke_test_model"], harness.THIRD_MODEL)

    def test_an_ordinary_run_is_not_marked(self):
        self.workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}],
                             harness.FAST_MODEL: [{"body": harness.valid_report()}]})
        self.run_panel()
        self.assertIs(self.manifest()["smoke_test"], False)
        self.assertIsNone(self.manifest()["smoke_test_model"])

    def test_the_banner_says_so_before_anything_is_dispatched(self):
        _code, out, _err = self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        self.assertIn("SMOKE TEST", out)
        self.assertIn(harness.THIRD_MODEL, out)
        self.assertLess(out.index("SMOKE TEST"), out.index("Cost pre-flight"),
                        "the banner comes before the projection, which comes before the money")

    def test_the_banner_and_the_help_name_both_gates_and_not_only_the_budget(self):
        """The flag is gated by the connector's spend gate as well, and its own text used to say
        `still gated by the budget` — which reads as a list of one, and left an operator on a
        metered endpoint expecting a smoke test to be the one run that needs no approval."""
        _code, out, _err = self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        banner = out[out.index("SMOKE TEST"):out.index("Cost pre-flight")]
        self.assertIn("budget", banner)
        self.assertIn("--approve-spend", banner)

        # The option's own entry in the options list, not the usage line above it and not the
        # cross-reference to this flag inside `--draft`'s help paragraph below it. Sliced on the
        # metavar form argparse prints once, for exactly that reason.
        help_text = _help_text()
        start = help_text.index("--smoke-test MODEL", help_text.index("options:"))
        smoke = help_text[start:help_text.index("--draft", start)]
        self.assertIn("budget", smoke)
        self.assertIn("spend gate", smoke)


class StillPricedTest(SmokeTestCase):

    def test_it_is_priced_like_any_other_run(self):
        self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        projection = self.manifest()["projection"]
        self.assertEqual(sorted(row["model"] for row in projection["per_seat"]),
                         [harness.THIRD_MODEL, harness.THIRD_MODEL])
        self.assertGreater(projection["projection_usd"], 0)

    def test_the_budget_gate_still_refuses_an_over_budget_smoke_run(self):
        """A flag that turned the money control off would be the opposite of what this is for."""
        code, _out, err = self.run_panel(
            extra=["--smoke-test", harness.THIRD_MODEL, "--budget-usd", "0.0001"])
        self.assertEqual(code, run_panel.EXIT_BUDGET, err)
        self.assertIn("over budget", err)
        self.assertEqual(self.workspace.calls(), [], "refused before any call")
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "budget-refusal.json")))

    def test_an_unpriceable_smoke_model_is_a_composition_error(self):
        code, _out, err = self.run_panel(extra=["--smoke-test", "test/not-in-the-registry"])
        self.assertEqual(code, run_panel.EXIT_COMPOSITION)
        self.assertIn("test/not-in-the-registry", err)


class RenderedCaveatTest(SmokeTestCase):
    """End to end: the flag on the command line reaches the document a human reads.

    The judgment is written by hand here rather than dispatched, because what is under test is the
    manifest flag travelling into the rendered caveat — not the judge.
    """

    def test_the_rendered_reconciliation_says_the_run_is_not_evidence(self):
        code, _out, err = self.run_panel(extra=["--smoke-test", harness.THIRD_MODEL])
        self.assertEqual(code, 0, err)

        judgment = {
            "schema_version": "1",
            "run_id": RUN_ID,
            "author": "host",
            "generated_at": "2026-09-19T09:00:00-04:00",
            "dispositions": [{"cluster": "P-1", "disposition": "flag-for-human",
                              "disposition_reason": "Both seats file it as a design fork."}],
            # A smoke test pins every seat to one model, so both seats are one family and the
            # cluster tiers `same-family` — which owes a label exactly as a singleton does. The
            # fixture supplies one; a patch that did not would be a patch error, which is the point.
            "singleton_labels": [{"cluster": "P-1", "label": "blind-spot-catch",
                                  "reason": "Two lenses on one model is one mind agreeing with itself."}],
            "method_caveat": "One model behind every seat.",
        }
        with open(os.path.join(self.run_dir, "judgment.json"), "w", encoding="utf-8") as handle:
            json.dump(judgment, handle)

        import reconcile
        out_stream, err_stream = io.StringIO(), io.StringIO()
        saved_out, saved_err = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out_stream, err_stream
        try:
            code = reconcile.main(["--run-dir", self.run_dir, "--config", self.workspace.config,
                                   "--models", self.workspace.registry, "--reconciler", "host"])
        finally:
            sys.stdout, sys.stderr = saved_out, saved_err
        self.assertEqual(code, 0, err_stream.getvalue())

        with open(os.path.join(self.run_dir, "reconciliation.md"), "r", encoding="utf-8") as handle:
            rendered = handle.read()
        caveat = rendered.split("## Method caveat", 1)[1].strip()
        self.assertTrue(caveat.startswith("**This run is a smoke test"),
                        "the warning is the first thing under the heading: " + caveat[:120])
        self.assertIn("not evidence", caveat)
        self.assertIn("One model behind every seat.", caveat, "the supplier's own caveat survives")


class MethodCaveatTest(unittest.TestCase):
    """What a reader of the reconciliation is told, which is the whole point of marking the run."""

    def test_the_caveat_opens_by_saying_the_run_is_not_evidence(self):
        """**Opens**, literally: it is the first paragraph, not one appended below the rest.

        Every other appendix qualifies the supplied caveat and belongs after it. This one says the
        supplied caveat is about a run that proves nothing, so a reader who stops after the first
        paragraph has to have read it.
        """
        caveat = core.method_caveat("Two seats reported.", {"smoke_test": True, "seats": []})
        first = caveat.split("\n\n", 1)[0]
        self.assertIn("smoke test", first)
        self.assertIn("not evidence", first)
        self.assertIn("Do not cite", first)
        self.assertLess(caveat.index("smoke test"), caveat.index("Two seats reported."),
                        "the warning must come before the judgment supplier's own caveat")

    def test_it_still_opens_the_caveat_when_the_manifest_appends_its_own_lines(self):
        """The appendices — substitutions, missing seats, the family target, cost — all sit below."""
        manifest = {
            "smoke_test": True,
            "seats": [{"reviewer_id": "consistency-kimi", "status": "failed",
                       "failure_reason": "context-overflow", "family": "glm"}],
            "min_families": {"target": 1, "seated": 1, "reporting": 1,
                             "families_seated": ["glm"], "families_reporting": ["glm"]},
        }
        caveat = core.method_caveat("Two seats reported.", manifest)
        self.assertTrue(caveat.startswith("**This run is a smoke test"), caveat[:120])

    def test_it_says_why_the_agreement_counts_are_worthless(self):
        caveat = core.method_caveat("", {"smoke_test": True, "seats": []})
        self.assertIn("one mind agreeing with itself", caveat)

    def test_an_ordinary_run_says_none_of_it(self):
        self.assertEqual(core.method_caveat("As it was.", {"seats": []}), "As it was.")
        self.assertEqual(core.method_caveat("As it was.", {"smoke_test": False, "seats": []}),
                         "As it was.")


class FamilyForModelTest(unittest.TestCase):
    """The lookup behind the relabel, on its own."""

    CONFIG = {"tiers": {"standard": {"kimi": "m/kimi", "glm": "m/glm"}}}

    class _Registry(object):
        def __init__(self, data):
            self.data = data

        def get(self, model):
            return self.data.get(model)

    def test_the_registry_answers_first(self):
        registry = self._Registry({"m/kimi": {"family": "moonshot"}})
        self.assertEqual(seating_lib.family_for_model("m/kimi", self.CONFIG, registry),
                         ("moonshot", "registry"))

    def test_the_config_answers_when_the_registry_is_silent(self):
        registry = self._Registry({"m/kimi": {}})
        self.assertEqual(seating_lib.family_for_model("m/kimi", self.CONFIG, registry),
                         ("kimi", "config"))

    def test_a_model_neither_knows_is_unknown_rather_than_wrong(self):
        self.assertEqual(seating_lib.family_for_model("m/nobody", self.CONFIG, None), (None, None))

    def test_an_unpinned_seat_is_never_relabelled(self):
        """The pass runs over pinned seats only: an ordinary seat's family is the template's word."""
        seats = seating_lib.resolve(
            {"name": "t", "seats": [{"lens": "consistency", "family": "kimi"}]},
            {"type": "openai_compat", "tiers": self.CONFIG["tiers"]}, cli_tier="standard",
            registry=self._Registry({"m/kimi": {"family": "moonshot"}}))
        self.assertEqual(seats[0]["family"], "kimi")
        self.assertIsNone(seats[0]["family_relabel"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

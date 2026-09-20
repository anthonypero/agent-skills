#!/usr/bin/env python3
"""`same-family`: the tier a cluster gets when every seat that raised it is one family.

**The tier used to be called `majority` and it was the one same-family tier that skipped the
labelling duty.** It is unreachable with two families in it — `consensus` catches those one row
above — so the name promised corroboration the tier cannot carry, and nothing demanded a label for
it. On an ordinary four-family panel that is an edge; on a **draft pass**, where every seat is one
family by construction, it is the normal case, and every multi-lens agreement rendered as
`**unlabelled.**` under a heading reading "Single-seat should-fixes", beneath a preamble saying an
unlabelled single-seat cluster is not an allowed output. Three lies in one row.

So the tier is named for what it is, it is in `LABELLED_TIERS`, it renders under its own heading,
and the judgment supplier is asked for a label. This file holds all four to that, plus the rule
that makes them one rule: **one family agreeing with itself is one mind, whether it is one seat or
four.**

No network and no model: every report here is a file on disk.

    python3 scripts/tests/test_same_family_tier.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(TESTS_DIR)
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

import reconcile  # noqa: E402
from lib import judge as judge_lib  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
from lib import schema as schema_lib  # noqa: E402

RUN_ID = "2026-09-19-1"

ARTIFACT = """# A draft under review

## Budget
The budget is stated in one place and contradicted in another.

## Seats
The config shape has no family dimension, so the seat constraints cannot resolve.
"""

SHARED_QUOTE = "The budget is stated in one place and contradicted in another."
OTHER_QUOTE = "The config shape has no family dimension, so the seat constraints cannot resolve."


def finding(location, quote, finding_id="F1", severity="should-fix"):
    return {
        "id": finding_id,
        "location": location,
        "quote": quote,
        "claim": "{0} is stated twice and the two do not agree.".format(location),
        "citation": None,
        "severity": severity,
        "reasoning": "A builder cannot tell which figure binds.",
        "suggested_change": "State it once.",
        # `judgment-call` carries `fork`, which the ingest validator requires; the author here is
        # `host`, which is the one author allowed to dispose a fork anything but flag-for-human.
        "change_kind": "judgment-call",
        "literal_edit": None,
        "confidence": "high",
        "externally_verified": False,
        "tags": ["budget", "fork"],
    }


def report(reviewer_id, lens, family, findings):
    return {
        "schema_version": "1",
        "reviewer_id": reviewer_id,
        "lens": lens,
        "family": family,
        "model": "test/one-model",
        "leg": "harness",
        "artifact": "draft.md",
        "artifact_revision": None,
        "verdict": "fix-then-ship",
        "summary": "One defect found.",
        "findings": findings,
        "method_notes": "scripted",
    }


class OneFamilyPanelCase(unittest.TestCase):
    """A four-seat panel, every seat on `claude` — the shape a draft pass always has."""

    # Four non-citing lenses: `fidelity` would want a `citation` on every finding, which is a
    # different rule and not this file's subject.
    SEATS = (("alternatives-claude", "alternatives"), ("buildability-claude", "buildability"),
             ("consistency-claude", "consistency"), ("adversarial-claude", "adversarial"))

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="panel-review-same-family-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.run_dir = os.path.join(self.root, "reviews", RUN_ID)
        os.makedirs(self.run_dir)

    def build(self, agreeing):
        """Write four one-family reports; `agreeing` many anchor on the same defect."""
        _write_json(os.path.join(self.run_dir, "manifest.json"), {
            "run_id": RUN_ID,
            "panel": "draft-review",
            "artifact": "draft.md",
            "artifact_revision": None,
            "draft": True,
            "seats": [{"reviewer_id": reviewer_id, "lens": lens, "family": "claude",
                       "status": "ok", "leg": "harness"}
                      for reviewer_id, lens in self.SEATS],
        })
        for index, (reviewer_id, lens) in enumerate(self.SEATS):
            location, quote = ("Budget", SHARED_QUOTE) if index < agreeing else ("Seats", OTHER_QUOTE)
            _write_json(os.path.join(self.run_dir, reviewer_id + ".json"),
                        report(reviewer_id, lens, "claude", [finding(location, quote)]))

    def reconcile(self, patch):
        return core.reconcile(self.run_dir, patch, ARTIFACT)

    def provisional_ids(self):
        """`{tier: provisional id}` for this run, so a patch names what the matcher actually made."""
        _document, _errors, context = core.reconcile(self.run_dir, None, ARTIFACT)
        return {cluster["provisional_tier"]: cluster["provisional_id"]
                for cluster in context["provisional"]}

    def patch(self, label_tiers=()):
        """A patch disposing every cluster and labelling the tiers named."""
        ids = self.provisional_ids()
        document = {
            "schema_version": "1",
            "run_id": RUN_ID,
            "author": "host",
            "generated_at": "2026-09-19T09:00:00-04:00",
            "dispositions": [{"cluster": c, "disposition": "fix-now",
                              "disposition_reason": "A determinate fix."} for c in sorted(ids.values())],
            "method_caveat": "One family behind four lenses.",
        }
        if label_tiers:
            document["singleton_labels"] = [
                {"cluster": ids[tier], "label": "blind-spot-catch",
                 "reason": "One family under several lenses is one mind."} for tier in label_tiers]
        return document


class TierTableTest(unittest.TestCase):
    """`compute_tier` itself, over the three one-family rows and the two cross-family ones."""

    def tier(self, reviewers, families, expected_seats=4, reporting=4):
        """`reviewers` are seat names; the panel's expected and reporting sets are `s0…`, plus the
        named reviewers, so a cluster is `unanimous` only when it names every expected seat."""
        seats = list(reviewers) + ["s{0}".format(n) for n in range(expected_seats - len(reviewers))]
        cluster = {"members": [_Member(r, f) for r, f in zip(reviewers, families)]}
        return core.compute_tier(cluster, seats, seats[:reporting])

    def test_three_of_four_seats_on_one_family_is_same_family(self):
        self.assertEqual(self.tier(["a", "b", "c"], ["claude"] * 3), "same-family")

    def test_two_of_four_seats_on_one_family_is_corroborated_same_family(self):
        self.assertEqual(self.tier(["a", "b"], ["claude"] * 2), "corroborated-same-family")

    def test_one_seat_is_a_singleton_however_many_are_reporting(self):
        self.assertEqual(self.tier(["a"], ["claude"]), "singleton")
        self.assertEqual(self.tier(["a"], ["claude"], expected_seats=1, reporting=1), "singleton")

    def test_two_families_outrank_any_count_of_one(self):
        self.assertEqual(self.tier(["a", "b"], ["claude", "openai"]), "consensus")
        self.assertEqual(self.tier(["a", "b", "c", "d"], ["claude", "openai", "glm", "xai"]),
                         "unanimous", "every expected seat, across families")

    def test_the_tier_is_unreachable_with_two_families_in_it(self):
        """Which is why it is not called `majority`: the name promised something the row cannot
        carry, and `consensus` has already taken every cross-family cluster one row above."""
        for count in range(2, 5):
            families = ["claude"] * (count - 1) + ["openai"]
            self.assertNotEqual(self.tier(list("abcd")[:count], families), "same-family")

    def test_every_one_family_tier_is_a_labelled_tier(self):
        for tier in ("singleton", "same-family", "corroborated-same-family"):
            self.assertIn(tier, core.LABELLED_TIERS, tier)
            self.assertIn(tier, core.TIERS, tier)
        for tier in ("consensus", "unanimous"):
            self.assertNotIn(tier, core.LABELLED_TIERS, tier)

    def test_the_product_schema_knows_the_tier(self):
        path = os.path.join(SKILL_DIR, "schemas", "reconciliation.schema.json")
        document = schema_lib.load(path)
        enum = document["properties"]["clusters"]["items"]["properties"]["tier"]["enum"]
        self.assertEqual(sorted(enum), sorted(core.TIERS))


class LabelIsDemandedTest(OneFamilyPanelCase):
    """A same-family cluster with no label is a patch error, exactly as a singleton is."""

    def test_an_unlabelled_same_family_cluster_is_refused_and_named(self):
        self.build(agreeing=3)
        document, errors, _context = self.reconcile(self.patch(label_tiers=["singleton"]))
        self.assertIsNone(document)
        self.assertTrue(any("same-family" in error and "carries no label" in error
                            for error in errors), errors)

    def test_labelling_it_produces_the_document(self):
        self.build(agreeing=3)
        document, errors, _context = self.reconcile(self.patch(label_tiers=["same-family", "singleton"]))
        self.assertEqual(errors, [])
        cluster = _by_tier(document, "same-family")
        self.assertEqual(cluster["n_reviewers"], 3)
        self.assertEqual(cluster["n_families"], 1)
        self.assertEqual(cluster["singleton_label"], "blind-spot-catch")
        self.assertTrue(cluster["singleton_reason"].strip())

    def test_the_worksheet_asks_for_the_label_before_the_patch_is_written(self):
        """The supplier is told what it owes rather than refused after the fact."""
        self.build(agreeing=3)
        _document, _errors, context = core.reconcile(self.run_dir, None, ARTIFACT)
        request = core.judgment_request(context)
        owed = {entry["provisional_id"]: entry["required"] for entry in request["clusters"]}
        same_family = [entry for entry in request["clusters"]
                       if entry["provisional_tier"] == "same-family"]
        self.assertEqual(len(same_family), 1, request["clusters"])
        self.assertIn("singleton_labels", owed[same_family[0]["provisional_id"]])
        self.assertIn("same-family", request["note"])


class RenderingTest(OneFamilyPanelCase):
    """Where the cluster lands in the document a human reads."""

    def setUp(self):
        super(RenderingTest, self).setUp()
        self.build(agreeing=3)
        document, errors, _context = self.reconcile(self.patch(label_tiers=["same-family", "singleton"]))
        self.assertEqual(errors, [])
        self.document = document
        self.rendered = reconcile.render_markdown(document)

    def test_it_renders_under_a_heading_that_says_what_it_is(self):
        self.assertIn("## Same-family should-fixes — one family, uncorroborated", self.rendered)
        self.assertIn("one mind agreeing with itself", self.rendered)

    def test_it_never_renders_under_the_single_seat_heading(self):
        same_family = _by_tier(self.document, "same-family")
        section = self.rendered.split("## Single-seat should-fixes", 1)[1].split("## Nice", 1)[0]
        self.assertNotIn(same_family["id"], section)

    def test_the_label_is_rendered_rather_than_the_word_unlabelled(self):
        section = self.rendered.split("## Same-family should-fixes", 1)[1].split("## Single-seat", 1)[0]
        self.assertIn("**blind-spot-catch.**", section)
        self.assertNotIn("**unlabelled.**", section)

    def test_a_single_seat_cluster_still_has_its_own_section(self):
        singleton = _by_tier(self.document, "singleton")
        section = self.rendered.split("## Single-seat should-fixes", 1)[1].split("## Nice", 1)[0]
        self.assertIn(singleton["id"], section)


class JudgeInstructionsTest(unittest.TestCase):
    """Every place the judgment supplier is told what it owes names the tier."""

    def test_the_repair_re_ask_names_it(self):
        prompt = judge_lib.build_repair_prompt("original", "{}", ["something failed"])
        self.assertIn("same-family", prompt)

    def test_the_shipped_personas_name_it(self):
        for name in ("synthesis.md", "judge.md"):
            with open(os.path.join(SKILL_DIR, "agents", name), "r", encoding="utf-8") as handle:
                body = handle.read()
            self.assertIn("same-family", body, name)

    def test_the_reference_tier_table_names_it_and_no_longer_names_majority(self):
        with open(os.path.join(SKILL_DIR, "references", "reconciliation.md"), "r",
                  encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("| 3 | `same-family` |", body)
        self.assertNotIn("| 3 | `majority` |", body)


class _Member(object):
    """The two fields `compute_tier` reads off a cluster member."""

    def __init__(self, reviewer_id, family):
        self.reviewer_id = reviewer_id
        self.family = family


def _by_tier(document, tier):
    picked = [c for c in document["clusters"] if c["tier"] == tier]
    if len(picked) != 1:
        raise AssertionError("expected one {0} cluster, got {1}".format(
            tier, [(c["id"], c["tier"]) for c in document["clusters"]]))
    return picked[0]


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Acceptance (a) — the deterministic replay control.

Feeds the four validated reports of the 2026-09-18 run 1 panel, plus the `judgment.json` fixture
transcribed from that run's hand reconciliation, through `reconcile_core` and checks the product
against the hand document and its erratum. No models are called and nothing is written: the judgment
a live run would get from the host or the `synthesis` persona comes from the fixture, and the code
under test does what it always does — cluster mechanically, merge, recompute tiers, validate, mint
ids, compute the verdict.

A failure here is a clustering bug — membership, tiering or labelling that the same reports and the
same fixture do not support — and it blocks `reconcile.py` shipping. Disposition differences are not
failures: four of the hand document's clusters were superseded by decisions the owner took during the
run, which a script cannot know, so every difference is printed as a diff instead.

**The matcher is pinned separately from the product.** The fixture's twelve `claim_joins` and four
`splits` can transform any partition into any other, so a test that only checks the merged product
would pass against a matcher that joined nothing at all. Two tests assert the PROVISIONAL partition
instead, before any of the patch is applied:

- `test_the_mechanical_matcher_joins_exactly_these` pins the nine multi-finding clusters by
  `reviewer_id` + `finding_id` set, the key each was joined on, `match_key: none` on every cluster
  of one, and the `P-1…P-41` id sequence. Four of the nine joins are known-spurious and are split
  apart again by the fixture (P-1, P-4, P-9, P-28); they record what the matcher does today, not
  what it ought to do.
- `test_the_matcher_does_not_join_these` pins two near misses that must stay apart, at 39 shared
  characters each. Without this half only a *raised* threshold would fail.

Together they bracket `QUOTE_OVERLAP_MIN` to **[40, 75]** — measured by sweeping the threshold over
this corpus, not reasoned about: at 39 the two near misses join, and at 76 `P-4` loses
`adversarial-glm` F12 and the pinned nine change. No fixture here distinguishes 60 from 45. They
also fail on a disabled `location_match`, which a product-only test would not catch at all.

    python3 -m unittest discover -s scripts/tests     # from the skill root
    python3 scripts/tests/test_replay.py
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))

from lib import reconcile_core as core  # noqa: E402

DEFAULT_RUN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(SKILL_DIR)),
    ".agents", "subprojects", "panel-review", "reviews", "v1-spec", "2026-09-18-1",
)
RUN_DIR = os.environ.get("PANEL_REVIEW_REPLAY_DIR", DEFAULT_RUN_DIR)

# The pinned artifact the four seats read. The anchor check is not optional, so the replay runs with
# it: `adversarial-glm F6` is the one quote the validator truncated at the 300-character cap, and it
# is the case that proves the truncation marker is recognised before NFKC rewrites it.
DEFAULT_ARTIFACT = os.path.join(
    os.path.dirname(os.path.dirname(SKILL_DIR)),
    ".agents", "subprojects", "panel-review", "design", "v1-spec.md",
)
ARTIFACT = os.environ.get("PANEL_REVIEW_REPLAY_ARTIFACT", DEFAULT_ARTIFACT)

FIDELITY = "fidelity-claude"
BUILDABILITY = "buildability-openai"
CONSISTENCY = "consistency-kimi"
ADVERSARIAL = "adversarial-glm"


def members(*pairs):
    return frozenset(pairs)


# The hand document's seat attributions, transcribed by hand from the tables of
# `reviews/v1-spec/2026-09-18-1/reconciliation.md` and its erratum. Keys are that document's own
# cluster ids, which the replay is free to renumber; only membership is asserted.
HAND_MEMBERS = {
    "BK-1": members((BUILDABILITY, "F7")),
    "SF-1": members((FIDELITY, "F3"), (BUILDABILITY, "F1"), (ADVERSARIAL, "F4")),
    "SF-2": members((FIDELITY, "F4"), (BUILDABILITY, "F4")),
    "SF-3": members((FIDELITY, "F1"), (ADVERSARIAL, "F3")),
    "SF-4": members((FIDELITY, "F5"), (ADVERSARIAL, "F5")),
    "SF-5": members((CONSISTENCY, "F6"), (ADVERSARIAL, "F1"), (ADVERSARIAL, "F2")),
    "SF-6": members((BUILDABILITY, "F2"), (CONSISTENCY, "F5")),
    "SF-7": members((BUILDABILITY, "F3"), (BUILDABILITY, "F6"), (ADVERSARIAL, "F8")),
    "SF-8": members((BUILDABILITY, "F8"), (ADVERSARIAL, "F13")),
    "SF-9": members((BUILDABILITY, "F9"), (BUILDABILITY, "F10"), (ADVERSARIAL, "F11")),
    "SF-10": members((BUILDABILITY, "F5"), (ADVERSARIAL, "F7"), (ADVERSARIAL, "F9")),
    "SF-11": members((BUILDABILITY, "F15"), (ADVERSARIAL, "F10")),
    "SF-12": members((CONSISTENCY, "F3"), (ADVERSARIAL, "F6")),
    "SF-13": members((BUILDABILITY, "F14")),
    "SF-14": members((CONSISTENCY, "F1"), (CONSISTENCY, "F2")),
    "SF-15": members((FIDELITY, "F2")),
    "SF-16": members((BUILDABILITY, "F11")),
    "SF-17": members((BUILDABILITY, "F12")),
    "SF-18": members((FIDELITY, "F6")),
    "SF-19": members((FIDELITY, "F7")),
    "SF-20": members((CONSISTENCY, "F7")),
    "SF-21": members((BUILDABILITY, "F13")),
    "NH-1": members((FIDELITY, "F8"), (CONSISTENCY, "F4")),
    "NH-2": members((CONSISTENCY, "F8"), (CONSISTENCY, "F9"), (CONSISTENCY, "F10")),
    "NH-3": members((ADVERSARIAL, "F14")),
    "NH-4": members((FIDELITY, "F10")),
    "NH-5": members((FIDELITY, "F11"), (FIDELITY, "F12")),
    "NH-6": members((FIDELITY, "F9")),
    "NH-7": members((ADVERSARIAL, "F12")),
}

# The hand document's dispositions, with `superseded` read as `defer` — the patch has no fourth
# disposition, and "nobody has agreed to fix it" is what both words mean about the artifact.
HAND_DISPOSITIONS = {
    "BK-1": "fix-now", "SF-1": "fix-now", "SF-2": "fix-now", "SF-3": "defer", "SF-4": "fix-now",
    "SF-5": "fix-now", "SF-6": "fix-now", "SF-7": "fix-now", "SF-8": "fix-now", "SF-9": "fix-now",
    "SF-10": "fix-now", "SF-11": "defer", "SF-12": "defer", "SF-13": "defer", "SF-14": "fix-now",
    "SF-15": "fix-now", "SF-16": "fix-now", "SF-17": "fix-now", "SF-18": "fix-now", "SF-19": "fix-now",
    "SF-20": "fix-now", "SF-21": "fix-now", "NH-1": "fix-now", "NH-2": "fix-now", "NH-3": "fix-now",
    "NH-4": "fix-now", "NH-5": "fix-now", "NH-6": "flag-for-human", "NH-7": "flag-for-human",
}

# The defect in the oracle, recorded here rather than worked around in silence. The hand document
# lists `adversarial-glm F5` twice — as the third seat of SF-2 (the effort and accounting cluster)
# and as the second seat of SF-4 (the budget cluster). Its 29 clusters therefore claim 52 member
# slots against 51 findings. The finding's own text is a budget finding, so it is assigned to SF-4
# above, which leaves exactly one cluster at three families. The v3 spec agrees: its Acceptance (a)
# table, the run 1 yield table and the run 1/run 2 comparison all now say one, and the run 1
# reconciliation's erratum says the same. Nothing here is a spec diff any more.
ORACLE_NOTE = (
    "adversarial-glm F5 is listed in both SF-2 and SF-4 of the hand reconciliation. Assigned here to "
    "SF-4, the budget cluster its text argues, which leaves ONE cluster at n_families = 3 — which is "
    "what the v3 spec expects in all three places it states the count."
)

# The multi-finding clusters the MECHANICAL matcher produces on these four reports, before any part
# of the judgment patch is applied. Pinned so a change to `quote_match`, `location_match` or the
# 60-character threshold fails this test instead of being absorbed by the fixture's joins and splits.
# The four marked spurious are split apart again by the fixture's `splits`; they record what the
# matcher does today, not what it ought to do.
# The key each one was joined on is pinned beside the membership: P-1 is the corpus's only
# `location` join, and without it a change that disabled location matching entirely would still pass
# here, because P-1's two findings also overlap on quote.
MECHANICAL_JOINS = {
    "P-1": ("location", members((ADVERSARIAL, "F1"), (FIDELITY, "F11"))),        # spurious; split
    "P-3": ("quote", members((ADVERSARIAL, "F11"), (BUILDABILITY, "F9"))),
    "P-4": ("quote", members((ADVERSARIAL, "F12"), (BUILDABILITY, "F1"), (FIDELITY, "F3"))),  # split
    "P-8": ("quote", members((ADVERSARIAL, "F3"), (FIDELITY, "F1"))),
    "P-9": ("quote", members((ADVERSARIAL, "F4"), (BUILDABILITY, "F4"))),        # spurious; split
    "P-10": ("quote", members((ADVERSARIAL, "F5"), (FIDELITY, "F5"))),
    "P-21": ("quote", members((BUILDABILITY, "F2"), (CONSISTENCY, "F5"))),
    "P-28": ("quote", members((CONSISTENCY, "F10"), (FIDELITY, "F2"))),          # spurious; split
    "P-31": ("quote", members((CONSISTENCY, "F4"), (FIDELITY, "F8"))),
}

MECHANICALLY_SPURIOUS = ("P-1", "P-4", "P-9", "P-28")

# The other half of the pin: pairs the matcher must NOT join. Without these the threshold is pinned
# only from above. Every value from **40 through 75** produces the same nine clusters with the same
# memberships, so the two tests together bracket the threshold to that range rather than pinning 60
# exactly. Both pairs below sit at 39, so a threshold of 39 or lower joins them and fails here; at
# 76 the weakest quote link in `P-4` gives way and it drops `adversarial-glm` F12, and at 81 `P-9`
# (80) breaks too — so either end of the bracket fails the positive pin above.
#
# `P-1`'s two findings overlap by 73 and that length is **not** in the sweep, because `P-1` is the
# corpus's one `location` join: the quote threshold never decides it, and a sweep of this threshold
# alone cannot see it. That is why the bracket's upper end is 75 rather than 73.
MUST_NOT_JOIN = (
    ((ADVERSARIAL, "F4"), (FIDELITY, "F3")),
    ((ADVERSARIAL, "F4"), (BUILDABILITY, "F1")),
)


def _require_fixtures():
    """Missing fixtures are a failure, not a skip.

    A skip makes the whole acceptance control report OK while asserting nothing, which is the same
    failure mode the matcher pin exists to close: a green run that measured nothing.
    """
    for path, what in (
        (RUN_DIR, "the run 1 fixture directory (override with PANEL_REVIEW_REPLAY_DIR)"),
        (os.path.join(RUN_DIR, "judgment.json"), "the judgment patch fixture"),
        (os.path.join(RUN_DIR, "manifest.json"), "the run manifest"),
        (ARTIFACT, "the pinned artifact (override with PANEL_REVIEW_REPLAY_ARTIFACT)"),
    ):
        if not os.path.exists(path):
            raise AssertionError("acceptance (a) cannot run: {0} is not at {1}".format(what, path))


def _finding_set(provisional_cluster):
    """Every (reviewer_id, finding_id) in a PROVISIONAL cluster, whose members are Finding objects."""
    return frozenset((f.reviewer_id, f.raw.get("id")) for f in provisional_cluster["members"])


def _member_set(cluster):
    """Every (reviewer_id, finding_id) the cluster stands on, collapsed duplicates included."""
    pairs = set()
    for member in cluster["members"]:
        pairs.add((member["reviewer_id"], member["finding_id"]))
        for finding_id in member.get("collapsed_finding_ids") or []:
            pairs.add((member["reviewer_id"], finding_id))
    return frozenset(pairs)


class ReplayTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _require_fixtures()
        patch_path = os.path.join(RUN_DIR, "judgment.json")
        with open(patch_path, "r", encoding="utf-8") as handle:
            patch = json.load(handle)
        with open(ARTIFACT, "r", encoding="utf-8") as handle:
            artifact_text = handle.read()
        document, errors, context = core.reconcile(RUN_DIR, patch, artifact_text)
        cls.errors = errors
        cls.document = document
        cls.context = context
        cls.provisional = context["provisional"]
        if errors:
            return
        cls.clusters = document["clusters"]
        cls.by_members = {_member_set(c): c for c in cls.clusters}

    def test_merge_produced_a_document(self):
        self.assertEqual(self.errors, [], "the judgment fixture did not merge cleanly")

    def test_no_anchor_was_dropped_against_the_pinned_artifact(self):
        """Every quote and old_text in the four reports is real text in v1-spec.md.

        The one quote the validator truncated — `adversarial-glm` F6, cut at the 300-character cap —
        is why this is an assertion and not a comment: NFKC rewrites the U+2026 marker to "...", so
        a check that looks for the marker after normalization drops a perfectly real quote and, with
        it, a finding the patch still names.
        """
        self.assertEqual(self.document["anchor_drops"], [])
        self.assertEqual(self.context["anchor_drops"], [])

    def test_the_mechanical_matcher_joins_exactly_these(self):
        """The provisional partition, before the patch. This is the matcher's own acceptance test.

        Pinned: the nine multi-finding clusters, the key each was joined on, `match_key: none` on
        every cluster of one, and the id sequence. `test_the_matcher_does_not_join_these` pins the
        other side. Together they fail on a raised threshold, on a lowered one, and on a disabled
        `location_match` — none of which a product-only test would catch, because the fixture's
        joins and splits can rewrite any partition into any other.

        Four of these nine joins are known-spurious — P-1, P-4, P-9 and P-28 — and the fixture's
        `splits` pull them apart again. They record what the matcher does today, not what it ought
        to do.
        """
        provisional = self.provisional
        joined = {c["provisional_id"]: (c["match_key"], _finding_set(c))
                  for c in provisional if len(c["members"]) > 1}
        self.assertEqual(joined, MECHANICAL_JOINS,
                         "the mechanical matcher no longer joins exactly the pinned set on the pinned keys")
        singles = [c for c in provisional if len(c["members"]) == 1]
        self.assertEqual(len(singles), 32, "32 findings joined nothing; 19 findings are in the 9 joins")
        for cluster in singles:
            self.assertEqual(cluster["match_key"], "none", cluster["provisional_id"])
        self.assertEqual(len(provisional), 41, "41 provisional clusters from 51 findings")
        for number, cluster in enumerate(provisional, start=1):
            self.assertEqual(cluster["provisional_id"], "P-{0}".format(number),
                             "provisional ids are minted in order and never renumbered")

    def test_the_matcher_does_not_join_these(self):
        """The negative half of the pin: near misses that must stay apart at the 60-char threshold.

        Both pairs share 39 characters of longest common substring, so a threshold of 39 or lower
        joins them and lands them in one provisional cluster. Asserted on the partition, not on
        `quote_match` alone, so a change anywhere in the clustering path is caught.
        """
        where = {}
        for cluster in self.provisional:
            for pair in _finding_set(cluster):
                where[pair] = cluster["provisional_id"]
        for left, right in MUST_NOT_JOIN:
            self.assertIn(left, where)
            self.assertIn(right, where)
            self.assertNotEqual(
                where[left], where[right],
                "{0} and {1} share 39 characters and must not join at a 60-character threshold".format(left, right))

    def test_the_patch_splits_the_four_spurious_joins(self):
        """The fixture's `splits` name exactly the four joins the matcher got wrong, and no others."""
        with open(os.path.join(RUN_DIR, "judgment.json"), "r", encoding="utf-8") as handle:
            patch = json.load(handle)
        split_from = sorted(entry["from"] for entry in patch.get("splits") or [])
        self.assertEqual(split_from, sorted(MECHANICALLY_SPURIOUS))

    def test_all_four_seats_reported(self):
        self.assertEqual(self.document["missing_seats"], [])
        self.assertEqual(len(self.document["seats_reporting"]), 4)
        self.assertEqual(sorted(self.document["families_reporting"]), ["claude", "glm", "kimi", "openai"])

    def test_verdict_is_fix_then_ship(self):
        self.assertEqual(self.document["verdict"], "fix-then-ship")

    def test_one_blocker_and_what_it_is(self):
        blockers = [c for c in self.clusters if c["severity"] == "blocker"]
        self.assertEqual(len(blockers), 1, "expected exactly one cluster at blocker severity")
        blocker = blockers[0]
        self.assertEqual(blocker["tier"], "singleton")
        self.assertEqual(blocker["singleton_label"], "blind-spot-catch")
        self.assertTrue((blocker.get("singleton_reason") or "").strip())
        self.assertEqual(_member_set(blocker), members((BUILDABILITY, "F7")))
        self.assertEqual(blocker["disposition"], "fix-now")

    def test_thirteen_cross_family_clusters(self):
        consensus = [c for c in self.clusters if c["tier"] == "consensus"]
        self.assertEqual(len(consensus), 13, "the erratum re-derived thirteen cross-family clusters, NH-1 among them")
        nice = [c for c in consensus if c["severity"] == "nice-to-have"]
        self.assertEqual(len(nice), 1, "one of the thirteen is the nice-to-have the hand document filed as NH-1")
        three_family = [c for c in consensus if c["n_families"] == 3]
        # One, in the fixtures and in the spec alike. See ORACLE_NOTE for why the oracle read two.
        self.assertEqual(len(three_family), 1, ORACLE_NOTE)
        self.assertEqual(_member_set(three_family[0]), HAND_MEMBERS["SF-1"])

    def test_nine_labelled_single_seat_should_fixes(self):
        singles = [c for c in self.clusters if c["severity"] == "should-fix" and c["n_families"] < 2]
        self.assertEqual(len(singles), 9, "the erratum makes SF-13 the ninth: the pre-panel checker is not a seat")
        for cluster in singles:
            self.assertEqual(cluster["tier"], "singleton", "{0} is single-family but not tiered singleton".format(cluster["id"]))
            self.assertIn(cluster["singleton_label"], core.SINGLETON_LABELS,
                          "{0} is a single-seat cluster with no label; unlabelled singletons are not an allowed output".format(cluster["id"]))
            self.assertTrue((cluster.get("singleton_reason") or "").strip(),
                            "{0} carries a label with no reason".format(cluster["id"]))

    def test_no_cluster_here_is_a_same_family_agreement(self):
        """**This is why the `same-family` tier changed nothing on this corpus, and it is asserted
        rather than assumed.**

        `same-family` and `corroborated-same-family` both mean "two or more seats, all of them one
        family", and both now owe a label. No cluster of this frozen run is one — every multi-seat
        cluster here crosses families — so the fixture's patch needs no new label and the replay's
        dispositions are unchanged. A future frozen run that does carry one would silently need a
        `singleton_labels` entry it does not have, and would fail somewhere far less legible than
        here; this is the line that names it.
        """
        for cluster in self.clusters:
            if cluster["n_reviewers"] >= 2:
                self.assertGreaterEqual(
                    cluster["n_families"], 2,
                    "{0} is a same-family agreement of {1} seats; this corpus had none, so its "
                    "judgment fixture owes it a label it does not carry".format(
                        cluster["id"], cluster["n_reviewers"]))
            self.assertNotIn(cluster["tier"], ("same-family", "corroborated-same-family"),
                             cluster["id"])

    def test_every_labellable_cluster_is_labelled(self):
        for cluster in self.clusters:
            if cluster["tier"] in core.LABELLED_TIERS:
                self.assertIn(cluster["singleton_label"], core.SINGLETON_LABELS, cluster["id"])
            else:
                self.assertIsNone(cluster["singleton_label"], cluster["id"])

    def test_member_sets_match_the_hand_reconciliation(self):
        produced = {_member_set(c): c["id"] for c in self.clusters}
        self.assertEqual(len(produced), len(self.clusters), "two clusters share a member set")
        missing = []
        for hand_id, expected in sorted(HAND_MEMBERS.items()):
            if expected not in produced:
                missing.append((hand_id, sorted(expected)))
        extra = [sorted(key) for key in produced if key not in set(HAND_MEMBERS.values())]
        self.assertEqual(missing, [], "clusters the hand document has and the replay did not produce")
        self.assertEqual(extra, [], "clusters the replay produced that the hand document does not have")
        self.assertEqual(len(self.clusters), len(HAND_MEMBERS))

    def test_no_finding_lands_in_two_clusters(self):
        seen = set()
        for cluster in self.clusters:
            for pair in _member_set(cluster):
                self.assertNotIn(pair, seen, "{0} {1} is in two clusters".format(*pair))
                seen.add(pair)
        self.assertEqual(len(seen), 51, "the four reports carry 51 findings between them")

    def test_contradictions_are_empty(self):
        self.assertEqual(self.document["disagreements"]["contradictions"], [])
        for cluster in self.clusters:
            self.assertEqual(cluster["contradicted_by"], [], cluster["id"])

    def test_three_severity_spreads_all_with_buildability_high(self):
        spreads = self.document["disagreements"]["severity_spreads"]
        self.assertEqual(len(spreads), 3, "the erratum: three two-step spreads, not four")
        for entry in spreads:
            self.assertEqual(entry["steps"], 2)
            self.assertEqual(entry["high_side"], [BUILDABILITY],
                             "{0} does not have the buildability seat alone on the high side".format(entry["cluster"]))
        expected = {HAND_MEMBERS["SF-8"], HAND_MEMBERS["SF-9"], HAND_MEMBERS["SF-11"]}
        got = {_member_set(self._cluster(entry["cluster"])) for entry in spreads}
        self.assertEqual(got, expected, "the two-step spreads are the hand document's SF-8, SF-9 and SF-11")

    def test_the_altitude_split_is_reported_as_a_split(self):
        splits = self.document["disagreements"]["altitude_splits"]
        self.assertEqual(len(splits), 1)
        entry = splits[0]
        self.assertEqual(entry["reviewer_id"], BUILDABILITY)
        self.assertEqual(len(entry["clusters"]), 6, "all six clusters with the buildability seat on the high side")
        self.assertNotIn(entry["clusters"], [c["cluster"] for c in self.document["disagreements"]["contradictions"]])
        self.assertTrue(entry["note"].strip())

    def test_every_cluster_carries_a_match_key(self):
        for cluster in self.clusters:
            self.assertIn(cluster["match_key"], core.MATCH_KEYS, cluster["id"])
            if cluster["match_key"] == "claim":
                self.assertTrue(cluster["judgment"], "{0} is claim-joined but not flagged judgment".format(cluster["id"]))
            else:
                self.assertFalse(cluster["judgment"], "{0} is flagged judgment without a claim join".format(cluster["id"]))
            if cluster["match_key"] == "none":
                self.assertEqual(cluster["n_reviewers"], 1, cluster["id"])

    def test_every_claim_join_traces_to_the_fixture(self):
        with open(os.path.join(RUN_DIR, "judgment.json"), "r", encoding="utf-8") as handle:
            patch = json.load(handle)
        joined = {pid for entry in patch.get("claim_joins") or [] for pid in entry["merge"]}
        for cluster in self.clusters:
            if cluster["match_key"] != "claim":
                continue
            parts = set(cluster["provisional_id"].split("+"))
            self.assertTrue(parts & joined,
                            "{0} is claim-joined but no claim_joins entry names it".format(cluster["provisional_id"]))

    def test_counts_are_internally_consistent(self):
        counts = self.document["counts"]
        self.assertEqual(counts["clusters"], len(self.clusters))
        self.assertEqual(sum(counts["by_severity"].values()), len(self.clusters))
        self.assertEqual(sum(counts["by_tier"].values()), len(self.clusters))
        self.assertEqual(sum(counts["by_disposition"].values()), len(self.clusters))

    def test_z_reportable_diffs_are_printed_not_failed(self):
        """Dispositions may legitimately differ from the hand document; that is a diff, not a failure."""
        lines = []
        for hand_id, expected in sorted(HAND_DISPOSITIONS.items(), key=lambda pair: _hand_sort(pair[0])):
            cluster = self.by_members.get(HAND_MEMBERS[hand_id])
            if cluster is None:
                continue
            if cluster["disposition"] != expected:
                lines.append("  {0} (now {1}): hand document {2}, replay {3}".format(
                    hand_id, cluster["id"], expected, cluster["disposition"]))
        print("\n--- replay diffs against the hand reconciliation ---")
        print("ORACLE NOTE: " + ORACLE_NOTE)
        if lines:
            print("Disposition differences ({0}), reported and not failed:".format(len(lines)))
            print("\n".join(lines))
        else:
            print("Disposition differences: none.")
        renames = [(hand_id, self.by_members[HAND_MEMBERS[hand_id]]["id"])
                   for hand_id in sorted(HAND_MEMBERS, key=_hand_sort)
                   if HAND_MEMBERS[hand_id] in self.by_members
                   and self.by_members[HAND_MEMBERS[hand_id]]["id"] != hand_id]
        print("Cluster ids renumbered ({0} of {1}): {2}".format(
            len(renames), len(HAND_MEMBERS),
            ", ".join("{0}->{1}".format(old, new) for old, new in renames) or "none"))
        print("---------------------------------------------------")

    def _cluster(self, cluster_id):
        for cluster in self.clusters:
            if cluster["id"] == cluster_id:
                return cluster
        raise AssertionError("no cluster {0}".format(cluster_id))


class ArtifactIsMandatoryTest(unittest.TestCase):
    """`reconcile.py` refuses to reconcile without the pinned artifact.

    The anchor check is not an opt-in flag: a run reconciled without it has not verified that any
    quote is real text, and saying nothing about that is the failure mode the check exists to close.
    """

    @classmethod
    def setUpClass(cls):
        _require_fixtures()

    def _run_dir_without_an_artifact_path(self, root):
        """A copy of the run whose manifest records no artifact, so nothing can resolve one."""
        destination = os.path.join(root, "run")
        shutil.copytree(RUN_DIR, destination)
        manifest_path = os.path.join(destination, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest.pop("artifact", None)
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2)
        return destination

    def test_it_is_a_usage_error_when_no_artifact_resolves(self):
        import reconcile  # noqa: PLC0415 — the CLI, imported only by this test

        with tempfile.TemporaryDirectory() as root:
            run_dir = self._run_dir_without_an_artifact_path(root)
            before = sorted(os.listdir(run_dir))
            stderr = io.StringIO()
            saved, sys.stderr = sys.stderr, stderr
            try:
                code = reconcile.main(["--run-dir", run_dir, "--judgment", os.path.join(run_dir, "judgment.json")])
            finally:
                sys.stderr = saved
            self.assertEqual(code, 1, "a run with no resolvable artifact is a usage error")
            self.assertIn("--artifact", stderr.getvalue())
            self.assertEqual(sorted(os.listdir(run_dir)), before, "nothing may be written without the check")

    def test_a_failed_seat_reconciles_and_exits_three(self):
        """A run whose seat failed still reconciles. `<id>.failed.json` is not a fifth seat.

        `run_panel.py` writes `<reviewer_id>.failed.json` beside the reports for every seat that
        failed, and `dispatch.py` writes `<reviewer_id>.invalid.txt`. Both are records of the
        failure. Reading either as a report from a reviewer the manifest never resolved would abort
        the run at exit 1, where the spec requires the under-seated run to reconcile and exit 3.
        """
        import reconcile  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "run")
            shutil.copytree(RUN_DIR, run_dir)
            os.remove(os.path.join(run_dir, "reconciliation.md"))
            shutil.move(os.path.join(run_dir, "consistency-kimi.json"),
                        os.path.join(run_dir, "consistency-kimi.failed.json"))
            manifest_path = os.path.join(run_dir, "manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            for seat in manifest["seats"]:
                if seat["reviewer_id"] == "consistency-kimi":
                    seat["status"] = "failed"
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2)

            # The patch was written against all four seats, so it names findings that are now gone.
            # The point of the test is the loader, so ask for the judgment request instead, which
            # needs no patch and exercises the same scan.
            os.remove(os.path.join(run_dir, "judgment.json"))
            stderr = io.StringIO()
            saved, sys.stderr = sys.stderr, stderr
            try:
                code = reconcile.main(["--run-dir", run_dir, "--artifact", ARTIFACT])
            finally:
                sys.stderr = saved
            self.assertEqual(code, 3, stderr.getvalue())
            with open(os.path.join(run_dir, "judgment-request.json"), "r", encoding="utf-8") as handle:
                request = json.load(handle)
            self.assertEqual([s["reviewer_id"] for s in request["missing_seats"]], ["consistency-kimi"])
            self.assertEqual(request["missing_seats"][0]["stage"], "dispatch")
            self.assertEqual(sorted(request["seats_expected"]), sorted(
                [s["reviewer_id"] for s in manifest["seats"]]))
            self.assertIn("anchor_drops", request)

    def test_an_under_seated_run_writes_both_files_and_exits_three(self):
        """The failure record beside the reports does not stop the reconciliation."""
        import reconcile  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "run")
            shutil.copytree(RUN_DIR, run_dir)
            os.remove(os.path.join(run_dir, "reconciliation.md"))
            manifest_path = os.path.join(run_dir, "manifest.json")
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            manifest["seats"].append(
                {"reviewer_id": "security-xai", "lens": "security", "family": "xai", "status": "failed"})
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2)
            with open(os.path.join(run_dir, "security-xai.failed.json"), "w", encoding="utf-8") as handle:
                json.dump({"reviewer_id": "security-xai", "error": "the provider returned 500"}, handle)

            stderr = io.StringIO()
            saved, sys.stderr = sys.stderr, stderr
            try:
                code = reconcile.main([
                    "--run-dir", run_dir,
                    "--judgment", os.path.join(run_dir, "judgment.json"),
                    "--artifact", ARTIFACT,
                ])
            finally:
                sys.stderr = saved
            self.assertEqual(code, 3, stderr.getvalue())
            with open(os.path.join(run_dir, "reconciliation.json"), "r", encoding="utf-8") as handle:
                document = json.load(handle)
            self.assertTrue(os.path.isfile(os.path.join(run_dir, "reconciliation.md")))
            self.assertEqual([s["reviewer_id"] for s in document["missing_seats"]], ["security-xai"])
            self.assertNotIn("security-xai", document["seats_reporting"])
            self.assertIn("security-xai", document["seats_expected"])

    def test_render_only_survives_a_run_dir_the_loader_would_refuse(self):
        """`--render-only` re-renders an already validated document and must not die on the run."""
        import reconcile  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as root:
            run_dir = os.path.join(root, "run")
            shutil.copytree(RUN_DIR, run_dir)
            os.remove(os.path.join(run_dir, "reconciliation.md"))
            self.assertEqual(0, reconcile.main([
                "--run-dir", run_dir,
                "--judgment", os.path.join(run_dir, "judgment.json"),
                "--artifact", ARTIFACT,
            ]))
            # Now make the run directory something the reconcile path would refuse outright.
            os.remove(os.path.join(run_dir, "manifest.json"))
            with open(os.path.join(run_dir, "ghost-seat.json"), "w", encoding="utf-8") as handle:
                handle.write("{ not json at all")
            os.remove(os.path.join(run_dir, "reconciliation.md"))
            self.assertEqual(0, reconcile.main(["--run-dir", run_dir, "--render-only"]),
                             "--render-only must not die on a run directory it does not need")
            self.assertTrue(os.path.isfile(os.path.join(run_dir, "reconciliation.md")))

    def test_a_bad_artifact_path_is_also_refused(self):
        import reconcile  # noqa: PLC0415

        stderr = io.StringIO()
        saved, sys.stderr = sys.stderr, stderr
        try:
            code = reconcile.main([
                "--run-dir", RUN_DIR,
                "--judgment", os.path.join(RUN_DIR, "judgment.json"),
                "--artifact", os.path.join(RUN_DIR, "there-is-no-such-file.md"),
            ])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 1)
        self.assertIn("no such artifact", stderr.getvalue())


def _hand_sort(cluster_id):
    prefix, _, number = cluster_id.partition("-")
    return ({"BK": 0, "SF": 1, "NH": 2}.get(prefix, 9), int(number))


if __name__ == "__main__":
    unittest.main(verbosity=2)

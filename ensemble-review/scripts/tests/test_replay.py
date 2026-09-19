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

    python3 -m unittest discover -s scripts/tests     # from the skill root
    python3 scripts/tests/test_replay.py
"""

import json
import os
import sys
import unittest

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))

from lib import reconcile_core as core  # noqa: E402

DEFAULT_RUN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(SKILL_DIR)),
    ".agents", "subprojects", "ensemble-review", "reviews", "v1-spec", "2026-09-18-1",
)
RUN_DIR = os.environ.get("ENSEMBLE_REVIEW_REPLAY_DIR", DEFAULT_RUN_DIR)

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

# The one place the oracle cannot be satisfied, recorded here rather than worked around in silence.
# The hand document lists `adversarial-glm F5` twice — as the third seat of SF-2 (the effort and
# accounting cluster) and as the second seat of SF-4 (the budget cluster). Its 29 clusters therefore
# claim 52 member slots against 51 findings. The finding's own text is a budget finding, so it is
# assigned to SF-4 above, which keeps the erratum's three verified counts — thirteen consensus
# clusters, nine single-seat should-fixes, three two-step spreads — and costs the one count the
# erratum did not verify: the spec's Acceptance (a) expects TWO consensus clusters at three families
# and only the config-shape cluster can reach three. No claim join or split fixes this; a cluster
# cannot gain a member that does not exist.
ORACLE_DEFECT = (
    "adversarial-glm F5 is listed in both SF-2 and SF-4 of the hand reconciliation. Assigned here to "
    "SF-4, the budget cluster its text argues, which leaves ONE cluster at n_families = 3 where the "
    "v3 spec's Acceptance (a) table expects two."
)


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
        if not os.path.isdir(RUN_DIR):
            raise unittest.SkipTest(
                "replay fixtures not found at {0}; set ENSEMBLE_REVIEW_REPLAY_DIR".format(RUN_DIR))
        patch_path = os.path.join(RUN_DIR, "judgment.json")
        if not os.path.isfile(patch_path):
            raise unittest.SkipTest("no judgment.json fixture in {0}; the replay does not run without it".format(RUN_DIR))
        with open(patch_path, "r", encoding="utf-8") as handle:
            patch = json.load(handle)
        document, errors, context = core.reconcile(RUN_DIR, patch)
        cls.errors = errors
        cls.document = document
        cls.context = context
        if errors:
            return
        cls.clusters = document["clusters"]
        cls.by_members = {_member_set(c): c for c in cls.clusters}

    def test_merge_produced_a_document(self):
        self.assertEqual(self.errors, [], "the judgment fixture did not merge cleanly")

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
        # The spec's table expects two here. See ORACLE_DEFECT: the fixtures hold one.
        self.assertEqual(len(three_family), 1, ORACLE_DEFECT)
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
        """Dispositions may legitimately differ; the spec's three-family count cannot be reached."""
        lines = []
        for hand_id, expected in sorted(HAND_DISPOSITIONS.items(), key=lambda pair: _hand_sort(pair[0])):
            cluster = self.by_members.get(HAND_MEMBERS[hand_id])
            if cluster is None:
                continue
            if cluster["disposition"] != expected:
                lines.append("  {0} (now {1}): hand document {2}, replay {3}".format(
                    hand_id, cluster["id"], expected, cluster["disposition"]))
        print("\n--- replay diffs against the hand reconciliation ---")
        print("SPEC DIFF: " + ORACLE_DEFECT)
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


def _hand_sort(cluster_id):
    prefix, _, number = cluster_id.partition("-")
    return ({"BK": 0, "SF": 1, "NH": 2}.get(prefix, 9), int(number))


if __name__ == "__main__":
    unittest.main(verbosity=2)

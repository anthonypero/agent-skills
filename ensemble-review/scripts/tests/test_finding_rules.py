#!/usr/bin/env python3
"""The two per-finding rules the validator enforces, and what the reconciler does with the tag.

No network and no paid call. Three claims, all of them about the one question `change_kind` and
`citation` exist to answer — is this fix writable, and is this finding an argument or an opinion:

- **Both citing lenses cite.** `fidelity` and `source-credibility` require a `citation` on every
  finding. `run_panel.py` refuses to seat either with no references; this is the other half of that
  rule, one finding at a time.
- **`judgment-call` carries `fork`, at ingest.** A design fork is what `judgment-call` means, so the
  tag says so out loud; `gap` on a judgment call is rejected outright, because a determinate fix is
  a `literal-edit` however large it is. The rule runs only where a reviewer can still be asked for a
  repair — reading a stored report never applies it, or the frozen replay fixture's 44 untagged
  judgment calls would stop replaying.
- **A gap-tagged cluster is not a question for a human.** `reconcile_core` forces an all-
  `judgment-call` cluster to `flag-for-human` on a patch from `synthesis`; a cluster whose judgment
  calls are every one of them tagged `gap` is a determinate fix and goes on the fix list instead.
  Untagged is unchanged and still goes to the human, which is what keeps the fixture's dispositions
  identical.

    python3 scripts/tests/test_finding_rules.py
"""

import copy
import io
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

import render_harness_report  # noqa: E402
from lib import reconcile_core as core  # noqa: E402
from lib import report as report_lib  # noqa: E402

REPO = os.path.dirname(os.path.dirname(SKILL_DIR))
FIXTURE_RUN_DIR = os.environ.get("ENSEMBLE_REVIEW_REPLAY_DIR", os.path.join(
    REPO, ".agents", "subprojects", "ensemble-review", "reviews", "v1-spec", "2026-09-18-1"))

CITATION = {
    "reference": "pm/prd.md",
    "location": "§4",
    "quote": "humans see the re-presented result before accepting",
}


def finding(**overrides):
    base = {
        "id": "F1",
        "location": "§1",
        "quote": "The config shape has no family dimension",
        "claim": "The config shape has no family dimension.",
        "citation": None,
        "severity": "should-fix",
        "reasoning": "A builder cannot resolve a seat constraint without one.",
        "suggested_change": "Add the family axis under the tier map.",
        "change_kind": "literal-edit",
        "literal_edit": {"old_text": "The config shape has no family dimension",
                         "new_text": "The config shape carries a family dimension"},
        "confidence": "high",
        "externally_verified": False,
        "tags": ["configuration"],
    }
    base.update(overrides)
    return base


def report(lens, findings):
    return {
        "schema_version": "1",
        "reviewer_id": "{0}-openai".format(lens),
        "lens": lens,
        "family": "openai",
        "model": "openai/test",
        "leg": "openrouter",
        "artifact": "design/v3-spec.md",
        "verdict": "fix-then-ship",
        "summary": "One defect found.",
        "findings": findings,
        "method_notes": "scripted",
    }


def judgment_call(**overrides):
    overrides.setdefault("change_kind", "judgment-call")
    overrides.setdefault("literal_edit", None)
    return finding(**overrides)


# --- the citation rule ---------------------------------------------------------------------------

class CitingLensTest(unittest.TestCase):
    """`fidelity` and `source-credibility` are one rule, not one rule and a persona's good manners."""

    def errors(self, lens, citation):
        data = report(lens, [finding(citation=citation)])
        return report_lib.validate_report(data, lens=lens)

    def test_the_two_citing_lenses_are_the_two_the_composition_check_refuses(self):
        self.assertEqual(sorted(report_lib.CITING_LENSES), sorted(("fidelity", "source-credibility")))

    def test_a_fidelity_finding_with_no_citation_is_rejected(self):
        errors = self.errors("fidelity", None)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("the fidelity lens requires a `citation`", errors[0])

    def test_a_source_credibility_finding_with_no_citation_is_rejected_identically(self):
        errors = self.errors("source-credibility", None)
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("the source-credibility lens requires a `citation`", errors[0])

    def test_both_citing_lenses_pass_once_the_citation_is_there(self):
        for lens in report_lib.CITING_LENSES:
            with self.subTest(lens=lens):
                self.assertEqual(self.errors(lens, dict(CITATION)), [])

    def test_a_non_citing_lens_may_leave_it_null(self):
        for lens in ("adversarial", "completeness", "security"):
            with self.subTest(lens=lens):
                self.assertEqual(self.errors(lens, None), [])


# --- the fork tag --------------------------------------------------------------------------------

class ForkTagTest(unittest.TestCase):
    """At ingest a `judgment-call` carries `fork`; off ingest nothing here applies at all."""

    def errors(self, item, ingest=True):
        return report_lib.validate_report(report("adversarial", [item]), lens="adversarial", ingest=ingest)

    def test_an_untagged_judgment_call_is_rejected(self):
        errors = self.errors(judgment_call())
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("carries the `fork` tag", errors[0])

    def test_a_judgment_call_tagged_gap_is_rejected_and_told_what_to_file_instead(self):
        errors = self.errors(judgment_call(tags=["configuration", "gap"]))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("must not carry the `gap` tag", errors[0])
        self.assertIn("`literal-edit`", errors[0], "the message names the repair, not just the refusal")

    def test_carrying_both_tags_is_rejected_as_the_gap_case(self):
        errors = self.errors(judgment_call(tags=["fork", "gap"]))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("must not carry the `gap` tag", errors[0])

    def test_a_judgment_call_tagged_fork_passes(self):
        self.assertEqual(self.errors(judgment_call(tags=["configuration", "fork"])), [])

    def test_a_literal_edit_is_held_to_no_tag_rule(self):
        self.assertEqual(self.errors(finding()), [])
        self.assertEqual(self.errors(finding(tags=["gap"])), [],
                         "`gap` is a live tag; it is `judgment-call` that may not carry it")

    def test_a_malformed_tags_array_is_reported_once_as_the_type_error(self):
        errors = self.errors(judgment_call(tags="fork"))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("`tags` must be an array of strings", errors[0])

    def test_reading_a_stored_report_back_applies_none_of_it(self):
        for item in (judgment_call(), judgment_call(tags=["gap"])):
            with self.subTest(tags=item["tags"]):
                self.assertEqual(self.errors(item, ingest=False), [])

    def test_the_frozen_fixture_still_validates_the_way_every_reader_of_it_does(self):
        """44 untagged judgment calls across four reports, and the replay path must not reject one."""
        if not os.path.isdir(FIXTURE_RUN_DIR):
            self.skipTest("the frozen run 1 fixture is not at {0}".format(FIXTURE_RUN_DIR))
        untagged = 0
        for name in sorted(os.listdir(FIXTURE_RUN_DIR)):
            if not name.endswith(".json") or name in ("manifest.json", "judgment.json"):
                continue
            with open(os.path.join(FIXTURE_RUN_DIR, name), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            with self.subTest(report=name):
                self.assertEqual(report_lib.validate_report(data, lens=data.get("lens")), [])
            untagged += sum(1 for f in data["findings"]
                            if f.get("change_kind") == "judgment-call"
                            and not ({"fork", "gap"} & set(f.get("tags") or [])))
        self.assertEqual(untagged, 44, "the fixture's untagged judgment calls are what this test is about")


# --- the other ingest point -----------------------------------------------------------------------

class HarnessLegIngestTest(unittest.TestCase):
    """`render_harness_report.py` is the harness leg's validator, and both legs answer to one rule.

    No test covered this script before. It is the second of the two places a report first enters the
    system, and a subagent that files an untagged judgment call has to hear the same refusal a model
    on the OpenRouter leg hears — otherwise the rule holds only for the seats that cost money.
    """

    def render(self, item, reviewer_id="adversarial-claude"):
        root = tempfile.mkdtemp(prefix="ensemble-review-harness-")
        self.addCleanup(shutil.rmtree, root, True)
        path = os.path.join(root, reviewer_id + ".json")
        report_lib.write_json(path, report("adversarial", [item]))
        err, saved = io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            code = render_harness_report.main([path])
        finally:
            sys.stderr = saved
        return code, err.getvalue(), root, path

    def test_an_untagged_judgment_call_is_refused_and_nothing_is_rendered(self):
        code, err, root, _path = self.render(judgment_call())
        self.assertEqual(code, 3, err)
        self.assertIn("carries the `fork` tag", err)
        self.assertFalse(os.path.isfile(os.path.join(root, "adversarial-claude.md")),
                         "a report that did not validate is not rendered as if it had")

    def test_a_gap_tagged_judgment_call_is_refused_the_same_way(self):
        code, err, _root, _path = self.render(judgment_call(tags=["gap"]))
        self.assertEqual(code, 3, err)
        self.assertIn("must not carry the `gap` tag", err)

    def test_a_fork_tagged_judgment_call_renders(self):
        code, err, root, _path = self.render(judgment_call(tags=["fork"]))
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(root, "adversarial-claude.md")))


# --- the reconciler's reading of the tag ----------------------------------------------------------

class GapClusterTest(unittest.TestCase):
    """A synthesis patch may dispose a `gap` cluster; a fork and an untagged call it may not."""

    def run_dir_with(self, item):
        root = tempfile.mkdtemp(prefix="ensemble-review-tags-")
        self.addCleanup(shutil.rmtree, root, True)
        run_dir = os.path.join(root, "run")
        os.makedirs(run_dir)
        report_lib.write_json(os.path.join(run_dir, "manifest.json"), {
            "run_id": "2026-09-18-1",
            "panel": "spec-review",
            "artifact": "design/v3-spec.md",
            "artifact_revision": None,
            "seats": [{"reviewer_id": "adversarial-openai", "lens": "adversarial", "family": "openai",
                       "status": "ok", "leg": "openrouter"}],
        })
        report_lib.write_json(os.path.join(run_dir, "adversarial-openai.json"),
                              report("adversarial", [copy.deepcopy(item)]))
        return run_dir

    def patch(self, disposition):
        return {
            "schema_version": "1",
            "run_id": "2026-09-18-1",
            "author": "synthesis",
            "generated_at": "2026-09-18T12:00:00-04:00",
            "singleton_labels": [{"cluster": "P-1", "label": "blind-spot-catch",
                                  "reason": "one seat saw it and the others did not read that section"}],
            "dispositions": [{"cluster": "P-1", "disposition": disposition,
                              "disposition_reason": "scripted for this test"}],
            "method_caveat": "One family ran.",
        }

    def dispose(self, item, disposition):
        return core.reconcile(self.run_dir_with(item), self.patch(disposition))

    def test_a_gap_tagged_judgment_call_may_go_on_the_fix_list(self):
        document, errors, _context = self.dispose(judgment_call(tags=["gap"]), "fix-now")
        self.assertEqual(errors, [], "a determinate fix is not a question anybody owes an answer to")
        self.assertEqual(document["clusters"][0]["disposition"], "fix-now")

    def test_a_fork_tagged_judgment_call_is_forced_to_a_human(self):
        _document, errors, _context = self.dispose(judgment_call(tags=["fork"]), "fix-now")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("an unattended judge always flags a judgment call", errors[0])

    def test_an_untagged_judgment_call_is_forced_to_a_human_exactly_as_before(self):
        """The fixture's 44 untagged calls read this way, so its dispositions do not move."""
        _document, errors, _context = self.dispose(judgment_call(), "fix-now")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("an unattended judge always flags a judgment call", errors[0])

    def test_a_mixed_cluster_keeps_the_ruling(self):
        """One finding tagged `gap` does not make the fork collapsed beside it disappear.

        Both findings share a quote, so the matcher collapses them into one cluster's `_all` — which
        is exactly the case the rule has to get right: "every judgment call here is only a gap" is
        false the moment one of them is a fork.
        """
        run_dir = self.run_dir_with(judgment_call())
        report_lib.write_json(os.path.join(run_dir, "adversarial-openai.json"), report("adversarial", [
            judgment_call(id="F1", tags=["gap"]),
            judgment_call(id="F2", tags=["fork"]),
        ]))
        _document, errors, _context = core.reconcile(run_dir, self.patch("fix-now"))
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("an unattended judge always flags a judgment call", errors[0])

    def test_a_gap_tagged_literal_edit_is_untouched_by_any_of_this(self):
        document, errors, _context = self.dispose(finding(tags=["gap"]), "fix-now")
        self.assertEqual(errors, [])
        self.assertEqual(document["clusters"][0]["disposition"], "fix-now")


if __name__ == "__main__":
    unittest.main(verbosity=2)

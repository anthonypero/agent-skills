#!/usr/bin/env python3
"""`missing_seats[]`: why a composed seat is absent, not merely that it is.

**The run already knows.** A seat retired at Resolve for want of references, one dropped for a
context overflow, one whose lease went stale — each carries a `failure_reason` and an `error` the
manifest recorded at the moment it happened. The reconciliation was handing the judgment supplier
"no report file at fidelity-claude.json", which is the one thing it could already see for itself,
and the supplier is the mind that has to decide what a missing lens does to every agreement count
below it.

So `reason` is the seat's own `failure_reason` where it has one, `error` carries the prose beside
it, and `stage` says **`resolve`** for a seat that was never dispatched — distinct from `dispatch`,
which asserts that a call was made and came back empty.

No network and no model: every report here is a file on disk.

    python3 scripts/tests/test_missing_seats.py
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
from lib import reconcile_core as core  # noqa: E402
from lib import schema as schema_lib  # noqa: E402

RUN_ID = "2026-09-19-1"

ARTIFACT = """# A draft under review

## Budget
The budget is stated in one place and contradicted in another.
"""

QUOTE = "The budget is stated in one place and contradicted in another."

RETIRED_ERROR = ("the `fidelity` lens cites on every finding and this run supplied no "
                 "source-of-truth references, so every finding it returned would fail validation. "
                 "On a draft pass the seat is retired rather than the run refused; supply --ref to "
                 "seat it.")


class MissingSeatsCase(unittest.TestCase):
    """Two seats: one reported, one was retired at Resolve before anything was dispatched."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="panel-review-missing-seats-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.run_dir = os.path.join(self.root, "reviews", RUN_ID)
        os.makedirs(self.run_dir)

    def build(self, absent):
        """`absent` is the manifest record for the seat with no report on disk."""
        _write_json(os.path.join(self.run_dir, "manifest.json"), {
            "run_id": RUN_ID,
            "panel": "draft-review",
            "artifact": "draft.md",
            "artifact_revision": None,
            "draft": True,
            "seats": [
                dict({"reviewer_id": "fidelity-claude", "lens": "fidelity", "family": "claude",
                      "leg": "harness"}, **absent),
                {"reviewer_id": "consistency-claude", "lens": "consistency", "family": "claude",
                 "leg": "harness", "status": "ok"},
            ],
        })
        _write_json(os.path.join(self.run_dir, "consistency-claude.json"), {
            "schema_version": "1",
            "reviewer_id": "consistency-claude",
            "lens": "consistency",
            "family": "claude",
            "model": "test/one-model",
            "leg": "harness",
            "artifact": "draft.md",
            "artifact_revision": None,
            "verdict": "fix-then-ship",
            "summary": "One defect found.",
            "findings": [{
                "id": "F1",
                "location": "Budget",
                "quote": QUOTE,
                "claim": "The budget is stated twice and the two do not agree.",
                "citation": None,
                "severity": "should-fix",
                "reasoning": "A builder cannot tell which figure binds.",
                "suggested_change": "State it once.",
                "change_kind": "judgment-call",
                "literal_edit": None,
                "confidence": "high",
                "externally_verified": False,
                "tags": ["budget", "fork"],
            }],
            "method_notes": "scripted",
        })

    def retired(self):
        """The record `run_panel._retired_for_references` writes for a citing seat it will not run."""
        return {"status": "failed", "report": None, "failure_reason": "no-references",
                "error": RETIRED_ERROR}

    def reconcile(self):
        patch = {
            "schema_version": "1",
            "run_id": RUN_ID,
            "author": "host",
            "generated_at": "2026-09-19T09:00:00-04:00",
            "dispositions": [{"cluster": "P-1", "disposition": "fix-now",
                              "disposition_reason": "A determinate fix."}],
            "singleton_labels": [{"cluster": "P-1", "label": "blind-spot-catch",
                                  "reason": "One seat, and the lens that would corroborate it is absent."}],
            "method_caveat": "One seat reported.",
        }
        document, errors, context = core.reconcile(self.run_dir, patch, ARTIFACT)
        self.assertEqual(errors, [])
        return document, context


class RetiredSeatTest(MissingSeatsCase):

    def setUp(self):
        super(RetiredSeatTest, self).setUp()
        self.build(self.retired())
        self.document, self.context = self.reconcile()
        self.absent = self.document["missing_seats"][0]

    def test_the_stage_is_resolve_and_not_dispatch(self):
        """`dispatch` would assert a call that was never made. The seat never left Resolve."""
        self.assertEqual(self.absent["stage"], "resolve")

    def test_the_reason_is_the_runs_own_failure_reason(self):
        self.assertEqual(self.absent["reason"], "no-references")
        self.assertEqual(self.absent["failure_reason"], "no-references")
        self.assertNotIn("no report file at", self.absent["reason"])

    def test_the_error_prose_travels_with_it(self):
        """What the supplier needs is why the lens is absent, which is the sentence the run wrote."""
        self.assertEqual(self.absent["error"], RETIRED_ERROR)
        self.assertIn("--ref", self.absent["error"])

    def test_the_lens_and_the_family_are_still_named(self):
        self.assertEqual(self.absent["reviewer_id"], "fidelity-claude")
        self.assertEqual(self.absent["lens"], "fidelity")
        self.assertEqual(self.absent["family"], "claude")

    def test_the_worksheet_carries_the_same_record(self):
        """The supplier reads the worksheet before it reads anything else."""
        request = core.judgment_request(self.context)
        self.assertEqual(request["missing_seats"], self.document["missing_seats"])

    def test_the_rendered_line_names_the_reason_and_the_prose_behind_it(self):
        rendered = reconcile.render_markdown(self.document)
        line = [row for row in rendered.splitlines() if row.startswith("- `fidelity-claude`")]
        self.assertEqual(len(line), 1, rendered)
        self.assertIn("resolve: no-references", line[0])
        self.assertIn("supply --ref to seat it", line[0])

    def test_the_document_still_validates(self):
        path = os.path.join(SKILL_DIR, "schemas", "reconciliation.schema.json")
        self.assertEqual(schema_lib.validate(self.document, schema_lib.load(path)), [])


class OrdinaryMissingSeatTest(MissingSeatsCase):
    """A seat the manifest says nothing about keeps the record it always had."""

    def test_a_pending_seat_still_reads_dispatch_and_names_the_file(self):
        self.build({"status": "pending"})
        document, _context = self.reconcile()
        absent = document["missing_seats"][0]
        self.assertEqual(absent["stage"], "dispatch")
        self.assertEqual(absent["reason"], "no report file at fidelity-claude.json")
        self.assertIsNone(absent["failure_reason"])
        self.assertIsNone(absent["error"])

    def test_the_rendered_line_is_unchanged_for_one(self):
        self.build({"status": "pending"})
        document, _context = self.reconcile()
        rendered = reconcile.render_markdown(document)
        self.assertIn("- `fidelity-claude` (claude) — dispatch: no report file at "
                      "fidelity-claude.json", rendered)

    def test_a_dispatched_failure_carries_its_reason_at_the_dispatch_stage(self):
        """The stage override is about `no-references` and nothing else: a seat that really was
        dispatched and failed is still a `dispatch`-stage absence, now with its reason attached."""
        self.build({"status": "failed", "failure_reason": "context-overflow",
                    "error": "the composed prompt exceeds this model's context limit"})
        document, _context = self.reconcile()
        absent = document["missing_seats"][0]
        self.assertEqual(absent["stage"], "dispatch")
        self.assertEqual(absent["reason"], "context-overflow")
        self.assertIn("context limit", absent["error"])


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)

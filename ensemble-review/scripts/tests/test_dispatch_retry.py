#!/usr/bin/env python3
"""`dispatch.py`'s three retry paths, the completion cap, and the registry gate.

No network and no paid call: the connector is `fake_backend.py`, loaded by file path the same way a
project's own workspace driver would be, and scripted per call through a plan file.

    python3 scripts/tests/test_dispatch_retry.py
"""

import io
import json
import os
import socket
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import dispatch  # noqa: E402
from backends import load_driver  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402
import harness  # noqa: E402
from backends import AuthFailure  # noqa: E402
from lib import registry as registry_lib  # noqa: E402


class DispatchTestCase(unittest.TestCase):

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.out = self.workspace.path("run")
        os.makedirs(self.out)
        self.addCleanup(self.workspace.close)

    def run_seat(self, model=harness.SLOW_MODEL, family="kimi", extra=None):
        argv = [
            "--persona", "lens-consistency",
            "--family", family,
            "--artifact", self.workspace.artifact,
            "--artifact-name", "docs/artifact.md",
            "--artifact-revision", "deadbeef",
            "--out", self.out,
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
            "--tier", "standard",
        ] + list(extra or [])
        stderr, saved = io.StringIO(), sys.stderr
        sys.stderr = stderr
        try:
            code = dispatch.main(argv)
        finally:
            sys.stderr = saved
        return code, stderr.getvalue()

    def report(self, reviewer_id="consistency-kimi"):
        with open(os.path.join(self.out, reviewer_id + ".json"), "r", encoding="utf-8") as handle:
            return json.load(handle)


class CompletionCapTest(DispatchTestCase):

    def test_the_cap_is_always_explicit_and_defaults_to_32000(self):
        self.workspace.plan({harness.FAST_MODEL: [{"body": harness.valid_report()}]})
        code, _err = self.run_seat(family="xai")
        self.assertEqual(code, 0)
        calls = self.workspace.calls(harness.FAST_MODEL)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["max_tokens"], registry_lib.DEFAULT_MAX_TOKENS)

    def test_max_tokens_overrides_the_default_per_run(self):
        self.workspace.plan({harness.FAST_MODEL: [{"body": harness.valid_report()}]})
        code, _err = self.run_seat(family="xai", extra=["--max-tokens", "8000"])
        self.assertEqual(code, 0)
        self.assertEqual(self.workspace.calls(harness.FAST_MODEL)[0]["max_tokens"], 8000)

    def test_a_per_model_floor_raises_the_run_cap_and_the_cap_sent_is_recorded(self):
        """`min_max_tokens` is the companion knob to the length retry: run 2's consistency seat spent
        a whole 32000-token cap on reasoning, and a floor is what stops that seat starting there."""
        self.workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}]})
        code, err = self.run_seat(extra=["--max-tokens", "32000"])
        self.assertEqual(code, 0)
        self.assertEqual(self.workspace.calls(harness.SLOW_MODEL)[0]["max_tokens"], 64000)
        self.assertIn("min_max_tokens", err)
        meta = self.report()["_meta"]
        self.assertEqual(meta["max_tokens"], 64000)
        self.assertEqual(meta["attempts"][0]["max_tokens_sent"], 64000)

    def test_the_floor_never_lowers_a_higher_run_cap(self):
        self.workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}]})
        code, _err = self.run_seat(extra=["--max-tokens", "100000"])
        self.assertEqual(code, 0)
        self.assertEqual(self.workspace.calls(harness.SLOW_MODEL)[0]["max_tokens"], 100000)


class LengthRetryTest(DispatchTestCase):

    def test_a_truncation_retries_once_at_double_the_cap_with_a_fresh_prompt(self):
        self.workspace.plan({harness.FAST_MODEL: [
            {"finish_reason": "length", "raw": '{"verdict": "fix-then-sh', "cost": 0.5, "completion_tokens": 32000},
            {"finish_reason": "stop", "body": harness.valid_report(), "cost": 0.25},
        ]})
        code, err = self.run_seat(family="xai", extra=["--max-tokens", "16000"])
        self.assertEqual(code, 0, err)

        calls = self.workspace.calls(harness.FAST_MODEL)
        self.assertEqual(len(calls), 2, "a truncation is retried exactly once before the repair path")
        self.assertEqual([c["max_tokens"] for c in calls], [16000, 32000], "the retry doubles the cap")
        self.assertEqual([c["is_repair"] for c in calls], [False, False],
                         "the retry sends a FRESH prompt; the truncated output is never quoted back")
        self.assertEqual(calls[0]["prompt_chars"], calls[1]["prompt_chars"],
                         "the fresh prompt is the original prompt, byte for byte")

    def test_the_failed_attempt_is_recorded_with_its_cap_and_its_cost(self):
        self.workspace.plan({harness.FAST_MODEL: [
            {"finish_reason": "length", "raw": "{", "cost": 0.5},
            {"finish_reason": "stop", "body": harness.valid_report(), "cost": 0.25},
        ]})
        self.assertEqual(self.run_seat(family="xai", extra=["--max-tokens", "16000"])[0], 0)
        meta = self.report("consistency-xai")["_meta"]
        self.assertEqual(len(meta["attempts"]), 2)
        self.assertEqual(meta["attempts"][0]["finish_reason"], "length")
        self.assertEqual(meta["attempts"][0]["max_tokens_sent"], 16000)
        self.assertEqual(meta["attempts"][1]["max_tokens_sent"], 32000)
        self.assertEqual(meta["length_truncations"], 1)
        self.assertAlmostEqual(meta["cost_usd"], 0.75, places=6,
                               msg="the truncated attempt's spend is carried, not dropped")

    def test_a_second_length_falls_through_to_the_repair_path(self):
        """Two truncations, then the repair re-ask — which is the one prompt that quotes back."""
        self.workspace.plan({harness.FAST_MODEL: [
            {"finish_reason": "length", "raw": '{"verdict": "fix', "cost": 0.5},
            {"finish_reason": "length", "raw": '{"verdict": "fix-then-ship", "summary": "x"', "cost": 0.5},
            {"finish_reason": "stop", "body": harness.valid_report(), "cost": 0.25},
        ]})
        code, err = self.run_seat(family="xai", extra=["--max-tokens", "16000"])
        self.assertEqual(code, 0, err)
        calls = self.workspace.calls(harness.FAST_MODEL)
        self.assertEqual(len(calls), 3)
        self.assertEqual([c["max_tokens"] for c in calls], [16000, 32000, 32000],
                         "the cap doubles once and stays there; the repair is not a third doubling")
        self.assertEqual([c["is_repair"] for c in calls], [False, False, True],
                         "only the repair re-ask quotes the previous response back")

    def test_a_validation_failure_alone_gets_one_repair_and_then_fails(self):
        self.workspace.plan({harness.FAST_MODEL: [
            {"body": {"verdict": "nonsense", "summary": "x", "findings": []}, "cost": 0.1},
            {"body": {"verdict": "still-nonsense", "summary": "x", "findings": []}, "cost": 0.1},
        ]})
        code, err = self.run_seat(family="xai")
        self.assertEqual(code, 3, err)
        calls = self.workspace.calls(harness.FAST_MODEL)
        self.assertEqual([c["is_repair"] for c in calls], [False, True])
        with open(os.path.join(self.out, "consistency-xai.failed.json"), "r", encoding="utf-8") as handle:
            failed = json.load(handle)
        self.assertAlmostEqual(failed["_meta"]["cost_usd"], 0.2, places=6,
                               msg="a failed seat still spent money and the record must say so")


class ForkTagAtIngestTest(DispatchTestCase):
    """The `fork` rule reaches a real reviewer, or it is a rule only the unit tests enforce.

    `lib/report.py` applies the rule only when it is called with `ingest=True`, and the one caller
    that matters here is `dispatch.py`. Every other test in this file feeds it reports that would
    pass either way, so without these two the wiring could be deleted and the suite stay green.
    """

    def untagged(self):
        """The house fixture's judgment call with its `fork` tag taken off again."""
        item = dict(harness.valid_report()["findings"][0])
        item["tags"] = ["configuration"]
        return harness.valid_report(findings=[item])

    def test_an_untagged_judgment_call_costs_the_seat_a_repair_re_ask(self):
        self.workspace.plan({harness.FAST_MODEL: [
            {"body": self.untagged()},
            {"body": harness.valid_report()},
        ]})
        code, err = self.run_seat(family="xai")
        self.assertEqual(code, 0, err)
        self.assertIn("carries the `fork` tag", err, "the reviewer is told which rule it broke")
        self.assertEqual([c["is_repair"] for c in self.workspace.calls(harness.FAST_MODEL)], [False, True])
        self.assertIn("fork", self.report("consistency-xai")["findings"][0]["tags"])

    def test_a_still_untagged_second_reply_retires_the_seat(self):
        self.workspace.plan({harness.FAST_MODEL: [{"body": self.untagged()}]})
        code, err = self.run_seat(family="xai")
        self.assertEqual(code, 3, err)
        self.assertIn("carries the `fork` tag", err)
        self.assertFalse(os.path.isfile(os.path.join(self.out, "consistency-xai.json")),
                         "an invalid report is never written as if it had passed")
        self.assertTrue(os.path.isfile(os.path.join(self.out, "consistency-xai.invalid.txt")),
                        "the raw response is kept so the seat's failure can be read")

    def test_a_gap_tagged_judgment_call_is_refused_the_same_way(self):
        item = dict(harness.valid_report()["findings"][0])
        item["tags"] = ["configuration", "gap"]
        self.workspace.plan({harness.FAST_MODEL: [{"body": harness.valid_report(findings=[item])}]})
        code, err = self.run_seat(family="xai")
        self.assertEqual(code, 3, err)
        self.assertIn("must not carry the `gap` tag", err)


class RegistryGateTest(DispatchTestCase):

    def test_a_model_absent_from_the_registry_is_a_composition_error(self):
        """A seat pinned to a model no model file covers.

        Since the registry became a directory of model files, a model absent from it is also
        absent from the derived tier map — so the way to reach this gate is the way an operator
        reaches it in practice: pin the seat to an id nobody has a file for.
        """
        code, err = self.run_seat(extra=["--model", "test/no-such-model"])
        self.assertEqual(code, 1, "an unpriced seat is refused before dispatch, not projected at zero")
        self.assertIn("test/no-such-model", err)
        self.assertIn("refresh_models.py", err)
        self.assertEqual(self.workspace.calls(), [], "nothing was dispatched")

    def test_a_model_whose_file_carries_no_price_is_the_same_composition_error(self):
        """Presence is not coverage: a null price would be a silent hole in the projection."""
        self.workspace.edit_model(harness.SLOW_MODEL, input_price_per_token=None,
                                  output_price_per_token=None)
        code, err = self.run_seat()
        self.assertEqual(code, 1, err)
        self.assertIn(harness.SLOW_MODEL, err)
        self.assertIn("carries no", err)


class EffortTest(DispatchTestCase):

    def test_the_config_effort_is_sent_when_the_registry_vocabulary_allows_it(self):
        self.workspace.plan({harness.FAST_MODEL: [{"body": harness.valid_report()}]})
        self.assertEqual(self.run_seat(family="xai")[0], 0)
        self.assertEqual(self.workspace.calls(harness.FAST_MODEL)[0]["effort"], "high")
        self.assertEqual(self.report("consistency-xai")["_meta"]["effort"], "high")

    def test_an_effort_outside_the_vocabulary_is_a_composition_error(self):
        """Not a warning. Dropping it would dispatch the seat at whatever depth the provider
        defaults to, and a panel whose seats ran at unintended depths is not a comparison."""
        workspace = harness.Workspace(effort={harness.FAST_MODEL: "xhigh", harness.SLOW_MODEL: "xhigh"})
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}]})
        stderr, saved = io.StringIO(), sys.stderr
        sys.stderr = stderr
        try:
            code = dispatch.main([
                "--persona", "lens-consistency", "--family", "kimi",
                "--artifact", workspace.artifact, "--out", self.out,
                "--config", workspace.config, "--models", workspace.registry, "--tier", "standard",
            ])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 1, stderr.getvalue())
        self.assertEqual(workspace.calls(harness.SLOW_MODEL), [], "nothing is dispatched")
        self.assertIn("composition error", stderr.getvalue())
        self.assertIn("max/high/low", stderr.getvalue(), "the message names the allowed values")
        self.assertIn(harness.SLOW_MODEL, stderr.getvalue())

    def test_a_level_the_model_does_not_map_is_a_composition_error(self):
        """The other half of the effort gate, and the half abstract effort made possible.

        A model file with no `effort` map used to mean "send no effort parameter at all". Under an
        abstract level that reading is gone: the run asked for `standard` and the answer is not
        "whatever depth the provider defaults to", it is that nobody has said what `standard` means
        on this model. The refusal names the model and the levels the file does map.
        """
        workspace = harness.Workspace(effort={harness.SLOW_MODEL: None})
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}]})
        stderr, saved = io.StringIO(), sys.stderr
        sys.stderr = stderr
        try:
            code = dispatch.main([
                "--persona", "lens-consistency", "--family", "kimi",
                "--artifact", workspace.artifact, "--out", self.out,
                "--config", workspace.config, "--models", workspace.registry, "--tier", "standard",
                "--effort", "standard",
            ])
        finally:
            sys.stderr = saved
        self.assertEqual(code, 1, stderr.getvalue())
        self.assertEqual(workspace.calls(harness.SLOW_MODEL), [], "nothing is dispatched")
        self.assertIn("composition error", stderr.getvalue())
        self.assertIn(harness.SLOW_MODEL, stderr.getvalue())
        self.assertIn("no effort levels at all", stderr.getvalue())

    def test_a_reasoning_token_budget_is_sent_instead_of_a_word_when_the_file_binds_one(self):
        """Both shapes ship. A file may bind a level to that model's rung or to a token budget."""
        workspace = harness.Workspace()
        self.addCleanup(workspace.close)
        workspace.apply_env()
        workspace.edit_model(harness.SLOW_MODEL,
                             effort={"light": "low", "standard": {"max_tokens": 8000}, "deep": "max"})
        workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}]})
        code = dispatch.main([
            "--persona", "lens-consistency", "--family", "kimi",
            "--artifact", workspace.artifact, "--out", self.out,
            "--config", workspace.config, "--models", workspace.registry, "--tier", "standard",
            "--effort", "standard",
        ])
        self.assertEqual(code, 0)
        call = workspace.calls(harness.SLOW_MODEL)[0]
        self.assertIsNone(call["effort"], "a budget is not a word and no word is sent")
        self.assertEqual(call.get("effort_tokens"), 8000)


class EnvelopeTest(DispatchTestCase):

    def test_the_report_records_the_artifact_revision_and_the_working_tree_label(self):
        self.workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}]})
        self.assertEqual(self.run_seat()[0], 0)
        report = self.report()
        self.assertEqual(report["artifact"], "docs/artifact.md",
                         "the reviewer cites the working-tree path, not the inputs/ copy it read")
        self.assertEqual(report["artifact_revision"], "deadbeef")
        self.assertEqual(report["_meta"]["connector"], "fake_backend")
        self.assertEqual(report["_meta"]["provider"], "fake")


class WireShapeTest(DispatchTestCase):
    """What actually lands in the HTTP body, through the **real** driver.

    The other effort tests assert on what `dispatch.py` hands the driver entry, which the scripted
    connector reads back from the same key — so they would still pass if the driver stopped sending
    it. These go the whole way: a config with an `effort` and a `provider_routing` entry, the real
    `openai_compat` driver, and `urllib.request.urlopen` monkeypatched to capture the request body.
    `provider_routing` has no other coverage at all and would pass with the feature deleted.
    """

    MODEL = "test/wire-model"
    BARE_MODEL = "test/bare-model"

    ROUTING = {"sort": "throughput", "quantizations": ["fp8", "bf16"], "allow_fallbacks": True}

    def setUp(self):
        DispatchTestCase.setUp(self)
        self.connector = self.workspace.path("wire-connector.json")
        with open(self.connector, "w", encoding="utf-8") as handle:
            json.dump({
                "name": "wire",
                "type": "openai_compat",
                "base_url": "https://example.invalid/api/v1",
                "api_key_secret": None,
                "api_key_env": "ENSEMBLE_REVIEW_TEST_KEY",
                "catalogue_url": "https://example.invalid/api/v1/models",
                "billing": "free",
                "provider_routing": {self.MODEL: self.ROUTING},
            }, handle)

        self.config = self.workspace.path("wire-config.json")
        with open(self.config, "w", encoding="utf-8") as handle:
            json.dump({
                "default_connector": self.connector,
                "default_tier": "standard",
                "default_effort": "standard",
                "family_order": ["kimi", "xai"],
                "tier_order": ["standard"],
            }, handle)

        self.registry = self.workspace.path("wire-models")
        priced = {
            "input_price_per_token": 1e-06, "output_price_per_token": 2e-06,
            "context_limit": 1000000, "output_token_prior": 1000, "min_max_tokens": None,
            "connector": "wire",
        }
        self.workspace.write_models(
            {
                self.MODEL: dict(priced, effort_vocabulary=["max", "high", "low"]),
                # The bare model has no recorded effort vocabulary at all, so there is no rung
                # this skill could name for it and no reasoning parameter is sent.
                self.BARE_MODEL: dict(priced, effort_vocabulary=None),
            },
            effort={self.MODEL: "high", self.BARE_MODEL: None},
            directory=self.registry,
            tiers={"kimi": self.MODEL, "xai": self.BARE_MODEL})

        self.sent = []
        saved = dispatch.urllib.request.urlopen
        dispatch.urllib.request.urlopen = self._capture
        self.addCleanup(setattr, dispatch.urllib.request, "urlopen", saved)

    def _capture(self, request, timeout=None):
        del timeout
        self.sent.append(json.loads(request.data.decode("utf-8")))
        return _Response(json.dumps({
            "id": "wire-1",
            "model": self.sent[-1]["model"],
            "provider": "example",
            "choices": [{"message": {"content": json.dumps(harness.valid_report())}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        }))

    def dispatch_seat(self, family):
        stderr, saved = io.StringIO(), sys.stderr
        sys.stderr = stderr
        try:
            code = dispatch.main([
                "--persona", "lens-consistency", "--family", family,
                "--artifact", self.workspace.artifact, "--out", self.out,
                "--config", self.config, "--models", self.registry,
                "--tier", "standard", "--max-tokens", "8000",
            ])
        finally:
            sys.stderr = saved
        return code, stderr.getvalue()

    def test_the_effort_and_the_provider_routing_reach_the_request_body(self):
        code, err = self.dispatch_seat("kimi")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.sent), 1)
        payload = self.sent[0]
        self.assertEqual(payload["model"], self.MODEL)
        self.assertEqual(payload["reasoning"], {"effort": "high"},
                         "the config's effort map reaches OpenRouter's `reasoning.effort`")
        self.assertEqual(payload["provider"], self.ROUTING,
                         "provider_routing is passed through verbatim as the request-level `provider` object")
        self.assertEqual(payload["max_tokens"], 8000)

    def test_a_model_in_neither_map_sends_neither_key(self):
        code, err = self.dispatch_seat("xai")
        self.assertEqual(code, 0, err)
        payload = self.sent[0]
        self.assertEqual(payload["model"], self.BARE_MODEL)
        self.assertNotIn("reasoning", payload,
                         "a model with no recorded effort vocabulary is dispatched with no effort parameter at all")
        self.assertNotIn("provider", payload,
                         "and with no routing object, so OpenRouter's own default routing stands")


class _Response(object):
    """The shape `urlopen` returns, enough of it for the driver."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def getcode(self):
        return 200

    def read(self):
        return self.body.encode("utf-8")


class AuthAndBackoffTest(DispatchTestCase):

    def test_an_auth_failure_halts_the_seat_with_exit_two(self):
        self.workspace.plan({harness.SLOW_MODEL: [{"raise": "auth"}]})
        code, err = self.run_seat()
        self.assertEqual(code, 2, "framework §20: auth failures are not transient")
        self.assertIn("not transient", err)
        self.assertEqual(len(self.workspace.calls(harness.SLOW_MODEL)), 1, "no retry on a 401")

    def test_a_transient_error_is_retried_three_times_with_exponential_backoff(self):
        """Asserted on the transport, which is where the policy lives — the driver never sees it."""
        slept = []
        attempts = []

        class _Response(object):
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def getcode(self):
                return 200

            def read(self):
                return b'{"ok": true}'

        def fake_urlopen(_request, timeout=None):
            attempts.append(timeout)
            if len(attempts) <= 3:
                raise OSError("connection reset")
            return _Response()

        saved = dispatch.urllib.request.urlopen
        dispatch.urllib.request.urlopen = fake_urlopen
        stderr, saved_err = io.StringIO(), sys.stderr
        sys.stderr = stderr
        try:
            import backends.openai_compat as driver
            request_fn = dispatch.make_http_request_fn(driver, sleep=slept.append)
            status, body = request_fn("https://example.invalid", {}, b"{}", 10)
        finally:
            dispatch.urllib.request.urlopen = saved
            sys.stderr = saved_err
        self.assertEqual((status, body), (200, '{"ok": true}'))
        self.assertEqual(slept, [1, 4, 16], "framework §11: three retries at 1 s, 4 s, 16 s")
        self.assertEqual(len(attempts), 4)

    def test_a_401_from_the_transport_raises_auth_failure_without_retrying(self):
        slept = []

        def fake_urlopen(_request, timeout=None):
            raise dispatch.urllib.error.HTTPError("https://example.invalid", 401, "Unauthorized", {}, None)

        saved = dispatch.urllib.request.urlopen
        dispatch.urllib.request.urlopen = fake_urlopen
        try:
            import backends.openai_compat as driver
            request_fn = dispatch.make_http_request_fn(driver, sleep=slept.append)
            with self.assertRaises(AuthFailure):
                request_fn("https://example.invalid", {}, b"{}", 10)
        finally:
            dispatch.urllib.request.urlopen = saved
        self.assertEqual(slept, [], "an auth failure must not spend three more calls proving itself")




class IncompleteReadTest(DispatchTestCase):
    """Run 3's `buildability-glm`: `IncompleteRead(528 bytes read)` after 147 s, and no retry.

    `http.client.IncompleteRead` descends from `HTTPException`, not from `OSError`, so the old
    transient set caught every socket failure and missed the one that actually happened — a provider
    closing the connection part way through the body. Both halves are asserted here: the transport
    retries it under framework §11, and a seat that still loses leaves an attempt record behind.
    """

    def test_a_mid_stream_incomplete_read_is_transient_and_recovers_on_the_retry(self):
        slept, tries = [], []

        def fake_urlopen(_request, timeout=None):
            tries.append(timeout)
            if len(tries) == 1:
                raise dispatch.http.client.IncompleteRead(b"x" * 528)
            return _Response('{"ok": true}')

        saved = dispatch.urllib.request.urlopen
        dispatch.urllib.request.urlopen = fake_urlopen
        stderr, saved_err = io.StringIO(), sys.stderr
        sys.stderr = stderr
        try:
            import backends.openai_compat as driver
            request_fn = dispatch.make_http_request_fn(driver, sleep=slept.append)
            status, body = request_fn("https://example.invalid", {}, b"{}", 10)
        finally:
            dispatch.urllib.request.urlopen = saved
            sys.stderr = saved_err

        self.assertEqual((status, body), (200, '{"ok": true}'))
        self.assertEqual(slept, [1], "one retry was enough; the backoff must not run to the end")
        self.assertEqual(len(tries), 2)
        self.assertIn("IncompleteRead", " ".join(request_fn.transient_notes),
                      "the exception class is what tells a flaky provider from a slow model")

    def test_an_incomplete_read_that_never_recovers_exhausts_the_three_retries(self):
        slept, tries = [], []

        def fake_urlopen(_request, timeout=None):
            tries.append(timeout)
            raise dispatch.http.client.IncompleteRead(b"x" * 528)

        saved = dispatch.urllib.request.urlopen
        dispatch.urllib.request.urlopen = fake_urlopen
        stderr, saved_err = io.StringIO(), sys.stderr
        sys.stderr = stderr
        try:
            import backends.openai_compat as driver
            request_fn = dispatch.make_http_request_fn(driver, sleep=slept.append)
            with self.assertRaises(dispatch.http.client.IncompleteRead):
                request_fn("https://example.invalid", {}, b"{}", 10)
        finally:
            dispatch.urllib.request.urlopen = saved
            sys.stderr = saved_err

        self.assertEqual(slept, [1, 4, 16], "framework §11: three retries at 1 s, 4 s, 16 s")
        self.assertEqual(len(tries), 4)
        self.assertIn("exhausted 3 retries", " ".join(request_fn.transient_notes))

    def test_every_named_transient_class_is_actually_caught_by_the_set(self):
        """The set is spelled out in `dispatch.py`; this asserts the spelling is not decorative."""
        for exception in (dispatch.urllib.error.URLError("x"), socket.timeout(),
                          TimeoutError(), ConnectionResetError(), ConnectionError(),
                          dispatch.http.client.IncompleteRead(b""),
                          dispatch.http.client.HTTPException()):
            self.assertIsInstance(exception, dispatch.TRANSIENT_EXCEPTIONS,
                                  "{0} is named in framework §11's policy".format(type(exception).__name__))

    def test_a_seat_that_dies_in_the_transport_still_writes_its_attempt_record(self):
        """No report, no cost — but never `attempts: null` over a seat that was called and failed."""
        self.workspace.plan({harness.FAST_MODEL: [{"raise": "incomplete"}]})
        code, err = self.run_seat(family="xai")
        self.assertEqual(code, 3, err)
        self.assertIn("IncompleteRead", err)

        path = os.path.join(self.out, "consistency-xai.failed.json")
        self.assertTrue(os.path.isfile(path), "a dispatch failure used to leave nothing on disk")
        with open(path, "r", encoding="utf-8") as handle:
            failed = json.load(handle)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["failure_stage"], "dispatch")
        self.assertIn("IncompleteRead", failed["errors"][0])

        attempts = failed["_meta"]["attempts"]
        self.assertEqual(len(attempts), 1, "the call that raised is still a call that was made")
        self.assertEqual(attempts[0]["finish_reason"], "dispatch-error")
        self.assertEqual(attempts[0]["max_tokens_sent"], registry_lib.DEFAULT_MAX_TOKENS)
        self.assertIsNotNone(attempts[0]["elapsed_s"])
        self.assertIn("exception: IncompleteRead", attempts[0]["notes"])
        self.assertIn("IncompleteRead", attempts[0]["validation_errors"][0])


class RepairQuoteCapTest(DispatchTestCase):
    """The repair re-ask is the one prompt that quotes back, so it is the one that needs a ceiling."""

    def test_a_response_inside_the_cap_is_quoted_whole(self):
        text = "x" * (dispatch.REPAIR_QUOTE_CHARS - 1)
        quoted, note = dispatch.quote_for_repair(text)
        self.assertEqual(quoted, text)
        self.assertIsNone(note)

    def test_an_over_long_response_is_elided_from_the_middle_and_says_how_much(self):
        text = "H" * 10000 + "M" * 200000 + "T" * 10000
        quoted, note = dispatch.quote_for_repair(text)
        self.assertLess(len(quoted), len(text))
        self.assertLessEqual(len(quoted) - len("\n…[  characters elided from the middle of your previous response]…\n"),
                             dispatch.REPAIR_QUOTE_CHARS + 16)
        self.assertTrue(quoted.startswith("H"), "the head is kept")
        self.assertTrue(quoted.endswith("T"), "so is the tail, which is where the parse error points")
        self.assertIn("characters elided", quoted)
        self.assertIn("cap {0}".format(dispatch.REPAIR_QUOTE_CHARS), note)

    def test_the_cap_is_well_under_the_200000_characters_it_replaced(self):
        self.assertLessEqual(dispatch.REPAIR_QUOTE_CHARS, 60000)

    def test_the_repair_prompt_carries_the_elision_and_the_note_reaches_the_attempt(self):
        oversized = '{"verdict": "fix-then-ship", "summary": "' + "y" * 150000
        self.workspace.plan({harness.FAST_MODEL: [
            {"raw": oversized, "cost": 0.1},
            {"body": harness.valid_report(), "cost": 0.1},
        ]})
        code, err = self.run_seat(family="xai")
        self.assertEqual(code, 0, err)
        calls = self.workspace.calls(harness.FAST_MODEL)
        self.assertEqual([c["is_repair"] for c in calls], [False, True])
        self.assertLess(calls[1]["prompt_chars"], calls[0]["prompt_chars"] + dispatch.REPAIR_QUOTE_CHARS + 2000,
                        "the repair prompt is the original plus a capped quote, not plus 150k characters")
        notes = self.report("consistency-xai")["_meta"]["attempts"][1]["notes"]
        self.assertTrue(any("elided from the middle of the repair quote" in note for note in notes), notes)

    def test_a_length_truncation_is_still_never_quoted_back(self):
        """Stage 2a's rule, re-asserted here because the cap is not what enforces it."""
        self.workspace.plan({harness.FAST_MODEL: [
            {"finish_reason": "length", "raw": "z" * 150000, "cost": 0.5},
            {"body": harness.valid_report(), "cost": 0.1},
        ]})
        code, err = self.run_seat(family="xai", extra=["--max-tokens", "16000"])
        self.assertEqual(code, 0, err)
        calls = self.workspace.calls(harness.FAST_MODEL)
        self.assertEqual([c["is_repair"] for c in calls], [False, False])
        self.assertEqual(calls[0]["prompt_chars"], calls[1]["prompt_chars"],
                         "the length retry sends a fresh prompt; the truncated bytes are discarded")


class CostSourceTest(unittest.TestCase):
    """`usage.cost` is the billed figure, zero included — settled against the credit ledger.

    OpenRouter's `/credits` read `total_usage` 7.770718 before the first unattended run and
    10.522836 after it: a delta of $2.752118 against the manifest's recorded $2.752117. The errored
    attempt that reported `cost: 0` beside an `upstream_inference_cost` of $1.816461 was not billed
    for it. The upstream figure is kept as `upstream_unbilled_usd` and never folded into the cost.
    """

    def setUp(self):
        from backends import openai_compat
        self.driver = openai_compat
        self.prices = {"input": 2.1e-06, "output": 1.095e-05}

    # The run's own row: `finish_reason: error`, billed 0, upstream $1.816461.
    ERRORED = {
        "prompt_tokens": 97967, "completion_tokens": 101504, "cost": 0,
        "cost_details": {"upstream_inference_cost": 1.816461},
    }

    def test_a_billed_cost_is_taken_as_given(self):
        cost, source = self.driver._cost({"cost": 0.883682464}, self.prices)
        self.assertAlmostEqual(cost, 0.883682464, places=9)
        self.assertEqual(source, "provider")

    def test_a_billed_zero_beside_an_upstream_cost_stays_zero(self):
        cost, source = self.driver._cost(self.ERRORED, self.prices)
        self.assertEqual(cost, 0.0, "the ledger says an errored generation costs the account nothing")
        self.assertEqual(source, "provider")

    def test_the_unbilled_upstream_figure_is_recorded_separately(self):
        self.assertAlmostEqual(self.driver._upstream_unbilled(self.ERRORED, 0.0), 1.816461, places=6)

    def test_an_upstream_cost_equal_to_the_bill_is_not_unbilled_at_all(self):
        """The ordinary row: both fields carry the same number and there is no divergence to report."""
        usage = {"cost": 0.883682464,
                 "cost_details": {"upstream_inference_cost": 0.883682464}}
        self.assertIsNone(self.driver._upstream_unbilled(usage, 0.883682464))

    def test_a_usage_block_with_no_cost_key_is_estimated_from_usage_and_labelled(self):
        usage = {"prompt_tokens": 100000, "completion_tokens": 10000}
        cost, source = self.driver._cost(usage, self.prices)
        self.assertEqual(source, "estimated")
        self.assertAlmostEqual(cost, 100000 * 2.1e-06 + 10000 * 1.095e-05, places=9)

    def test_an_unpriced_model_with_no_reported_cost_is_unknown_not_zero(self):
        self.assertEqual(self.driver._cost({"prompt_tokens": 10, "completion_tokens": 10}, None),
                         (None, None))

    def test_a_free_call_stays_a_reported_zero_and_is_never_estimated_over(self):
        self.assertEqual(self.driver._cost({"cost": 0}, None), (0.0, "provider"))
        self.assertEqual(self.driver._cost({"cost": 0, "prompt_tokens": 100000,
                                            "completion_tokens": 10000}, self.prices),
                         (0.0, "provider"))

    def _meta_for(self, spec):
        workspace = harness.Workspace()
        self.addCleanup(workspace.close)
        workspace.apply_env()
        out = os.path.join(workspace.root, "run")
        os.makedirs(out)
        workspace.plan({harness.FAST_MODEL: [spec]})
        code = dispatch.main([
            "--persona", "lens-consistency", "--family", "xai",
            "--artifact", workspace.artifact, "--out", out,
            "--config", workspace.config, "--models", workspace.registry, "--tier", "standard",
        ])
        self.assertEqual(code, 0)
        with open(os.path.join(out, "consistency-xai.json"), "r", encoding="utf-8") as handle:
            return json.load(handle)["_meta"]

    def test_the_source_reaches_the_attempt_record_and_the_seat_meta(self):
        meta = self._meta_for({"body": harness.valid_report(), "cost": 0.25})
        self.assertAlmostEqual(meta["cost_usd"], 0.25, places=6)
        self.assertEqual(meta["cost_sources"], ["provider"])
        self.assertFalse(meta["cost_estimated"])
        self.assertEqual(meta["attempts"][0]["cost_source"], "provider")

    def test_unbilled_upstream_spend_rolls_up_without_touching_the_cost(self):
        meta = self._meta_for({"body": harness.valid_report(), "cost": 0,
                               "cost_details": {"upstream_inference_cost": 1.816461}})
        self.assertEqual(meta["cost_usd"], 0.0, "the billed total must reconcile against the ledger")
        self.assertAlmostEqual(meta["upstream_unbilled_usd"], 1.816461, places=6)
        self.assertAlmostEqual(meta["attempts"][0]["upstream_unbilled_usd"], 1.816461, places=6)

    def test_a_call_with_nothing_unbilled_carries_a_null_rather_than_a_zero(self):
        meta = self._meta_for({"body": harness.valid_report(), "cost": 0.25})
        self.assertIsNone(meta["upstream_unbilled_usd"])
        self.assertIsNone(meta["attempts"][0]["upstream_unbilled_usd"])


class ShippedDriverAssemblyTest(unittest.TestCase):
    """The **real** `openai_compat.dispatch_detailed`, against a fake `urlopen`.

    Every other cost test in this file calls `_cost` directly or goes through the scripted double.
    Neither reaches the line that puts the numbers into the result dict, so a mutation that dropped
    `cost_source` or `upstream_unbilled_usd` from the assembly survived all of them. This drives the
    shipped driver end to end over a hand-written OpenRouter response body.
    """

    ERRORED_BODY = {
        "id": "gen-test",
        "model": "moonshotai/kimi-k3",
        "provider": "Moonshot AI",
        "choices": [{"finish_reason": "error", "message": {"content": '{"verdict": "fix-then-'}}],
        "usage": {
            "prompt_tokens": 97967, "completion_tokens": 101504, "total_tokens": 199471,
            "cost": 0, "is_byok": False,
            "cost_details": {"upstream_inference_cost": 1.816461},
            "completion_tokens_details": {"reasoning_tokens": 97562},
        },
    }

    def _dispatch(self, body):
        from backends import openai_compat

        def request_fn(_url, _headers, _body_bytes, _timeout):
            return 200, json.dumps(body)

        return openai_compat.dispatch_detailed(
            "system", "user", "moonshotai/kimi-k3",
            {"base_url": "https://example.invalid/api/v1", "api_key": "k",
             "max_tokens": 128000, "prices": {"input": 2.1e-06, "output": 1.095e-05}},
            {"type": "json_object"}, request_fn)

    def test_a_billed_zero_beside_an_upstream_cost_reaches_the_result_intact(self):
        result = self._dispatch(self.ERRORED_BODY)
        self.assertEqual(result["cost_usd"], 0.0, "the ledger says the errored generation was free")
        self.assertEqual(result["cost_source"], "provider")
        self.assertAlmostEqual(result["upstream_unbilled_usd"], 1.816461, places=6)
        self.assertEqual(result["finish_reason"], "error")
        self.assertEqual(result["reasoning_tokens"], 97562)

    def test_the_same_numbers_reach_the_drivers_own_attempt_entry(self):
        attempt = self._dispatch(self.ERRORED_BODY)["attempts"][0]
        self.assertEqual(attempt["cost_usd"], 0.0)
        self.assertEqual(attempt["cost_source"], "provider")
        self.assertAlmostEqual(attempt["upstream_unbilled_usd"], 1.816461, places=6)
        self.assertEqual(attempt["max_tokens_sent"], 128000)

    def test_the_divergence_is_named_in_the_drivers_notes(self):
        notes = " ".join(self._dispatch(self.ERRORED_BODY)["notes"])
        self.assertIn("not billed", notes)
        self.assertIn("1.816461", notes)

    def test_an_ordinary_call_carries_the_billed_cost_and_nothing_unbilled(self):
        body = json.loads(json.dumps(self.ERRORED_BODY))
        body["choices"][0]["finish_reason"] = "stop"
        body["usage"]["cost"] = 0.765174
        body["usage"]["cost_details"]["upstream_inference_cost"] = 0.765174
        result = self._dispatch(body)
        self.assertAlmostEqual(result["cost_usd"], 0.765174, places=6)
        self.assertIsNone(result["upstream_unbilled_usd"])
        self.assertNotIn("not billed", " ".join(result["notes"]))

    def test_a_usage_block_with_no_cost_key_is_estimated_by_the_shipped_driver(self):
        body = json.loads(json.dumps(self.ERRORED_BODY))
        body["choices"][0]["finish_reason"] = "stop"
        del body["usage"]["cost"]
        del body["usage"]["cost_details"]
        result = self._dispatch(body)
        self.assertEqual(result["cost_source"], "estimated")
        self.assertAlmostEqual(result["cost_usd"], 97967 * 2.1e-06 + 101504 * 1.095e-05, places=9)
        self.assertIn("estimated from", " ".join(result["notes"]))

    def test_the_shipped_results_satisfy_the_contract_including_its_optional_fields(self):
        from backends import base
        silent = []
        self.assertEqual(base.check_result(self._dispatch(self.ERRORED_BODY), warn=silent.append), [])
        self.assertEqual(silent, [], "the shipped driver omits neither optional field")


class DriverContractTest(unittest.TestCase):
    """`backends/base.py` states the connector contract; these assert it describes what ships.

    A contract document nothing checks is a contract that drifts, and the cost of the drift lands on
    whoever writes the next driver — the `azure_openai` one a project needs to route a confidential
    review to a host with a data agreement.
    """

    def test_the_shipped_driver_satisfies_the_documented_contract(self):
        from backends import base, openai_compat
        self.assertEqual(base.check_driver(openai_compat), [])

    def test_the_scripted_test_driver_satisfies_it_too(self):
        from backends import base
        driver = load_driver(harness.FAKE_BACKEND)
        self.assertEqual(base.check_driver(driver), [])

    def test_one_dispatch_detailed_return_carries_every_field_the_manifest_promises(self):
        from backends import base
        driver = load_driver(harness.FAKE_BACKEND)
        workspace = harness.Workspace()
        self.addCleanup(workspace.close)
        workspace.apply_env()
        result = driver.dispatch_detailed("system", "user", harness.FAST_MODEL,
                                          {"max_tokens": 1000}, {"type": "json_object"}, None)
        self.assertEqual(base.check_result(result), [])

    def test_a_driver_that_omits_the_optional_accounting_fields_still_passes(self):
        """A connector written before those fields existed is not a broken connector."""
        from backends import base
        legacy = {
            "text": "{}", "usage": {}, "reasoning_tokens": None, "cost_usd": 0.1,
            "model": "m", "provider": "p", "connector": "legacy", "effort": None,
            "max_tokens": 1000, "finish_reason": "stop",
            "attempts": [{"max_tokens_sent": 1000, "finish_reason": "stop",
                          "usage": {}, "cost_usd": 0.1}],
        }
        warnings = []
        self.assertEqual(base.check_result(legacy, warn=warnings.append), [],
                         "the optional fields are optional, not required")
        text = " ".join(warnings)
        self.assertIn("cost_source", text, "but their absence is worth a line on stderr")
        self.assertIn("upstream_unbilled_usd", text)
        self.assertIn("defaulting to None", text)
        self.assertIn("effort_tokens", text)
        self.assertEqual(len(warnings), 5, "three on the result, two on the attempt")

    def test_check_driver_folds_in_the_result_contract_when_it_is_given_one(self):
        """The optional fields live in a result, so a module alone can never reveal them."""
        from backends import base
        driver = load_driver(harness.FAKE_BACKEND)
        warnings = []
        self.assertEqual(base.check_driver(driver, warn=warnings.append), [])
        self.assertEqual(warnings, [], "no result was supplied, so there is nothing to warn about")

        legacy_result = {
            "text": "{}", "usage": {}, "reasoning_tokens": None, "cost_usd": 0.1,
            "model": "m", "provider": "p", "connector": "legacy", "effort": None,
            "max_tokens": 1000, "finish_reason": "stop",
            "attempts": [{"max_tokens_sent": 1000, "finish_reason": "stop", "usage": {}, "cost_usd": 0.1}],
        }
        self.assertEqual(base.check_driver(driver, result=legacy_result, warn=warnings.append), [])
        self.assertIn("cost_source", " ".join(warnings))

    def test_a_result_missing_a_required_field_is_still_a_failure(self):
        from backends import base
        broken = {"text": "{}", "attempts": [{}]}
        problems = base.check_result(broken, warn=lambda _: None)
        self.assertTrue(any("`cost_usd`" in p for p in problems))
        self.assertTrue(any("`attempts[0]` has no `max_tokens_sent`" in p for p in problems))

    def test_the_defaults_are_filled_in_where_a_driver_attempt_becomes_a_manifest_attempt(self):
        from backends import base
        filled = base.with_attempt_defaults({"max_tokens_sent": 1000, "cost_usd": 0.1})
        self.assertIsNone(filled["cost_source"])
        self.assertIsNone(filled["upstream_unbilled_usd"])
        self.assertEqual(filled["cost_usd"], 0.1)

    def test_a_driver_that_does_supply_them_is_not_overwritten(self):
        from backends import base
        filled = base.with_attempt_defaults({"cost_source": "provider", "upstream_unbilled_usd": 1.5})
        self.assertEqual(filled["cost_source"], "provider")
        self.assertEqual(filled["upstream_unbilled_usd"], 1.5)


class ScriptedDriverDelegatesItsAccountingTest(unittest.TestCase):
    """The double must **call** the shipped cost rule, not reimplement it.

    A reimplementation with today's semantics is behaviourally invisible — no assertion about
    outputs can tell the two apart — which is exactly why it is dangerous: the day the shipped rule
    changes, every test still passes against a double that kept the old one. So this asserts the
    delegation directly, by making the shipped functions return something unmistakable.
    """

    def setUp(self):
        from backends import openai_compat
        import fake_backend
        self.real = openai_compat
        self.fake = fake_backend

    def test_the_double_calls_the_shipped_cost_rule(self):
        saved = self.real._cost
        self.real._cost = lambda usage, prices: ("SENTINEL", "sentinel-source")
        self.addCleanup(setattr, self.real, "_cost", saved)
        self.assertEqual(self.fake._cost({"cost": 0.01}), ("SENTINEL", "sentinel-source"),
                         "the fake backend reimplemented `_cost` instead of calling it")

    def test_the_double_calls_the_shipped_unbilled_rule(self):
        saved = self.real._upstream_unbilled
        self.real._upstream_unbilled = lambda usage, billed: "SENTINEL"
        self.addCleanup(setattr, self.real, "_upstream_unbilled", saved)
        self.assertEqual(self.fake._upstream_unbilled({}, 0.0), "SENTINEL",
                         "the fake backend reimplemented `_upstream_unbilled` instead of calling it")

    def test_the_two_agree_on_the_rows_the_plan_file_can_script(self):
        rows = [
            {"cost": 0.883682464},
            {"cost": 0, "cost_details": {"upstream_inference_cost": 1.816461}},
            {"cost": 0, "cost_details": {"upstream_inference_cost": 0}},
            {"cost": 0.25, "cost_details": {"upstream_inference_cost": 0.25}},
        ]
        for usage in rows:
            self.assertEqual(self.fake._cost(usage), self.real._cost(usage, None), usage)
            billed = self.real._cost(usage, None)[0]
            self.assertEqual(self.fake._upstream_unbilled(usage, billed),
                             self.real._upstream_unbilled(usage, billed), usage)


class JudgeRepairPromptTest(unittest.TestCase):
    """`lib/judge.build_repair_prompt` had no test at all, and its own 200,000-character quote.

    It matters more here than on a seat: the judgment call's prompt already runs to six figures of
    tokens — the first unattended run's was 127,624 — so an uncapped quote of a rejected patch would
    make the re-ask the dearest call in the run by a distance.
    """

    def setUp(self):
        from lib import judge as judge_lib
        from lib import report as report_lib
        self.judge = judge_lib
        self.report = report_lib

    def test_a_short_patch_is_quoted_whole(self):
        prompt = self.judge.build_repair_prompt("ORIGINAL", '{"clusters": []}', ["bad"])
        self.assertIn('{"clusters": []}', prompt)
        self.assertIn("ORIGINAL", prompt)
        self.assertIn("===== YOUR PREVIOUS RESPONSE =====", prompt)

    def test_an_over_long_patch_is_capped_at_the_shared_ceiling_with_the_elision_marker(self):
        oversized = "H" * 10000 + "M" * 300000 + "T" * 10000
        prompt = self.judge.build_repair_prompt("ORIGINAL", oversized, ["bad"])
        self.assertLess(len(prompt), len(oversized),
                        "the 200,000-character quote this replaced was not a cap")
        self.assertIn("characters elided from the middle of your previous response", prompt)
        quoted = prompt.split("===== YOUR PREVIOUS RESPONSE =====\n")[1].split(
            "\n===== END PREVIOUS RESPONSE =====")[0]
        self.assertLessEqual(len(quoted), self.report.REPAIR_QUOTE_CHARS + 200)
        self.assertTrue(quoted.startswith("H"))
        self.assertTrue(quoted.endswith("T"))

    def test_it_uses_the_same_ceiling_as_the_seats_repair_prompt(self):
        oversized = "z" * (self.report.REPAIR_QUOTE_CHARS * 3)
        judge_quoted, _note = self.report.quote_for_repair(oversized)
        self.assertIn(judge_quoted[:500], self.judge.build_repair_prompt("O", oversized, ["bad"]))
        self.assertIn(judge_quoted[:500], dispatch.build_repair_prompt("O", oversized, ["bad"]))

    def test_the_instructions_that_make_it_a_judgment_re_ask_survive_the_cap(self):
        prompt = self.judge.build_repair_prompt("ORIGINAL", "x" * 400000, ["bad"])
        self.assertIn("you may not emit `rulings`", prompt)
        self.assertIn("Re-emit the WHOLE patch", prompt)
        self.assertIn("- bad", prompt)


class PersonaContextTest(unittest.TestCase):
    """The system message is built from the persona's declared `context`, on both legs.

    It used to be built two ways: the judge leg read the declaration and the seat leg hardcoded the
    finding schema. A workspace persona naming a second reference therefore got it as a judge and
    silently lost it as a reviewer — and the persona's system message is the one thing this skill
    promises is identical across families.
    """

    EXTRA = "house-style.md"
    MARKER = "The house style forbids the passive voice in a finding's claim."

    def setUp(self):
        self.workspace = harness.Workspace()
        self.workspace.apply_env()
        self.out = self.workspace.path("run")
        os.makedirs(self.out)
        self.addCleanup(self.workspace.close)
        self.workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}]})

    def override_persona(self, context_lines):
        """A workspace copy of a shipped lens, with its `context` list rewritten."""
        packaged = os.path.join(harness.SKILL_DIR, "agents", "lens-consistency.md")
        with open(packaged, "r", encoding="utf-8") as handle:
            text = handle.read()
        before, marker, after = text.partition("context:\n  - finding-schema.md\n")
        self.assertTrue(marker, "the shipped persona no longer declares its context the expected way")
        self.workspace.override(os.path.join("agents", "lens-consistency.md"),
                                text=before + context_lines + after)
        self.workspace.override(os.path.join("references", self.EXTRA), text="# House style\n\n" + self.MARKER + "\n")

    def dispatch_seat(self):
        argv = [
            "--persona", "lens-consistency", "--family", "kimi",
            "--artifact", self.workspace.artifact,
            "--out", self.out,
            "--workspace", self.workspace.root,
            "--config", self.workspace.config,
            "--models", self.workspace.registry,
            "--tier", "standard",
        ]
        stderr, saved = io.StringIO(), sys.stderr
        sys.stderr = stderr
        try:
            code = dispatch.main(argv)
        finally:
            sys.stderr = saved
        return code, stderr.getvalue()

    def expected_system_chars(self, order):
        """The exact system message this persona should produce, built by the real builder.

        Exact rather than "longer than the finding schema": the persona body alone clears that bar,
        so a seat leg that had dropped the second reference would still pass a size floor. Length
        equality against the builder's own output is what makes the assertion bite.
        """
        paths = paths_lib.Paths(workspace=self.workspace.root)
        _frontmatter, body = report_lib.parse_agent_file(paths.persona("lens-consistency"))
        return len(dispatch.build_system_prompt(body, [paths.reference(name) for name in order]))

    def test_a_second_declared_reference_reaches_the_seats_system_message(self):
        self.override_persona("context:\n  - finding-schema.md\n  - {0}\n".format(self.EXTRA))
        code, err = self.dispatch_seat()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.workspace.calls()[0]["system_chars"],
                         self.expected_system_chars(["finding-schema.md", self.EXTRA]),
                         "the persona body, the finding schema and the second reference, in declaration order")

    def test_declaration_order_is_the_order_the_blocks_are_concatenated_in(self):
        self.override_persona("context:\n  - {0}\n  - finding-schema.md\n".format(self.EXTRA))
        code, err = self.dispatch_seat()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.workspace.calls()[0]["system_chars"],
                         self.expected_system_chars([self.EXTRA, "finding-schema.md"]))
        self.assertNotIn("does not name finding-schema.md", err, "it is declared, just not first")

    def test_the_finding_schema_is_loaded_even_when_the_declaration_forgets_it(self):
        """By invariant, not by declaration: a reviewer never sees a schema it was not shown."""
        self.override_persona("context:\n  - {0}\n".format(self.EXTRA))
        code, err = self.dispatch_seat()
        self.assertEqual(code, 0, err)
        self.assertIn("does not name finding-schema.md", err)
        self.assertEqual(self.workspace.calls()[0]["system_chars"],
                         self.expected_system_chars([self.EXTRA, "finding-schema.md"]),
                         "appended last, after everything the persona did declare")

    def test_the_resolver_is_the_one_the_judge_leg_uses(self):
        """Same function, so the two legs cannot drift apart again without a test moving."""
        paths = paths_lib.Paths(workspace=harness.SKILL_DIR)
        frontmatter, _body = report_lib.parse_agent_file(
            os.path.join(harness.SKILL_DIR, "agents", "synthesis.md"))
        resolved = dispatch.persona_context_paths(paths, frontmatter)
        self.assertEqual([os.path.basename(p) for p in resolved],
                         ["reconciliation.md", "finding-schema.md"],
                         "declaration order, which is the order the blocks are concatenated in")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""`dispatch.py`'s three retry paths, the completion cap, and the registry gate.

No network and no paid call: the connector is `fake_backend.py`, loaded by file path the same way a
project's own workspace driver would be, and scripted per call through a plan file.

    python3 scripts/tests/test_dispatch_retry.py
"""

import io
import json
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TESTS_DIR)
sys.path.insert(0, os.path.dirname(TESTS_DIR))

import dispatch  # noqa: E402
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
        with open(self.workspace.registry, "r", encoding="utf-8") as handle:
            registry = json.load(handle)
        del registry["models"][harness.SLOW_MODEL]
        with open(self.workspace.registry, "w", encoding="utf-8") as handle:
            json.dump(registry, handle)
        code, err = self.run_seat()
        self.assertEqual(code, 1, "an unpriced seat is refused before dispatch, not projected at zero")
        self.assertIn(harness.SLOW_MODEL, err)
        self.assertIn("refresh_models.py", err)
        self.assertEqual(self.workspace.calls(), [], "nothing was dispatched")


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

    def test_a_model_absent_from_the_effort_map_is_sent_no_effort_at_all(self):
        self.workspace.plan({harness.SLOW_MODEL: [{"body": harness.valid_report()}]})
        self.assertEqual(self.run_seat()[0], 0)
        self.assertIsNone(self.workspace.calls(harness.SLOW_MODEL)[0]["effort"])


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
        self.config = self.workspace.path("wire-config.json")
        with open(self.config, "w", encoding="utf-8") as handle:
            json.dump({"openrouter": {
                "type": "openai_compat",
                "base_url": "https://example.invalid/api/v1",
                "api_key_secret": None,
                "api_key_env": "ENSEMBLE_REVIEW_TEST_KEY",
                "default_tier": "standard",
                "tiers": {"standard": {"kimi": self.MODEL, "xai": self.BARE_MODEL}},
                "effort": {self.MODEL: "high"},
                "provider_routing": {self.MODEL: self.ROUTING},
            }}, handle)

        self.registry = self.workspace.path("wire-models.json")
        priced = {
            "input_price_per_token": 1e-06, "output_price_per_token": 2e-06,
            "context_limit": 1000000, "output_token_prior": 1000, "min_max_tokens": None,
        }
        with open(self.registry, "w", encoding="utf-8") as handle:
            json.dump({"schema_version": "1", "models": {
                self.MODEL: dict(priced, effort_vocabulary=["max", "high", "low"]),
                self.BARE_MODEL: dict(priced, effort_vocabulary=["high", "low"]),
            }}, handle)

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
                         "a model absent from the effort map is dispatched with no effort parameter at all")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

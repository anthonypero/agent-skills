"""A scripted connector, for tests. No network, no key, no cost.

It is a real driver: `backends/__init__.py` loads any `type` that names a `.py` file, which is how a
project binds a family to a connector of its own without editing the read-only package. A test points
a config's `type` at this file and scripts the calls through two environment variables:

- `FAKE_BACKEND_PLAN` — a JSON file, `{model id: [call spec, ...]}`. Call `n` on a model takes spec
  `n`; past the end of the list the last spec repeats.
- `FAKE_BACKEND_LOG` — a JSON-lines file this driver appends one record per call to, so a test can
  assert on the cap sent, the effort sent, and whether the prompt was fresh or a repair re-ask.

A call spec:

    {"finish_reason": "stop",                      # or "length"
     "body": {"verdict": "ship", "summary": "…", "findings": []},   # serialized as the content
     "raw": "…",                                   # content verbatim, instead of `body`
     "prompt_tokens": 1000, "completion_tokens": 500, "reasoning_tokens": 100,
     "cost": 0.01,
     "raise": "auth" | "transient" | "error" | "unavailable" | "incomplete"}

`"unavailable"` is a 404 naming the model, which is what a provider answers when it will not serve
that model at all — the row the re-seat-once path turns on. `"incomplete"` is
`http.client.IncompleteRead`, the provider closing the connection mid-body, which is what took the
first unattended run's `buildability-glm` seat down.
"""

import http.client
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backends import AuthFailure  # noqa: E402
from backends import openai_compat  # noqa: E402

DEFAULT_MAX_TOKENS = 32000

REPAIR_MARKER = "===== YOUR PREVIOUS RESPONSE ====="


class _Rejected(Exception):
    def __init__(self, status, body):
        super(_Rejected, self).__init__("HTTP {0}: {1}".format(status, (body or "")[:400]))
        self.status = status
        self.body = body or ""


Rejected = _Rejected


def _plan():
    path = os.environ.get("FAKE_BACKEND_PLAN")
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _log(record):
    path = os.environ.get("FAKE_BACKEND_LOG")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _calls_so_far(model):
    path = os.environ.get("FAKE_BACKEND_LOG")
    if not path or not os.path.isfile(path):
        return 0
    count = 0
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                if json.loads(line).get("model") == model:
                    count += 1
            except ValueError:
                continue
    return count


def dispatch_detailed(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn):
    plan = _plan().get(model) or [{}]
    index = min(_calls_so_far(model), len(plan) - 1)
    spec = plan[index] or {}

    max_tokens = int(backend_entry.get("max_tokens") or DEFAULT_MAX_TOKENS)
    _log({
        "model": model,
        "max_tokens": max_tokens,
        "effort": backend_entry.get("reasoning_effort"),
        "is_repair": REPAIR_MARKER in user_prompt,
        "prompt_chars": len(user_prompt),
        "system_chars": len(system_prompt),
        "at": time.time(),
    })

    failure = spec.get("raise")
    if failure == "auth":
        raise AuthFailure("the provider answered HTTP 401: scripted auth failure")
    if failure == "transient":
        raise _Rejected(503, "service unavailable")
    if failure == "error":
        raise RuntimeError("provider error: scripted failure")
    if failure == "unavailable":
        raise _Rejected(404, "No endpoints found for {0}.".format(model))
    if failure == "incomplete":
        raise http.client.IncompleteRead(b"x" * spec.get("partial_bytes", 528))

    if "raw" in spec:
        text = spec["raw"]
    else:
        text = json.dumps(spec.get("body") or {"verdict": "ship", "summary": "nothing to report", "findings": []})

    usage = {
        "prompt_tokens": spec.get("prompt_tokens", 1000),
        "completion_tokens": spec.get("completion_tokens", 500),
        "total_tokens": spec.get("prompt_tokens", 1000) + spec.get("completion_tokens", 500),
        "cost": spec.get("cost", 0.01),
        "completion_tokens_details": {"reasoning_tokens": spec.get("reasoning_tokens", 100)},
    }
    if "cost_details" in spec:
        usage["cost_details"] = spec["cost_details"]
    finish = spec.get("finish_reason", "stop")
    cost_usd, cost_source = _cost(usage)
    upstream_unbilled = _upstream_unbilled(usage, cost_usd)
    return {
        "text": text,
        "usage": usage,
        "reasoning_tokens": usage["completion_tokens_details"]["reasoning_tokens"],
        "cost_usd": cost_usd,
        "cost_source": cost_source,
        "upstream_unbilled_usd": upstream_unbilled,
        "model": model,
        "provider": "fake",
        "connector": "fake_backend",
        "effort": backend_entry.get("reasoning_effort"),
        "max_tokens": max_tokens,
        "finish_reason": finish,
        "notes": [],
        "response": {"id": "fake-{0}".format(index)},
        "attempts": [{
            "n": 1,
            "max_tokens_sent": max_tokens,
            "finish_reason": finish,
            "validation_errors": None,
            "usage": usage,
            "reasoning_tokens": usage["completion_tokens_details"]["reasoning_tokens"],
            "cost_usd": cost_usd,
            "cost_source": cost_source,
            "upstream_unbilled_usd": upstream_unbilled,
            "elapsed_s": 0.0,
            "notes": [],
            "response_id": "fake-{0}".format(index),
        }],
    }


def dispatch(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn):
    return dispatch_detailed(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn)["text"]


def _cost(usage):
    """The shipped driver's own cost rule, **called** rather than mirrored.

    A copy here would be a copy that drifts: a mutation to `openai_compat._cost` that this file
    reimplemented would leave every test passing against the double while the real driver did
    something else. Calling it means the scripted `cost_details` rows exercise the shipped
    accounting, and the only thing this module still owns is what the provider is pretending to say.
    """
    return openai_compat._cost(usage, None)


def _upstream_unbilled(usage, billed):
    """The shipped driver's rule, called for the same reason `_cost` is."""
    return openai_compat._upstream_unbilled(usage, billed)

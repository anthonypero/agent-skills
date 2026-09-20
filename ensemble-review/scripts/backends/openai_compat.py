"""OpenAI-compatible chat-completions driver.

Covers every family OpenRouter serves, Anthropic's included — OpenRouter exposes them all behind
one `/chat/completions` endpoint, so a single driver carries the whole panel. A native Anthropic
driver or a harness driver would each be one more file in this package, not a change here.

Framework §9 driver shape, minus the tool arguments (ensemble-review personas are tool-less):

    dispatch(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn) -> str

`dispatch_detailed` is the same call returning a **result object** rather than a bare string: the
text, the usage, the reasoning tokens, the cost, the model and provider that actually served it, the
effort and the completion cap sent, the finish reason, and a one-entry `attempts` list describing the
call it just made. A bare string cannot carry any of that and the manifest promises all of it.

`attempts` is a list of one because a driver makes one call. The caller — `dispatch.py` — is what
retries: it owns the length retry and the repair re-ask, so it concatenates these one-entry lists
into the seat's `_meta.attempts` and fills in the `validation_errors` the driver cannot know.

The completion cap is `backend_entry["max_tokens"]` and is always explicit. `backend_entry` may also
carry `reasoning_effort` (a string for this one call, or None), `reasoning_max_tokens` (a reasoning-token
budget where the model's file binds an effort level to one rather than to a word, or None) and `prices`
(`{"input": …, "output": …}` per token) so cost is computed when the provider reports none.

Every result and every attempt carries a `cost_source` beside its `cost_usd` — `provider` for the
billed figure, `estimated` for one reconstructed from usage tokens at catalogue prices — and an
`upstream_unbilled_usd` when the provider reports running more inference than it charged for. See
`_cost` and `_upstream_unbilled`.
"""

import json
import time

DEFAULT_MAX_TOKENS = 32000
DEFAULT_TIMEOUT = 600


def _json_mode_rejected(status, body):
    """True when the provider rejected the request specifically because of `response_format`."""
    if status not in (400, 404, 415, 422, 500):
        return False
    low = (body or "").lower()
    return "response_format" in low or "json_object" in low or "json mode" in low


def dispatch_detailed(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn):
    """Send one chat completion. Returns the result object described in the module docstring."""
    base_url = (backend_entry.get("base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("backend entry has no base_url")
    api_key = backend_entry.get("api_key")
    if not api_key:
        raise ValueError("backend entry has no api_key")

    url = base_url + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "HTTP-Referer": backend_entry.get("http_referer", "https://github.com/anthonypero/agent-skills"),
        "X-Title": backend_entry.get("x_title", "ensemble-review"),
    }

    max_tokens = int(backend_entry.get("max_tokens") or DEFAULT_MAX_TOKENS)
    effort = backend_entry.get("reasoning_effort")
    reasoning_budget = backend_entry.get("reasoning_max_tokens")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": backend_entry.get("temperature", 0),
        "usage": {"include": True},
    }
    if effort:
        # Vocabularies differ by vendor, so the caller resolves the word from the model's own file
        # and the registry's vocabulary. A model whose file binds the level to a reasoning-token
        # budget instead sends `max_tokens` here; a caller that resolved neither sends no reasoning
        # parameter at all, which works everywhere at the cost of control.
        payload["reasoning"] = {"effort": effort}
    elif reasoning_budget:
        payload["reasoning"] = {"max_tokens": int(reasoning_budget)}
    routing = backend_entry.get("provider_routing_for_model")
    if routing:
        payload["provider"] = routing
    if json_schema is not None:
        payload["response_format"] = {"type": "json_object"}

    timeout = int(backend_entry.get("timeout") or DEFAULT_TIMEOUT)
    notes = []

    started = time.time()
    try:
        status, body = http_request_fn(url, headers, json.dumps(payload).encode("utf-8"), timeout)
    except _Rejected as exc:
        if json_schema is not None and _json_mode_rejected(exc.status, exc.body):
            notes.append("provider rejected response_format=json_object; retried without json mode")
            payload.pop("response_format", None)
            status, body = http_request_fn(url, headers, json.dumps(payload).encode("utf-8"), timeout)
        else:
            raise
    elapsed = time.time() - started

    data = json.loads(body)
    if data.get("error"):
        err = data["error"]
        raise RuntimeError("provider error: {0}".format(err.get("message") or json.dumps(err)[:400]))

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("provider returned no choices: {0}".format(body[:400]))
    message = choices[0].get("message") or {}
    text = message.get("content")
    if isinstance(text, list):
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    if not (text or "").strip():
        # Some providers return an empty `content` and put everything in `reasoning` — typically when
        # the completion cap was spent on reasoning tokens. Fall back rather than fail on nothing.
        fallback = message.get("reasoning")
        if isinstance(fallback, list):
            fallback = "".join(part.get("text", "") for part in fallback if isinstance(part, dict))
        text = fallback or ""
        if text:
            notes.append("content was empty; fell back to the `reasoning` field")

    finish = choices[0].get("finish_reason")
    if finish and finish not in ("stop", "end_turn"):
        notes.append("finish_reason={0}".format(finish))

    usage = data.get("usage") or {}
    reasoning_tokens = _reasoning_tokens(usage)
    cost_usd, cost_source = _cost(usage, backend_entry.get("prices"))
    upstream_unbilled = _upstream_unbilled(usage, cost_usd)
    if cost_source == "estimated":
        notes.append(
            "the provider reported no cost; ${0:.6f} estimated from {1} prompt and {2} completion "
            "tokens at the registry's catalogue prices".format(
                cost_usd, usage.get("prompt_tokens"), usage.get("completion_tokens")))
    if upstream_unbilled is not None:
        notes.append(
            "the provider billed ${0:.6f} and reported upstream_inference_cost ${1:.6f}; the "
            "difference was not billed".format(cost_usd or 0.0, upstream_unbilled))

    result = {
        "text": text,
        "usage": usage,
        "reasoning_tokens": reasoning_tokens,
        "cost_usd": cost_usd,
        "cost_source": cost_source,
        "upstream_unbilled_usd": upstream_unbilled,
        "model": data.get("model") or model,
        "provider": data.get("provider") or backend_entry.get("provider_name") or "openrouter",
        "connector": backend_entry.get("type", "openai_compat"),
        "effort": effort,
        "effort_tokens": reasoning_budget,
        "max_tokens": max_tokens,
        "finish_reason": finish,
        "notes": notes,
        "response": data,
        "attempts": [{
            "n": 1,
            "max_tokens_sent": max_tokens,
            "finish_reason": finish,
            "validation_errors": None,
            "usage": usage,
            "reasoning_tokens": reasoning_tokens,
            "cost_usd": cost_usd,
            "cost_source": cost_source,
            "upstream_unbilled_usd": upstream_unbilled,
            "elapsed_s": round(elapsed, 2),
            "notes": list(notes),
            "response_id": data.get("id"),
        }],
    }
    return result


def dispatch(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn):
    """Framework §9 driver entry point. Returns the assistant's text."""
    return dispatch_detailed(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn)["text"]


def _reasoning_tokens(usage):
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return None
    value = details.get("reasoning_tokens")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _cost(usage, prices):
    """(cost_usd, source) — what this call cost, and whether that number is billed or reconstructed.

    Two sources, and the order is not a matter of taste:

    - `provider` — the top-level `usage.cost`. **This is the billed figure and it is authoritative,
      including when it is 0.** Settled against the ledger: OpenRouter's `/credits` read
      `total_usage` 7.770718 before the first unattended run and 10.522836 after it, a delta of
      $2.752118 against the manifest's recorded $2.752117. A `finish_reason: error` attempt that
      reported `cost: 0` beside an `upstream_inference_cost` of $1.816461 was **not** billed for it.
      An errored generation costs the account nothing, and a driver that substituted the upstream
      figure would over-report every such call.
    - `estimated` — the registry's catalogue prices against the reported tokens, and only when the
      usage block carries no `cost` key at all. Catalogue prices are not what a routed call pays, so
      it is labelled and `_meta.cost_estimated` flags any total built from one.

    Returns `(None, None)` when there is neither a reported cost nor a price to reconstruct one from:
    unknown, never zero. The upstream figure is not discarded — see `_upstream_unbilled`.
    """
    if "cost" in usage:
        reported = _f(usage.get("cost"))
        if reported is not None:
            return reported, "provider"

    estimated = _estimate_cost(usage, prices)
    if estimated is not None:
        return estimated, "estimated"
    return None, None


def _upstream_unbilled(usage, billed):
    """`cost_details.upstream_inference_cost` when it exceeds what the account was billed, else None.

    The divergence is real and worth keeping — it is how much inference the provider says it ran and
    did not charge for, which on an errored generation is the whole of it. It is deliberately **not**
    added to `cost_usd`: the ledger says the account paid the billed figure, and a total that
    included this would over-report the run by $1.82 on exactly the row it was meant to explain.
    Recorded beside the cost so the gap stays visible without inflating any number that has to
    reconcile against a credit balance.
    """
    details = usage.get("cost_details")
    if not isinstance(details, dict):
        return None
    upstream = _f(details.get("upstream_inference_cost"))
    if upstream is None:
        return None
    if upstream > (billed if billed is not None else 0.0):
        return upstream
    return None


def _estimate_cost(usage, prices):
    """The registry's catalogue prices against the reported tokens. None when either is missing."""
    if not isinstance(prices, dict):
        return None
    input_price = _f(prices.get("input"))
    output_price = _f(prices.get("output"))
    if input_price is None or output_price is None:
        return None
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    if not prompt_tokens and not completion_tokens:
        return None
    return input_price * prompt_tokens + output_price * completion_tokens


def _f(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class _Rejected(Exception):
    """Raised by the caller's http_request_fn on a non-2xx response, so the driver can inspect it."""

    def __init__(self, status, body):
        super(_Rejected, self).__init__("HTTP {0}: {1}".format(status, (body or "")[:400]))
        self.status = status
        self.body = body or ""


Rejected = _Rejected

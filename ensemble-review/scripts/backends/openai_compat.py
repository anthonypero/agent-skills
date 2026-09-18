"""OpenAI-compatible chat-completions driver.

Covers every family OpenRouter serves, Anthropic's included — OpenRouter exposes them all behind
one `/chat/completions` endpoint, so a single driver carries the whole panel. A native Anthropic
driver or a harness driver would each be one more file in this package, not a change here.

Framework §9 driver shape, minus the tool arguments (ensemble-review personas are tool-less):

    dispatch(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn) -> str

`dispatch_detailed` is the same call returning the decoded response alongside the text, so the
caller can record usage and cost. `dispatch` is the framework-shaped wrapper over it.
"""

import json

DEFAULT_MAX_TOKENS = 20000
DEFAULT_TIMEOUT = 600


def _json_mode_rejected(status, body):
    """True when the provider rejected the request specifically because of `response_format`."""
    if status not in (400, 404, 415, 422, 500):
        return False
    low = (body or "").lower()
    return "response_format" in low or "json_object" in low or "json mode" in low


def dispatch_detailed(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn):
    """Send one chat completion. Returns (text, response_dict, notes) where notes lists fallbacks taken."""
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

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": int(backend_entry.get("max_tokens") or DEFAULT_MAX_TOKENS),
        "temperature": backend_entry.get("temperature", 0),
        "usage": {"include": True},
    }
    if json_schema is not None:
        payload["response_format"] = {"type": "json_object"}

    timeout = int(backend_entry.get("timeout") or DEFAULT_TIMEOUT)
    notes = []

    try:
        status, body = http_request_fn(url, headers, json.dumps(payload).encode("utf-8"), timeout)
    except _Rejected as exc:
        if json_schema is not None and _json_mode_rejected(exc.status, exc.body):
            notes.append("provider rejected response_format=json_object; retried without json mode")
            payload.pop("response_format", None)
            status, body = http_request_fn(url, headers, json.dumps(payload).encode("utf-8"), timeout)
        else:
            raise

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
    return text, data, notes


def dispatch(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn):
    """Framework §9 driver entry point. Returns the assistant's text."""
    text, _data, _notes = dispatch_detailed(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn)
    return text


class _Rejected(Exception):
    """Raised by the caller's http_request_fn on a non-2xx response, so the driver can inspect it."""

    def __init__(self, status, body):
        super(_Rejected, self).__init__("HTTP {0}: {1}".format(status, (body or "")[:400]))
        self.status = status
        self.body = body or ""


Rejected = _Rejected

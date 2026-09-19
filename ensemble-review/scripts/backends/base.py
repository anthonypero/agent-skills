"""The connector contract, in one place: what a driver must export and what it must return.

**This module is documentation with a runnable shape, not a base class to inherit from.** Drivers
are loaded by module — `backends/<type>.py` for the packaged ones, an absolute path for one a
project drops under `<workspace>/.agents/ensemble-review/backends/` — and `load_driver` looks for
two module-level functions and nothing else. There is no registration step and no class to subclass,
because the seam framework §9 defines is a function signature and adding an inheritance requirement
on top of it would make a driver harder to write than the thing it wraps.

What it is for: a second builder writing the `azure_openai` driver that routes a confidential review
to a host with a data agreement needs to know exactly what to return, and reading
`openai_compat.py` to find out means reading 200 lines of OpenRouter specifics to recover 20 lines
of contract. `check_driver()` at the bottom is that contract as an assertion.

## The two entry points

```python
def dispatch(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn) -> str
def dispatch_detailed(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn) -> dict
```

`dispatch()` is framework §9's own signature minus the tool arguments, because these personas are
single-turn and tool-less, and it keeps §9's string return so the seam stays framework-shaped.
`dispatch_detailed()` makes the same call and returns the result object below, and it is the one
this skill actually calls: a bare string cannot carry usage, cost, the provider that served the
call or the finish reason, and the manifest promises all of them. Ship both; `dispatch()` returning
`dispatch_detailed(...)["text"]` is a correct implementation of it.

## The arguments

| Argument | What it is |
| --- | --- |
| `system_prompt` | The persona body plus its `context` references, already composed. **Byte-identical across families** — that is the invariant the comparison rests on, so a driver never edits it |
| `user_prompt` | The artifact and references inlined, or the judgment call's own message |
| `model` | The concrete model id, already resolved from the tier map. A driver never consults the tier map |
| `backend_entry` | The connector's config entry, with four things the caller added: `api_key`, `max_tokens`, `reasoning_effort` (or `None`), and `prices` as `{input, output}` per token. `provider_routing_for_model` is present when the config carries routing for this model |
| `json_schema` | `{"type": "json_object"}` — the structured-output request. A provider that rejects it must fall back to plain completion rather than failing the seat |
| `http_request_fn` | `(url, headers, body_bytes, timeout) -> (status, text)`. **Use it; do not open your own socket.** Framework §11's retry policy lives inside it — three tries at 1 s, 4 s and 16 s on a 429, a 5xx or a socket timeout — and a 401 or 403 is raised as `AuthFailure` so the caller can halt the run instead of paying three more times to prove the key is still wrong |

## The result object

| Field | Meaning |
| --- | --- |
| `text` | The model's content, **falling back to the `reasoning` field when `content` is empty** — DeepSeek returns everything there, and a driver that does not look is a seat that returned nothing |
| `usage` | `{prompt_tokens, completion_tokens, total_tokens}` |
| `reasoning_tokens` | From the provider's own count when it reports one; `None` when it does not. Never zero as a stand-in for unknown |
| `cost_usd` | The provider's reported cost when it gives one, else computed from `backend_entry["prices"]` |
| `model` | The id actually served, which is not always the id asked for |
| `provider` | The upstream host the request was routed to — the audit trail for where the bytes went |
| `connector` | The driver type that made the call |
| `effort` | The effort parameter actually sent, or `None` |
| `max_tokens` | The completion cap actually sent |
| `finish_reason` | `stop`, `length`, … **`length` is not a reviewer error**: the caller retries once at double the cap with a fresh prompt before it ever reaches the repair path, so report it accurately or that retry never fires |
| `attempts` | A one-entry list for the call this driver made: `{max_tokens_sent, finish_reason, usage, reasoning_tokens, cost_usd, elapsed_s, notes, response_id}`. The caller renumbers it, fills in the validation verdict and concatenates the seat's history — a driver makes one call and cannot know whether what it returned validated |
| `notes` | Free strings about how the call was made — a fallback out of JSON mode, a retried body shape |

## Errors

Raise `backends.AuthFailure` for a 401 or a 403: framework §20 names auth failures non-transient,
and the caller halts the whole run on it rather than flagging one seat. Everything else raises
whatever the driver likes — the caller treats it as a failed seat and advances — except that a 400
or 404 whose body names the model is how a family is found to be unreachable, so let that body
reach the exception's `body` attribute or its `str()`.

A driver exports a `Rejected(status, body)` exception carrying both, which is what makes that check
possible from the outside; `openai_compat.Rejected` is the shipped one.
"""

REQUIRED_FUNCTIONS = ("dispatch", "dispatch_detailed")

RESULT_FIELDS = (
    "text", "usage", "reasoning_tokens", "cost_usd", "model", "provider", "connector",
    "effort", "max_tokens", "finish_reason", "attempts",
)

ATTEMPT_FIELDS = ("max_tokens_sent", "finish_reason", "usage", "cost_usd")


def check_driver(module):
    """Every way `module` fails the contract above, as messages. Empty means it satisfies it.

    Structural only — it says whether a driver exports the right shape, not whether it talks to its
    provider correctly. Useful from a test, and from a project checking its own workspace driver
    before a panel spends money discovering the same thing.
    """
    problems = []
    for name in REQUIRED_FUNCTIONS:
        if not callable(getattr(module, name, None)):
            problems.append("no module-level `{0}()`".format(name))
    if not isinstance(getattr(module, "Rejected", None), type):
        problems.append("no `Rejected` exception carrying the provider's status and body")
    return problems


def check_result(result):
    """Every way one `dispatch_detailed()` return fails the contract, as messages."""
    problems = []
    if not isinstance(result, dict):
        return ["the result is a {0}, expected a dict".format(type(result).__name__)]
    for field in RESULT_FIELDS:
        if field not in result:
            problems.append("the result has no `{0}`".format(field))
    attempts = result.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        problems.append("`attempts` must be a list holding one entry for the call that was made")
    elif not isinstance(attempts[0], dict):
        problems.append("`attempts[0]` must be an object")
    else:
        for field in ATTEMPT_FIELDS:
            if field not in attempts[0]:
                problems.append("`attempts[0]` has no `{0}`".format(field))
    return problems

"""The harness connector's driver, and it makes no call of any kind.

A seat bound to a `type: harness` connector is **not dispatched by this package**. `run_panel.py`
marks every such seat as a harness seat at Resolve — the same state `--skip-claude` produces for a
`claude` seat — prints the spawn block the orchestrating session follows, and stops. The session
spawns the subagent, `render_harness_report.py` validates what comes back, and the report is moved
into the run directory. Nothing in that path goes through a driver.

So why does this file exist at all? Because `lib/paths.py` resolves a connector's `type` to a driver
**at Resolve**, before anything has decided which leg a seat sits on, and a connector whose `type`
answers to nothing is a composition error. This module is what that resolution finds, and it is
written as a **refusal rather than an implementation**: if the dispatch path is ever reached with a
harness seat — a future edit that stops marking these seats, a driver `type` copied onto a metered
endpoint's connector file by mistake — the run stops here with a message that says what went wrong,
instead of the one thing the draft pass promises can never happen, which is a call that costs money
or leaves the machine.

It exports the two functions `backends/base.py` names, so the contract is satisfied and the failure
is the same whichever entry point reaches it.
"""


class HarnessLegError(Exception):
    """A harness seat reached the dispatch path. It never should; nothing here can serve it."""


MESSAGE = (
    "the `harness` connector has no dispatch path: a seat bound to it is spawned by the "
    "orchestrating session as a harness subagent, not called by this package.\n"
    "  `run_panel.py` marks every seat on a `type: harness` connector as a harness seat at Resolve "
    "and prints the spawn block for it; reaching a driver means that marking did not happen.\n"
    "  Nothing was sent anywhere. No network call is made from this module under any argument."
)


def dispatch(system_prompt, user_prompt, model, backend_entry, json_schema=None,
             http_request_fn=None):
    """Framework §9's entry point. Always raises: there is nothing here to call."""
    raise HarnessLegError(MESSAGE)


def dispatch_detailed(*args, **kwargs):
    """The result-object entry point this skill actually calls. Always raises, for the same reason."""
    raise HarnessLegError(MESSAGE)

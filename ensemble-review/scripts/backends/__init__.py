"""Backend driver loader.

One driver per access path. `dispatch.py` never contains backend-specific logic: it reads the
provider entry's `type` field and asks for the matching module here. Adding an access path is
adding one file in this package — a native `anthropic` driver, a `harness` driver that shells out
to the local agent CLI, a `claude_cli` driver — with no change to the caller.

A `type` that names a **file** rather than a package module is loaded from that file. This is how a
project binds a confidential review to a connector that carries a data agreement without editing the
read-only skill package: it drops `.config/ensemble-review/backends/azure_openai.py` beside its
config fragment and points `type` at it. Anything that is not an existing `.py` path is treated as a
package module name, so the shipped `"type": "openai_compat"` is unaffected.

**`lib/paths.py` is what turns a bare `type` into one or the other**, and the callers go through it:
`Paths.driver_ref()` searches the workspace root before the package, hands back an absolute path for
a workspace driver and the bare type name for the packaged one, and raises `PathError` — the
caller's exit 1 — for a `type` that resolves nowhere. Nothing in this file has to be edited to add a
driver at either root.

Every driver exports two functions. The framework §9 entry point, minus the tool arguments, because
ensemble-review's personas are single-turn and tool-less:

    dispatch(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn) -> str

and the one this skill actually calls, which returns the result object the manifest is built from:

    dispatch_detailed(...) -> {text, usage, reasoning_tokens, cost_usd, model, provider, connector,
                               effort, max_tokens, finish_reason, attempts, ...}
"""

import importlib
import importlib.util
import os
import sys


class AuthFailure(Exception):
    """A 401 or 403 from a provider. Framework §20 names auth failures non-transient.

    It lives here rather than in `dispatch.py` so every driver — including one a project drops into
    its own workspace — can raise the exception the caller halts the run on, instead of having its
    auth failure counted as one more transient error and retried three times.
    """


def load_driver(backend_type):
    """Return the driver module for a provider `type`, or exit with a clear message."""
    name = str(backend_type)
    if name.endswith(".py") or os.sep in name:
        return _load_from_file(name)
    module_name = name.replace("-", "_")
    try:
        return importlib.import_module("backends." + module_name)
    except ImportError:
        sys.exit("Unknown backend type '{0}'. No driver found at backends/{1}.py".format(backend_type, module_name))


def _load_from_file(path):
    absolute = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(absolute):
        sys.exit("Backend driver '{0}' is a path and there is no file at {1}".format(path, absolute))
    module_name = "ensemble_review_backend_" + os.path.basename(absolute)[:-3].replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, absolute)
    if spec is None or spec.loader is None:
        sys.exit("Backend driver at {0} could not be loaded".format(absolute))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

"""Backend driver loader.

One driver per access path. `dispatch.py` never contains backend-specific logic: it reads the
provider entry's `type` field and asks for the matching module here. Adding an access path is
adding one file in this package — a native `anthropic` driver, a `harness` driver that shells out
to the local agent CLI, a `claude_cli` driver — with no change to the caller.

Every driver exports one function with the framework §9 signature, minus the tool arguments,
because ensemble-review's personas are single-turn and tool-less:

    dispatch(system_prompt, user_prompt, model, backend_entry, json_schema, http_request_fn) -> str
"""

import importlib
import sys


def load_driver(backend_type):
    """Return the driver module for a provider `type`, or exit with a clear message."""
    module_name = str(backend_type).replace("-", "_")
    try:
        return importlib.import_module("backends." + module_name)
    except ImportError:
        sys.exit("Unknown backend type '{0}'. No driver found at backends/{1}.py".format(backend_type, module_name))

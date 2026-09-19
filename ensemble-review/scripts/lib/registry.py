"""The model registry: prices, context limits, effort vocabularies and output-token priors.

`templates/models.json` is the price and capability cache the cost pre-flight is unimplementable
without. It is seeded at build time, refreshed from the OpenRouter catalogue by `refresh_models.py`,
and read here by every script that needs to know what a model costs or how much room it has.

Two rules the spec makes load-bearing and this module enforces:

- **A resolved model absent from the registry is a composition error**, never a silent zero in the
  projection and never a live fetch inside the budget gate. `require()` raises `MissingModel`, which
  the caller turns into exit 1 naming the model and the fix.
- **The output-token prior is a prior.** It is seeded from measured runs, labelled as an estimate
  everywhere it is used, and `refresh_models.py` never touches it: the catalogue knows what a token
  costs and only a run knows how many a lens spends.

`min_max_tokens` is the completion floor: a model that reliably needs more room than the run's cap
raises the cap for its own seat. The cap actually sent is what is recorded, per attempt.
"""

import json
import os

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_REGISTRY = os.path.join(SKILL_DIR, "templates", "models.json")

# The completion cap every call carries when the run does not say otherwise. Reasoning tokens bill
# against it on most providers, so it is a cost knob and a truncation knob at once.
DEFAULT_MAX_TOKENS = 32000

# The length retry doubles the cap once. Named here so the retry, the manifest and the docs agree.
LENGTH_RETRY_MULTIPLIER = 2

SCHEMA_VERSION = "1"


class MissingModel(Exception):
    """A resolved model the registry does not cover, or covers without a price. The caller's exit 1.

    Presence is not coverage. An entry whose `input_price_per_token` or `output_price_per_token` is
    null cannot be projected, and a seat left out of the total is a budget gate that does not gate:
    the run would pass a $5 budget on a projection that priced three of its four seats. Both cases
    are the same composition error and carry the same fix.
    """

    def __init__(self, model, path, reason="is not in the registry"):
        super(MissingModel, self).__init__(
            "model {0!r} {2} at {1}.\n"
            "  Every resolved seat must be priced before dispatch: the cost pre-flight cannot\n"
            "  project a model it has no price for, and a silent zero is how a run overspends.\n"
            "  Fix: python3 scripts/refresh_models.py --add {0}".format(model, path, reason))
        self.model = model
        self.path = path
        self.reason = reason


class RegistryError(Exception):
    """The registry file itself is missing or unreadable."""


class Registry(object):
    """A loaded `models.json`. Read-only for every caller but `refresh_models.py`."""

    def __init__(self, data, path):
        self.data = data
        self.path = path
        self.models = data.get("models") or {}

    def get(self, model):
        entry = self.models.get(model)
        return dict(entry) if isinstance(entry, dict) else None

    def require(self, model):
        entry = self.get(model)
        if entry is None:
            raise MissingModel(model, self.path)
        why = unpriced_reason(entry)
        if why:
            raise MissingModel(model, self.path, why)
        return entry

    def covers(self, models):
        """Every model in `models` the registry cannot price, as `(model, reason)`, in the order given.

        One list for both failures — absent, and present but unpriced — because they are one
        composition error with one fix.
        """
        gaps = []
        for model in models:
            if not model:
                continue
            entry = self.models.get(model)
            if entry is None:
                gaps.append((model, "is not in the registry"))
                continue
            why = unpriced_reason(entry)
            if why:
                gaps.append((model, why))
        return gaps

    def refreshed_at(self):
        return self.data.get("refreshed_at")


def unpriced_reason(entry):
    """Why this entry cannot be projected, or None when it can."""
    input_price, output_price = prices(entry)
    missing = [name for name, value in (("input", input_price), ("output", output_price)) if value is None]
    if not missing:
        return None
    return "carries no {0} price".format(" or ".join(missing))


def load(path=None):
    path = path or DEFAULT_REGISTRY
    if not os.path.isfile(path):
        raise RegistryError(
            "no model registry at {0}. The registry ships seeded and `install.sh` refreshes it; "
            "run `python3 scripts/refresh_models.py` to build one.".format(path))
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
        raise RegistryError("{0} is not a model registry: no `models` object".format(path))
    return Registry(data, path)


def cap_for(entry, run_cap):
    """The completion cap this model is actually sent: the run's cap, raised by the model's floor.

    The floor never lowers a cap. A run that asks for 64000 against a model whose floor is 32000
    sends 64000; a run that asks for 32000 against a model whose floor is 64000 sends 64000.
    """
    floor = (entry or {}).get("min_max_tokens")
    try:
        floor = int(floor) if floor is not None else 0
    except (TypeError, ValueError):
        floor = 0
    return max(int(run_cap), floor)


def prior_tokens(entry):
    """The model's output-token prior, or None when the registry has no estimate for it."""
    value = (entry or {}).get("output_token_prior")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def context_limit(entry):
    value = (entry or {}).get("context_limit")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def prices(entry):
    """(input price per token, output price per token). Either may be None when unknown."""
    entry = entry or {}
    return _float_or_none(entry.get("input_price_per_token")), _float_or_none(entry.get("output_price_per_token"))


def effort_is_supported(entry, effort):
    """Whether the config's effort string is in this model's vocabulary.

    None when the registry has no vocabulary for the model — unknown is not the same as unsupported,
    and refusing to send an effort the registry simply has not learned about would be worse than
    sending it.
    """
    vocabulary = (entry or {}).get("effort_vocabulary")
    if not isinstance(vocabulary, list) or not vocabulary:
        return None
    return effort in vocabulary


def _float_or_none(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

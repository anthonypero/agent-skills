#!/usr/bin/env python3
"""Pull the OpenRouter catalogue, diff it against `templates/models.json`, report what moved.

    refresh_models.py                      # refresh every model already in the registry
    refresh_models.py --add z-ai/glm-5.4   # add one model from the catalogue
    refresh_models.py --dry-run            # print the diff, write nothing

`install.sh` calls this so the registry is populated before the first run rather than on some
unstated day. Re-running it is idempotent: a refresh that changes nothing writes nothing and says so.

**It never touches `output_token_prior`.** The catalogue knows what a token costs; only a run knows
how many tokens a lens spends. Priors are updated from run manifests, by hand, and that separation is
deliberate — a catalogue refresh that silently reset the priors would reset the budget projection
with them.

Offline, or any network failure: exit 1 with the reason, and the registry file is left untouched.
The catalogue endpoint is public and needs no key; the key is sent when one resolves, because a
keyed request is the one OpenRouter rate-limits per account rather than per IP.
"""

import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)
sys.path.insert(0, SCRIPTS_DIR)

from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402

CATALOGUE_URL = "https://openrouter.ai/api/v1/models"
TIMEOUT = 60

# What a refresh is allowed to overwrite. `output_token_prior`, `prior_source`, `min_max_tokens`,
# `family`, `measured_output_price` and `measured_output_price_source` are deliberately absent:
# they are measurements and operator decisions, not catalogue facts. `measured_output_price` in
# particular is what a frozen run actually paid per output token, which is what makes the gap
# between the catalogue's price and a routed call's price visible at all, and `family` is this
# skill's own vocabulary rather than the catalogue's author slug.
REFRESHED_FIELDS = ("input_price_per_token", "output_price_per_token", "context_limit", "effort_vocabulary")


def fetch_catalogue(url=CATALOGUE_URL, opener=None):
    """The catalogue as {model id: entry}. Raises RuntimeError with a readable reason on any failure."""
    headers = {"Accept": "application/json", "User-Agent": "ensemble-review/refresh_models"}
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key and api_key.strip():
        headers["Authorization"] = "Bearer " + api_key.strip()
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with (opener or urllib.request.urlopen)(request, timeout=TIMEOUT) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("the catalogue at {0} answered HTTP {1}; the registry was not touched".format(url, exc.code))
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("could not reach the catalogue at {0}: {1}; the registry was not touched".format(url, exc))
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise RuntimeError("the catalogue at {0} did not return JSON: {1}".format(url, exc))
    models = data.get("data")
    if not isinstance(models, list) or not models:
        raise RuntimeError("the catalogue at {0} returned no models".format(url))
    return {m.get("id"): m for m in models if isinstance(m, dict) and m.get("id")}


def catalogue_fields(entry):
    """The four refreshable fields, read out of one catalogue record."""
    pricing = entry.get("pricing") or {}
    reasoning = entry.get("reasoning") or {}
    efforts = reasoning.get("supported_efforts")
    return {
        "input_price_per_token": _price(pricing.get("prompt")),
        "output_price_per_token": _price(pricing.get("completion")),
        "context_limit": _int(entry.get("context_length")),
        "effort_vocabulary": list(efforts) if isinstance(efforts, list) and efforts else None,
    }


def refresh(registry_data, catalogue, add=(), now=None):
    """Apply the catalogue to the registry in place. Returns (changes, unknown, added)."""
    now = now or datetime.date.today().isoformat()
    models = registry_data.setdefault("models", {})

    added = []
    for model in add:
        if model in models:
            continue
        if model not in catalogue:
            raise RuntimeError("the catalogue has no model {0!r}; check the id against {1}".format(model, CATALOGUE_URL))
        models[model] = {
            "input_price_per_token": None,
            "output_price_per_token": None,
            # Which family this model belongs to. Not a catalogue fact — the catalogue knows the
            # author slug and this skill's families are its own vocabulary — so it is left null for
            # the operator to fill, and a refresh never touches it. `seating.family_for_model`
            # falls back to a reverse lookup over the config's tier map when it is null, which is
            # what answers for every model a shipped config actually seats.
            "family": None,
            "context_limit": None,
            "effort_vocabulary": None,
            "output_token_prior": DEFAULT_PRIOR,
            "prior_source": "default — added by refresh_models.py, no measured run",
            "min_max_tokens": None,
            "measured_output_price": None,
            "measured_output_price_source": "no frozen run dispatched this model",
            "price_source": "openrouter catalogue",
            "source": CATALOGUE_URL,
            "refreshed_at": now,
        }
        added.append(model)

    changes = []
    unknown = []
    for model in sorted(models):
        entry = models[model]
        record = catalogue.get(model)
        if record is None:
            unknown.append(model)
            continue
        fields = catalogue_fields(record)
        moved = False
        for field in REFRESHED_FIELDS:
            before = entry.get(field)
            after = fields[field]
            if after is None:
                continue
            if before != after:
                changes.append({"model": model, "field": field, "before": before, "after": after})
                entry[field] = after
                moved = True
        if moved:
            entry["price_source"] = "openrouter catalogue"
            entry["source"] = CATALOGUE_URL
            entry["refreshed_at"] = now
    if changes or added:
        registry_data["source"] = CATALOGUE_URL
        registry_data["refreshed_at"] = now
    return changes, unknown, added


DEFAULT_PRIOR = 16000


def _price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Refresh the model registry from the OpenRouter catalogue.")
    parser.add_argument("--registry", default=None, help="Registry file (default: the workspace-first cascade, so a project's own models.json is the one refreshed)")
    parser.add_argument("--workspace", default=None, help="The project whose registry to refresh; its .agents/ensemble-review/models.json wins over the packaged one (default: the working directory)")
    parser.add_argument("--add", action="append", default=[], help="Add a model id from the catalogue; repeat for several")
    parser.add_argument("--url", default=CATALOGUE_URL, help="Catalogue URL")
    parser.add_argument("--dry-run", action="store_true", help="Print the diff and write nothing")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    try:
        registry_path = paths_lib.Paths(args.workspace).registry(args.registry)
    except paths_lib.PathError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return 1
    if not os.path.isfile(registry_path):
        sys.stderr.write("no registry at {0}\n".format(registry_path))
        return 1
    with open(registry_path, "r", encoding="utf-8") as handle:
        registry_data = json.load(handle)

    try:
        catalogue = fetch_catalogue(args.url)
    except RuntimeError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return 1

    before = json.dumps(registry_data, sort_keys=True)
    try:
        changes, unknown, added = refresh(registry_data, catalogue, args.add)
    except RuntimeError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return 1

    print("catalogue: {0} model(s) from {1}".format(len(catalogue), args.url))
    for model in added:
        print("  added    {0}".format(model))
    for change in changes:
        print("  {0:<28} {1:<24} {2} -> {3}".format(change["model"], change["field"], _show(change["before"]), _show(change["after"])))
    if not changes and not added:
        print("  nothing moved; the registry is current")
    for model in unknown:
        print("  NOT IN CATALOGUE: {0} — kept as it stands".format(model))

    if args.dry_run:
        print("--dry-run: nothing written")
        return 0
    if json.dumps(registry_data, sort_keys=True) == before:
        return 0
    report_lib.write_json(registry_path, registry_data)
    print("wrote {0}".format(registry_path))
    return 0


def _show(value):
    if isinstance(value, list):
        return "/".join(value)
    return "null" if value is None else str(value)


if __name__ == "__main__":
    sys.exit(main())

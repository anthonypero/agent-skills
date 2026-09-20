#!/usr/bin/env python3
"""Pull the connector's model catalogue, diff it against the model files, report what moved.

    refresh_models.py                      # refresh every model the registry already holds
    refresh_models.py --add z-ai/glm-5.4   # add one model from the catalogue
    refresh_models.py --dry-run            # print the diff, write nothing

`install.sh` calls this so the registry is populated before the first run rather than on some
unstated day. Re-running it is idempotent: a refresh that changes nothing writes nothing and says so.

**It writes facts and never choices.** The catalogue owns prices, context limits and effort
vocabularies; a person owns the connector a model is served by, its family, the tiers it plays, its
abstract-effort map, its output-token prior, its completion floor and its measured prices. The two
halves live in one file per model and this script touches exactly one of them. A catalogue refresh
that silently reset the priors would reset the budget projection with them, and one that reset an
effort map would move every seat's depth without anybody choosing it.

**Where it writes, and why that is an exception.** Framework principle 12 says the package is
read-only during a run; registry maintenance is the spec's one explicit exception to it. A refresh
writes each model's facts back into **the outermost root that already holds a file for that model**
— the project's, else this machine's, else the package's. So a project that has taken a copy of one
model file keeps getting price refreshes into its own copy, and an installation that must be strictly
immutable puts a model file under `<project>/.agents/ensemble-review/models/` or
`~/.config/ensemble-review/models/` first, after which nothing writes into the package.

**And it writes a fragment as a fragment.** An outer model file is usually two or three keys, not a
copy of the package's; what goes back into it is the keys it already declared plus the facts that
actually moved for that model. Writing the merged entry would turn the fragment into a full copy on
its first refresh, freezing every fact it absorbed — the deep-merge promise is that a per-owner
choice keeps taking the package's price refreshes, and a whole-file write is exactly what stops it.

`--add` seeds a new model file with the catalogue's facts and **null choices**, because the
catalogue cannot know which family this skill calls a model, which tiers it should play, or what
`deep` ought to mean on its ladder. The file lands in the outermost root that already holds a model
directory, so `--add` on a project with its own registry does not write into the package either.

Offline, or any network failure: exit 1 with the reason, and every model file is left untouched.
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

from lib import connectors as connectors_lib  # noqa: E402
from lib import paths as paths_lib  # noqa: E402
from lib import registry as registry_lib  # noqa: E402
from lib import report as report_lib  # noqa: E402

CATALOGUE_URL = "https://openrouter.ai/api/v1/models"
TIMEOUT = 60

# What a refresh is allowed to overwrite. Everything in `registry.CHOICE_FIELDS` is deliberately
# absent: those are measurements and operator decisions, not catalogue facts. `measured_output_price`
# in particular is what a frozen run actually paid per output token, which is what makes the gap
# between the catalogue's price and a routed call's price visible at all, and `family` is this
# skill's own vocabulary rather than the catalogue's author slug.
REFRESHED_FIELDS = registry_lib.FACT_FIELDS

# Stamped beside the facts whenever one of them moves, and written into the same file they go into:
# where the number came from and when. `refresh` sets all three on an entry it changed.
PROVENANCE_FIELDS = ("price_source", "source", "refreshed_at")

DEFAULT_PRIOR = 16000


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


def seed_entry(model, now, connector=None, url=CATALOGUE_URL):
    """A new model file: the catalogue's facts land on the next pass, every choice starts null.

    `connector` is the one choice that can be guessed, because `--add` was run against exactly one
    endpoint's catalogue and that is the endpoint the model was found on. Everything else — family,
    tiers, the effort map — is a decision about how this skill uses the model and the catalogue has
    no opinion to offer.
    """
    return {
        "id": model,
        "connector": connector,
        "family": None,
        "tiers": None,
        "effort": None,
        "effort_source": None,
        "input_price_per_token": None,
        "output_price_per_token": None,
        "context_limit": None,
        "effort_vocabulary": None,
        "output_token_prior": DEFAULT_PRIOR,
        "prior_source": "default — added by refresh_models.py, no measured run",
        "min_max_tokens": None,
        "measured_output_price": None,
        "measured_output_price_source": "no frozen run dispatched this model",
        "price_source": "openrouter catalogue",
        "source": url,
        "refreshed_at": now,
    }


def foreign_models(models, connector):
    """Model ids this connector's catalogue has no business refreshing, in sorted order.

    A model file names the connector that serves it. A refresh runs against **one** endpoint's
    catalogue, so a model bound to a different endpoint is not "missing from the catalogue" — it was
    never that catalogue's to answer for, and reporting it as unknown would teach the operator to
    ignore the line that means something. The harness leg is the live case: `claude-opus-5` is
    served by a spawn, has no catalogue entry anywhere, and its zero price is a fact about the
    subscription rather than a price the catalogue could refresh.

    A model file naming no connector at all is still refreshed, so a registry written before the
    field existed behaves exactly as it did.
    """
    if not connector:
        return []
    return sorted(model for model, entry in models.items()
                  if (entry or {}).get("connector") and entry["connector"] != connector)


def refresh(models, catalogue, add=(), now=None, connector=None, url=CATALOGUE_URL, skip=()):
    """Apply the catalogue to `{model id: entry}` in place. Returns (changes, unknown, added).

    `skip` names the models this catalogue does not answer for — see `foreign_models`. They are left
    exactly as they stand and are not reported as unknown.
    """
    now = now or datetime.date.today().isoformat()
    skip = set(skip or ())

    added = []
    for model in add:
        if model in models:
            continue
        if model not in catalogue:
            raise RuntimeError("the catalogue has no model {0!r}; check the id against {1}".format(model, url))
        models[model] = seed_entry(model, now, connector=connector, url=url)
        added.append(model)

    changes = []
    unknown = []
    for model in sorted(models):
        if model in skip:
            continue
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
            entry["source"] = url
            entry["refreshed_at"] = now
    return changes, unknown, added


def write_back(models, files, target_dir, changes=(), dry_run=False):
    """Write each model's moved facts back into the file that holds it; a new model lands in `target_dir`.

    **A fragment stays a fragment.** `models` holds the *merged* entries — the package's facts with
    an outer root's choices on top — and `files` points at the **outermost** layer, which may be a
    two-key user file saying only which rung `standard` is on one model. Writing the merged entry
    into that file would turn it into a full copy of the package's, and the deep-merge promise dies
    with it: the copy freezes every fact it just absorbed, so the next price refresh writes into the
    outer file and the package's base layer is never read for that model again. So what is written
    is **the keys that file already declared, plus the facts that actually moved for that model**,
    with the provenance the refresh stamps beside them.

    `changes` is `refresh`'s own change list, which is what says a fact moved. A model with nothing
    in it is not rewritten at all — that is the no-op guarantee, and it now holds for a fragment as
    well as for a full file, because nothing serializes a fragment back into different bytes.
    """
    moved = {}
    for change in changes or ():
        moved.setdefault(change["model"], set()).add(change["field"])

    written = []
    for model, entry in models.items():
        path = files.get(model) or os.path.join(target_dir, paths_lib.model_slug(model) + ".json")
        fields = moved.get(model) or set()
        if os.path.isfile(path):
            if not fields:
                continue
            document = _refreshed_document(path, entry, fields)
        else:
            # A model `--add` seeded: there is no file yet, so the seeded entry *is* the file.
            document = entry
        before = None
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                before = handle.read()
        after = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        if before == after:
            continue
        written.append(path)
        if not dry_run:
            report_lib.write_json(path, document)
    return written


def _refreshed_document(path, entry, fields):
    """One model file's own keys with the moved facts written over them, in the file's own order.

    A fact the file already declares is updated in place; one it does not is appended, in
    `FACT_FIELDS` order so two refreshes of the same file agree. The provenance trio goes with them
    — a price sitting in a file with no `source` or `refreshed_at` beside it is a number nobody can
    date, and `Registry.refreshed_at()` reads the stalest stamp across the entries it merged.
    """
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        # Not a model file at all; `read_model_file` will have raised long before this. Rewriting it
        # whole is the only thing left that could be meant.
        return entry
    for field in REFRESHED_FIELDS:
        if field in fields:
            document[field] = entry.get(field)
    for field in PROVENANCE_FIELDS:
        document[field] = entry.get(field)
    return document


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Refresh the model registry from the connector's catalogue.")
    parser.add_argument("--models", default=None, help="Directory of model files, taken as given (default: the workspace-first cascade, so a project's own models/ is the one refreshed)")
    parser.add_argument("--workspace", default=None, help="The project whose registry to refresh; its .agents/ensemble-review/models/ wins over this machine's and over the packaged one (default: the working directory)")
    parser.add_argument("--add", action="append", default=[], help="Add a model id from the catalogue; repeat for several")
    parser.add_argument("--connector", default=None, help="Which connector file supplies the catalogue URL (default: the config's default_connector)")
    parser.add_argument("--url", default=None, help="Catalogue URL, overriding the connector's")
    parser.add_argument("--config", default=None, help="Config JSON, taken as given (default: the workspace-first cascade, deep-merged)")
    parser.add_argument("--dry-run", action="store_true", help="Print the diff and write nothing")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    paths = paths_lib.Paths(args.workspace)

    connector = None
    url = args.url
    if not url:
        try:
            config, _config_path = paths.config(args.config)
            connector, _path = connectors_lib.load(
                paths, args.connector or config.get("default_connector") or connectors_lib.DEFAULT_CONNECTOR)
            url = connectors_lib.catalogue_url(connector) or CATALOGUE_URL
        except (paths_lib.PathError, connectors_lib.ConnectorError) as failure:
            sys.stderr.write("{0}\n".format(failure))
            return 1

    try:
        registry = registry_lib.load(paths, args.models)
    except (paths_lib.PathError, registry_lib.RegistryError) as failure:
        sys.stderr.write("{0}\n".format(failure))
        return 1

    # Where a **new** model file lands: the outermost root that already keeps model files, which is
    # the same root a refresh of an existing model would write into. Adding a model to the package
    # from a project that keeps its own registry would put it somewhere the project does not read.
    directories = paths.model_dirs(args.models)
    target_dir = directories[-1][1] if directories else registry_lib.DEFAULT_MODEL_DIR

    try:
        catalogue = fetch_catalogue(url)
    except RuntimeError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return 1

    models = dict(registry.models)
    connector_name = (connector or {}).get("name")
    foreign = foreign_models(models, connector_name)
    try:
        changes, unknown, added = refresh(
            models, catalogue, args.add, connector=connector_name, url=url, skip=foreign)
    except RuntimeError as failure:
        sys.stderr.write("{0}\n".format(failure))
        return 1

    print("catalogue: {0} model(s) from {1}".format(len(catalogue), url))
    for model in foreign:
        print("  other connector: {0} is served by {1!r}, not by this catalogue — untouched".format(
            model, models[model].get("connector")))
    for model in added:
        print("  added    {0}  (facts from the catalogue; family, tiers and effort left null for you)".format(model))
    for change in changes:
        print("  {0:<28} {1:<24} {2} -> {3}".format(change["model"], change["field"], _show(change["before"]), _show(change["after"])))
    if not changes and not added:
        print("  nothing moved; the registry is current")
    for model in unknown:
        print("  NOT IN CATALOGUE: {0} — kept as it stands".format(model))

    written = write_back(models, registry.files, target_dir, changes=changes, dry_run=args.dry_run)
    if args.dry_run:
        for path in written:
            print("  would write {0}".format(path))
        print("--dry-run: nothing written")
        return 0
    for path in written:
        print("wrote {0}".format(path))
    return 0


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


def _show(value):
    if isinstance(value, list):
        return "/".join(value)
    return "null" if value is None else str(value)


if __name__ == "__main__":
    sys.exit(main())

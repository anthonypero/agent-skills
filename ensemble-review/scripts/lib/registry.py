"""The model registry: one file per model, holding catalogue facts and the choices about that model.

`templates/models/<slug>.json` is the price and capability cache the cost pre-flight is
unimplementable without, and it is also where a person says what that model is *for*. The two halves
live in one file and are owned by different writers:

- **Facts** — `input_price_per_token`, `output_price_per_token`, `context_limit`,
  `effort_vocabulary`, and the `source` / `refreshed_at` provenance beside them. `refresh_models.py`
  writes these from the OpenRouter catalogue and nothing else does.
- **Choices** — `connector` (which endpoint serves it), `family` (this skill's own vocabulary),
  `tiers` (which tier or tiers this model plays for its family), the **`effort` map** from an
  abstract level to this model's own rung, `output_token_prior` and its source, `min_max_tokens`,
  and the measured price fields. A person writes these and a refresh never touches them.

One file per model rather than one `models.json` for all of them, because the two writers were
colliding in one document and because adding a model should be dropping in a file. **Model files
deep-merge across the cascade's three roots**, package first, user over it, project last: a per-owner
copy that sets one model's effort rung keeps taking the package's price refreshes for that model,
which a whole-file replace would silently stop.

Three rules the spec makes load-bearing and this module enforces:

- **A resolved model absent from the registry is a composition error**, never a silent zero in the
  projection and never a live fetch inside the budget gate. `require()` raises `MissingModel`, which
  the caller turns into exit 1 naming the model and the fix.
- **The output-token prior is a prior.** It is seeded from measured runs, labelled as an estimate
  everywhere it is used, and `refresh_models.py` never touches it: the catalogue knows what a token
  costs and only a run knows how many a lens spends.
- **The tiers × families map is derived, not written.** It is built here from the model files' own
  `family` and `tiers`, so the one place a model's tier is stated is the file about that model. Two
  models claiming one family at one tier is a composition error rather than a coin toss.

`min_max_tokens` is the completion floor: a model that reliably needs more room than the run's cap
raises the cap for its own seat. The cap actually sent is what is recorded, per attempt.
"""

import json
import os

from . import paths as paths_lib

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_MODEL_DIR = os.path.join(SKILL_DIR, "templates", "models")

# The completion cap every call carries when the run does not say otherwise. Reasoning tokens bill
# against it on most providers, so it is a cost knob and a truncation knob at once.
DEFAULT_MAX_TOKENS = 32000

# The length retry doubles the cap once. Named here so the retry, the manifest and the docs agree.
LENGTH_RETRY_MULTIPLIER = 2

SCHEMA_VERSION = "2"

# **The three abstract effort levels, and why three rather than five.** Vendors do not share an
# effort ladder: Claude and OpenAI run five or six rungs, Kimi, GLM and DeepSeek exactly three,
# Grok four, Gemini three or four. A five-level abstraction would have to bind two abstract levels
# onto one rung for every three-rung model — and the manifest would then record two different
# answers for one parameter actually sent, which is a lie about the run. Three levels bind one to
# one on the narrowest ladder on the catalogue. The names are deliberately not vendor words: no
# provider has a rung called `light` or `deep`, so a level can never be mistaken for a rung.
EFFORT_LEVELS = ("light", "standard", "deep")
DEFAULT_EFFORT_LEVEL = "standard"

# The fields a person owns. `refresh_models.py` never writes one, and `--add` seeds them null.
CHOICE_FIELDS = (
    "connector", "family", "tiers", "effort",
    "output_token_prior", "prior_source", "min_max_tokens",
    "measured_output_price", "measured_output_price_source",
)

# The fields the catalogue owns. Only these are refreshed.
FACT_FIELDS = ("input_price_per_token", "output_price_per_token", "context_limit", "effort_vocabulary")


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
    """The registry is missing, unreadable, or says two contradictory things about one cell."""


class EffortError(Exception):
    """An abstract level this model does not map, or a rung it does not accept. Exit 1 either way."""


class Registry(object):
    """A loaded registry. Read-only for every caller but `refresh_models.py`.

    `data` keeps the aggregate shape — `{"models": {id: entry}}` — because every consumer of this
    object wants a lookup by id and building one in memory is cheaper than teaching five scripts to
    walk a directory. The files it was built from are in `files`, id by id, which is what
    `refresh_models.py` writes back through and what the manifest's `roots` block records.
    """

    def __init__(self, data, path, files=None, sources=None, declarations=None, paths=None):
        self.data = data
        self.path = path
        self.models = data.get("models") or {}
        # {model id: the path of the OUTERMOST layer} — where a refresh writes.
        self.files = dict(files or {})
        # {model id: [(root, path), ...]} in merge order — the whole provenance.
        self.sources = dict(sources or {})
        # {model id: {rank, root, path}} — the OUTERMOST layer that declares `tiers` for this model,
        # which is what decides a contested tier cell. `rank` counts from the package outwards.
        self.declarations = dict(declarations or {})
        # The cascade this registry was loaded through, when it was loaded through one. `derive_tiers`
        # records a displaced tier cell against it so the manifest's `roots` block carries it.
        self.paths = paths

    def tier_declaration(self, model):
        """Which layer said this model plays a tier, as `{rank, root, path}`. Rank 0 is the package.

        A registry built from one directory — `--models`, or a hand-built one in a test — has no
        layers, so every model answers rank 0 and two of them claiming one cell stay a collision
        rather than one silently outranking the other.
        """
        found = self.declarations.get(model)
        if found:
            return found
        return {"rank": 0, "root": None, "path": self.files.get(model)}

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
        """The oldest `refreshed_at` any entry carries — the age of the stalest fact in the cache."""
        stamps = sorted(str(entry.get("refreshed_at")) for entry in self.models.values()
                        if entry.get("refreshed_at"))
        return stamps[0] if stamps else self.data.get("refreshed_at")

    def file_for(self, model):
        return self.files.get(model)


def unpriced_reason(entry):
    """Why this entry cannot be projected, or None when it can."""
    input_price, output_price = prices(entry)
    missing = [name for name, value in (("input", input_price), ("output", output_price)) if value is None]
    if not missing:
        return None
    return "carries no {0} price".format(" or ".join(missing))


def read_model_file(path):
    """One model file, with its `id` checked against the slug the cascade found it under."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise RegistryError("{0} is not a model file: it does not hold a JSON object".format(path))
    from_slug = paths_lib.model_id_for_slug(os.path.basename(path))
    declared = data.get("id")
    if declared and declared != from_slug:
        raise RegistryError(
            "model file {0} declares `id: {1!r}` but its filename says {2!r}.\n"
            "  A model file is named by its id with every '/' written '{3}'. The two have to agree, "
            "or a lookup by id and a lookup by filename reach different files.".format(
                path, declared, from_slug, paths_lib.SLUG_SEPARATOR))
    data.setdefault("id", from_slug)
    return data


def load(paths, override=None):
    """Build the registry from every model file the cascade offers, deep-merged across the roots.

    `override` is `--models`: one directory, taken as given, no cascade — the operator-path rule the
    old `--models <file>` had, moved to the directory that replaced the file.
    """
    layers, directories = paths.model_layers(override)
    if not directories:
        raise RegistryError(
            "no model registry directory in any root.\n  tried, in order:\n{0}\n"
            "  The registry ships seeded and `install.sh` refreshes it; run "
            "`python3 scripts/refresh_models.py` to build one.".format(
                "\n".join("    " + os.path.join(root, relative)
                          for root in paths.roots for relative in paths_lib.MODEL_DIRS)))
    if not layers:
        raise RegistryError(
            "the model registry directories hold no model files: {0}".format(
                ", ".join(directory for _root, directory in directories)))

    # Merge order is package first, so a root's position in this list *is* how far out it sits.
    rank_of = {root: index for index, (root, _directory) in enumerate(directories)}

    models = {}
    files = {}
    declarations = {}
    for model_id, entries in layers.items():
        merged = {}
        for root, path in entries:
            data = read_model_file(path)
            if "tiers" in data:
                # The outermost layer that *declares* tiers wins a contested cell, so the layer is
                # remembered as each one speaks. A file that sets only `effort` says nothing about
                # cells and does not claim one.
                declarations[model_id] = {"rank": rank_of.get(root, 0), "root": root, "path": path}
            merged = paths_lib.deep_merge(merged, data)
        models[model_id] = merged
        outermost_root, outermost_path = entries[-1]
        files[model_id] = outermost_path
        paths.note_model(model_id, outermost_root)

    label = " + ".join(directory for _root, directory in directories)
    return Registry({"schema_version": SCHEMA_VERSION, "models": models}, label,
                    files=files, sources=layers, declarations=declarations, paths=paths)


def load_dir(directory):
    """Every model file in one directory, with no cascade. For `refresh_models.py` and for tests."""
    models = {}
    files = {}
    for entry in sorted(os.listdir(directory)):
        if not entry.endswith(".json"):
            continue
        path = os.path.join(directory, entry)
        data = read_model_file(path)
        models[data["id"]] = data
        files[data["id"]] = path
    return Registry({"schema_version": SCHEMA_VERSION, "models": models}, directory, files=files)


# --- the derived tiers x families map ---------------------------------------------------------------

def derive_tiers(registry, family_order=(), tier_order=(), connector=None):
    """The tiers × families map, built from the model files' own `family` and `tiers`.

    **Declaration order is the config's**, because seating depends on it: `non-claude` takes the
    first eligible family in declaration order, and a re-seat walks the same list. `family_order` and
    `tier_order` come from `config.json`, which is the one run-wide place a vocabulary belongs; any
    family or tier a model file names that the config's list does not is appended afterwards in
    sorted order, so dropping in a model for a family nobody has declared works and is deterministic.

    `connector` filters to the models that endpoint serves, so a second endpoint's model files do
    not seat themselves into this connector's map.

    **A contested cell is decided by the cascade, one level down.** Two model files claiming one
    family at one tier from **different roots** is not an ambiguity: it is a project or a machine
    saying "at this tier, this family is my model", which is the same thing an outer `config.json`
    fragment says about any other key. The outermost root that *declares `tiers`* wins, so the
    one-edit promise holds — dropping one model file that declares a tier is the whole of pointing
    a family at your own model — and the displaced model is recorded in the manifest's `roots` block
    as `tier_map_overrides`, because a packaged model quietly replaced is exactly the substitution a
    reader of the run has to be able to see. Declaring `tiers` is what claims a cell: a user file
    that sets only `effort` merges its rung and claims nothing.

    **Two files at the same root claiming one cell is still a composition error.** Nothing decides
    between them — they are the same layer, so there is no outer and no inner — and guessing would
    seat a panel on a coin toss.
    """
    cells = {}
    claims = {}
    overrides = []
    seen_families = []
    seen_tiers = []
    for model_id in sorted(registry.models):
        entry = registry.models[model_id] or {}
        if connector and entry.get("connector") and entry["connector"] != connector:
            continue
        family = entry.get("family")
        tiers = entry.get("tiers")
        if not family or not isinstance(tiers, list):
            continue
        if family not in seen_families:
            seen_families.append(family)
        declaration = registry.tier_declaration(model_id)
        for tier in tiers:
            if not tier:
                continue
            if tier not in seen_tiers:
                seen_tiers.append(tier)
            held = claims.get((tier, family))
            if held and held["model"] != model_id:
                if held["rank"] == declaration["rank"]:
                    raise RegistryError(_same_root_collision(family, tier, held, model_id, declaration))
                if declaration["rank"] > held["rank"]:
                    winner = dict(declaration, model=model_id)
                    displaced = held["model"]
                else:
                    winner = held
                    displaced = model_id
                overrides.append({"tier": tier, "family": family, "model": winner["model"],
                                  "displaced": displaced, "root": winner["root"]})
                claims[(tier, family)] = winner
            elif not held:
                claims[(tier, family)] = dict(declaration, model=model_id)
            cells.setdefault(tier, {})[family] = claims[(tier, family)]["model"]

    for record in overrides:
        # The holder is read back at the end rather than written as each contest resolves: three
        # files claiming one cell would otherwise leave a record naming a model that a later,
        # further-out file then displaced in its turn. Each record says truly "this model took this
        # cell from that one"; the winner named is the one the run actually seated.
        winner = claims[(record["tier"], record["family"])]
        record["model"], record["root"] = winner["model"], winner["root"]
        if registry.paths is not None:
            registry.paths.note_tier_override(record)

    tiers_in_order = [t for t in tier_order if t in cells]
    tiers_in_order += sorted(t for t in cells if t not in tiers_in_order)
    families_in_order = [f for f in family_order]
    families_in_order += sorted(f for f in seen_families if f not in families_in_order)

    return {
        tier: {family: cells[tier][family] for family in families_in_order if family in cells[tier]}
        for tier in tiers_in_order
    }


def _same_root_collision(family, tier, held, model_id, declaration):
    """Two files at one root claiming one cell. The message has to say what to do about it.

    The old text said "drop the tier from one of the two model files", which is not actionable when
    one of them is the packaged file: the package is read-only during a run and editing it is not
    the fix anybody should reach for. So the message names the recipe that works from outside —
    `"tiers": []` in an outer layer, which replaces because a list replaces — and says which root to
    put it in.
    """
    return (
        "two models claim the {0!r} family at tier {1!r}: {2} and {3}.\n"
        "  Both are declared at the same root, so nothing decides between them: a tier cell holds "
        "one model and there is no outer layer here to prefer.\n"
        "    {2}: {4}\n"
        "    {3}: {5}\n"
        "  A model file declared at an **outer** root wins a cell over one declared further in, so "
        "the ordinary way to point a family at your own model is to drop one model file that "
        "declares that tier into <project>/.agents/ensemble-review/models/ or "
        "~/.config/ensemble-review/models/.\n"
        "  **The packaged copy is read-only** and is not the file to edit. To empty a cell from an "
        "outer layer instead, write `\"tiers\": []` into your own copy of that model's file — a "
        "list replaces on merge, so the packaged tiers go away.\n"
        "  Or give one of the two a family of its own.".format(
            family, tier, held["model"], model_id,
            (held.get("path") or held.get("root") or "?"),
            (declaration.get("path") or declaration.get("root") or "?")))


# --- abstract effort --------------------------------------------------------------------------------

def effort_map(entry):
    value = (entry or {}).get("effort")
    return value if isinstance(value, dict) else {}


def effort_binding(entry, level, model=None):
    """What this model is actually sent for an abstract level, as `(kind, value)`.

    Two shapes are accepted and both ship today's word form:

    - a **string** — that model's own rung, e.g. `"xhigh"`. Checked against the model's recorded
      vocabulary, which is the check that has always been here.
    - an **object** `{"max_tokens": N}` (or a bare integer) — a **reasoning-token budget**, for the
      models where OpenRouter takes one. A budget is not a word and is not checked against the
      vocabulary; it is checked for being a positive integer.

    Both error cases are composition errors, raised rather than dropped. A level the model does not
    map cannot be silently downgraded to "send nothing": the seat would run at whatever depth the
    provider defaults to, and a panel whose seats ran at unintended depths is not the comparison this
    skill exists to make. A mapped word outside the vocabulary is the same failure one step later.
    """
    model = model or (entry or {}).get("id") or "this model"
    vocabulary = (entry or {}).get("effort_vocabulary")
    if not isinstance(vocabulary, list) or not vocabulary:
        # **A model with no recorded vocabulary has no effort knob this skill can name**, so it is
        # dispatched with no reasoning parameter at all — v0's behaviour, which works everywhere at
        # the cost of control. This is not the same as a level nobody mapped: there, a ladder exists
        # and nobody said which rung `standard` is, and guessing would run the seat at a depth
        # nobody chose. Here there is no ladder, so there is nothing to choose and nothing to send.
        return "none", None

    mapping = effort_map(entry)
    if level not in mapping:
        raise EffortError(
            "{0} has no {1!r} rung: its model file maps {2}.\n"
            "  Abstract effort levels are {3}. Add {1!r} to that model file's `effort` map, or run "
            "the seat at a level it maps.".format(
                model, level, ", ".join(sorted(mapping)) or "no effort levels at all",
                "/".join(EFFORT_LEVELS)))

    value = mapping[level]
    if isinstance(value, dict):
        budget = value.get("max_tokens")
    elif isinstance(value, int) and not isinstance(value, bool):
        budget = value
    else:
        budget = None

    if budget is not None:
        try:
            budget = int(budget)
        except (TypeError, ValueError):
            budget = None
        if not budget or budget <= 0:
            raise EffortError(
                "{0} maps effort level {1!r} to a reasoning-token budget that is not a positive "
                "integer: {2!r}".format(model, level, mapping[level]))
        return "tokens", budget

    if not isinstance(value, str) or not value.strip():
        raise EffortError(
            "{0} maps effort level {1!r} to {2!r}, which is neither one of its own effort words nor "
            "a reasoning-token budget".format(model, level, value))
    word = value.strip()
    if effort_is_supported(entry, word) is False:
        raise EffortError(
            "{0} maps effort level {1!r} to {2!r}, which is outside its recorded vocabulary {3}.\n"
            "  Fix that model file's `effort` map, or refresh the vocabulary with\n"
            "  python3 scripts/refresh_models.py".format(
                model, level, word, "/".join((entry or {}).get("effort_vocabulary") or [])))
    return "word", word


def default_effort_map(vocabulary):
    """The migration's default binding for one model's ladder, top-down.

    `deep` is the top rung and `light` the bottom, which are the two ends nobody argues about.
    `standard` is the middle of a three-rung ladder and **one below the top** on a ladder of four or
    more — because on a five- or six-rung ladder the arithmetic middle is two or three steps down,
    and a level called `standard` that runs a frontier model at `medium` is not what the word means
    anywhere else in this skill.
    """
    rungs = [r for r in (vocabulary or []) if isinstance(r, str) and r.strip()]
    if not rungs:
        return {}
    if len(rungs) == 1:
        return {level: rungs[0] for level in EFFORT_LEVELS}
    if len(rungs) == 2:
        return {"deep": rungs[0], "standard": rungs[0], "light": rungs[-1]}
    middle = rungs[1] if len(rungs) >= 4 else rungs[len(rungs) // 2]
    return {"deep": rungs[0], "standard": middle, "light": rungs[-1]}


# --- the small readers every caller shares ----------------------------------------------------------

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
    """Whether an effort string is in this model's vocabulary.

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

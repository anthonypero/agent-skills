"""The workspace-first resolution cascade — framework principle 12's carrier.

**The skill package is read-only.** Nothing in a run ever writes into `skills/ensemble-review/`, and
every file a run loads is resolved through three roots, in this order:

1. `<workspace>/.config/ensemble-review/` — the project holding the artifact under review, named by
   `--workspace` on `run_panel.py` and `reconcile.py`, else the working directory. It is `.config/`
   and not `.agents/` because `.agents/` is the agents framework's working folder — notes, ideas,
   runs, operational state — and this skill is published into projects that do not use that
   framework; `.config/<skill>/` mirrors the user tier's `~/.config/<skill>/`, so every project-like
   root follows one rule.
2. `~/.config/ensemble-review/` — **the user tier**, this machine's owner. `$XDG_CONFIG_HOME` is
   honoured when it is set, so the root is `$XDG_CONFIG_HOME/ensemble-review/` there. It exists so a
   per-owner choice — a key source, a connector this owner trusts, one model's effort rung — is made
   once for every project on the machine instead of once per project. It is optional: a machine with
   no such directory resolves exactly as it did with two roots.
3. `SKILL_DIR` — the package itself, derived from this file's own location.

Two rules, and they are not the same rule:

- **Files** — personas, panel templates, references, schemas, backend drivers, **connector files**:
  **first hit wins, whole file**. A same-named file under an outer root fully replaces the packaged
  one; there is no per-field merge. A connector is a whole file on purpose: an endpoint is a bundle
  of a driver, a base URL, a key source and a billing posture, and half of one is not an endpoint.
  The root each loaded file came from is recorded and lands in the manifest's `roots` block.
- **`config.json` and model files** — **deep merge**, package first, user over it, workspace last, at
  key granularity. A workspace fragment carrying only `{"default_tier": "fast"}` changes that one
  cell and nothing else, and a user copy of one model file that sets only `{"effort": {"standard":
  "max"}}` keeps taking the package's price refreshes for that model. Merging rather than replacing
  is what stops a per-owner choice from freezing a price from the day it was written.

**The cascade decides a contested tier cell too, one level down.** The tiers × families map is
derived from the model files, so two files can claim one family at one tier; the outermost root that
**declares `tiers`** wins it, which is what keeps "point this family at my own model" to one dropped
file. `registry.derive_tiers` does the deciding and records each displaced model here through
`note_tier_override`, so the manifest's `roots` block carries it. Two files at the **same** root
claiming one cell is still a composition error: one layer, so there is no outer and no inner.

**Where a file goes in an outer root.** The spec gives one example — a driver at
`.config/ensemble-review/backends/azure_openai.py` — and no full layout, so this module defines one:
an outer root mirrors the *logical* category rather than the package's internal nesting.
`config.json` sits at the top of it, connectors in `connectors/`, model files in `models/`, panels in
`panels/`, drivers in `backends/`, and personas, references and schemas keep the package's own folder
names. Each category also accepts the package-shaped path as a second candidate under either root, so
a root that mirrors the package layout resolves too; the outer-shaped candidate is tried first.

| Category | Outer root | Package |
| --- | --- | --- |
| `config` | `config.json` | `templates/config.json` |
| `connector` | `connectors/<name>.json` | `templates/connectors/<name>.json` |
| `model` | `models/<slug>.json` | `templates/models/<slug>.json` |
| `panel` | `panels/<name>.json` | `templates/panels/<name>.json` |
| `persona` | `agents/<name>.md` | `agents/<name>.md` |
| `reference` | `references/<name>` | `references/<name>` |
| `schema` | `schemas/<name>.json` | `schemas/<name>.json` |
| `driver` | `backends/<type>.py` | `scripts/backends/<type>.py` |

**The model-file slug rule, stated once and here.** A model file's name is its model id with every
`/` written `__`: `moonshotai/kimi-k3` is `moonshotai__kimi-k3.json`. The rule is total and its
inverse is unambiguous because a model id may not itself contain `__` — `model_slug` refuses one that
does rather than minting a name two ids could share. The file also carries its own `id`, which is
what every loader reads; the slug is how the cascade finds the file, not how it learns what it is
about, so a mis-named file is caught rather than silently believed.

An explicit `--config` or `--models` path is an **operator path**, not a cascade entry: it is taken
as given and recorded as such. `--models` names a *directory* of model files, since the registry is
a directory now. Same for a `type` in a connector file that already names a `.py` file.
"""

import json
import os

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The workspace root, relative to the project holding the artifact under review.
WORKSPACE_SUBDIR = os.path.join(".config", "ensemble-review")

# The user tier's directory name, under `$XDG_CONFIG_HOME` or `~/.config`.
USER_SUBDIR = "ensemble-review"

# Points the user tier somewhere else outright. It exists so the test suite can exercise the middle
# root without reading — or writing — the developer's own `~/.config`, the same way
# `$ENSEMBLE_REVIEW_HARNESS_AGENTS_DIR` keeps the harness-judge check off the real agents directory.
USER_ROOT_ENV = "ENSEMBLE_REVIEW_USER_DIR"

# How a model id becomes a filename, and back. See the docstring.
SLUG_SEPARATOR = "__"

# Per category, the relative paths tried inside each root, in order. `{0}` is the name.
CANDIDATES = {
    "config": ("config.json", os.path.join("templates", "config.json")),
    "connector": (os.path.join("connectors", "{0}.json"), os.path.join("templates", "connectors", "{0}.json")),
    "model": (os.path.join("models", "{0}.json"), os.path.join("templates", "models", "{0}.json")),
    "panel": (os.path.join("panels", "{0}.json"), os.path.join("templates", "panels", "{0}.json")),
    "persona": (os.path.join("agents", "{0}.md"),),
    "reference": (os.path.join("references", "{0}"),),
    "schema": (os.path.join("schemas", "{0}.json"),),
    "driver": (os.path.join("backends", "{0}.py"), os.path.join("scripts", "backends", "{0}.py")),
}

# The directories a model file may live in, per root, in the order they are tried.
MODEL_DIRS = ("models", os.path.join("templates", "models"))

# What a category is called in the manifest's `roots.files` map when it has no name of its own.
_UNNAMED = ("config",)


class PathError(Exception):
    """A file the run needs that neither root holds. Every caller turns this into exit 1."""


def user_root(environ=None):
    """The user tier's root: `$XDG_CONFIG_HOME/ensemble-review`, else `~/.config/ensemble-review`.

    Returned whether or not it exists — a root that is not there simply never answers a candidate,
    and saying so in the manifest's `search` list is more useful than leaving a tier out of the
    record because this machine happens not to use it.
    """
    environ = os.environ if environ is None else environ
    override = environ.get(USER_ROOT_ENV)
    if override and override.strip():
        return os.path.abspath(os.path.expanduser(override.strip()))
    base = environ.get("XDG_CONFIG_HOME")
    if not base or not base.strip():
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(os.path.abspath(os.path.expanduser(base.strip())), USER_SUBDIR)


def model_slug(model_id):
    """`moonshotai/kimi-k3` -> `moonshotai__kimi-k3`. Refuses an id the rule could not invert."""
    name = str(model_id or "").strip()
    if not name:
        raise PathError("a model file needs a model id and this one is empty")
    if SLUG_SEPARATOR in name:
        raise PathError(
            "model id {0!r} contains {1!r}, which is the slug rule's separator: its filename would "
            "be indistinguishable from another id's.\n"
            "  Model files are named by replacing every '/' in the id with '{1}'. An id carrying "
            "'{1}' has no unambiguous filename, so it is refused rather than guessed at.".format(
                name, SLUG_SEPARATOR))
    return name.replace("/", SLUG_SEPARATOR)


def model_id_for_slug(slug):
    """The inverse of `model_slug`, for reading a directory of model files back."""
    name = str(slug or "")
    if name.endswith(".json"):
        name = name[:-5]
    return name.replace(SLUG_SEPARATOR, "/")


class Paths(object):
    """One run's cascade. Resolves files, merges the config, and remembers what came from where."""

    def __init__(self, workspace=None, skill_dir=None, user=None):
        self.skill_dir = os.path.abspath(skill_dir or SKILL_DIR)
        self.workspace = os.path.abspath(workspace or os.getcwd())
        self.workspace_root = os.path.join(self.workspace, WORKSPACE_SUBDIR)
        self.user_root = os.path.abspath(user) if user else user_root()
        self.roots = [self.workspace_root, self.user_root, self.skill_dir]
        self._files = {}
        self._overrides = []
        self._user_overrides = []
        self._tier_overrides = []

    # --- resolution ------------------------------------------------------------------------------

    def find(self, category, name=None):
        """The first hit for this category, as `{key, category, name, path, root, packaged}`, or None."""
        for root in self.roots:
            for template in CANDIDATES[category]:
                candidate = os.path.join(root, template.replace("{0}", name or ""))
                if os.path.isfile(candidate):
                    return {
                        "key": self._key(category, name),
                        "category": category,
                        "name": name,
                        "path": os.path.abspath(candidate),
                        "root": root,
                        "packaged": root == self.skill_dir,
                    }
        return None

    def require(self, category, name=None):
        """`find`, recorded, or `PathError` naming every root and every candidate that was tried."""
        found = self.find(category, name)
        if found is None:
            raise PathError(
                "no {0} {1!r} in any root.\n  tried, in order:\n{2}\n"
                "  A project override goes under {3}, a per-owner one under {4}; the packaged copy "
                "lives in {5}.".format(
                    category, name or category,
                    "\n".join("    " + os.path.join(root, t.replace("{0}", name or ""))
                              for root in self.roots for t in CANDIDATES[category]),
                    self.workspace_root, self.user_root, self.skill_dir))
        return self.note(found)

    def note(self, found):
        """Record a resolved file against the manifest's `roots` block. Returns it unchanged."""
        self._files[found["key"]] = found["root"]
        if not found["packaged"]:
            bucket = self._user_overrides if found["root"] == self.user_root else self._overrides
            if found["key"] not in bucket:
                bucket.append(found["key"])
        return found

    def note_operator_path(self, key, path):
        """Record a path the operator gave on the command line, which no cascade produced."""
        self._files[key] = os.path.dirname(os.path.abspath(path))
        return path

    # --- the named categories --------------------------------------------------------------------

    def persona(self, name):
        return self.require("persona", name)["path"]

    def panel(self, name):
        return self.require("panel", name)["path"]

    def reference(self, name):
        return self.require("reference", name)["path"]

    def schema(self, name):
        return self.require("schema", name)["path"]

    def connector(self, name):
        """One connector file, whole: first hit wins across the three roots.

        A connector replaces rather than merges because it is one endpoint's whole story — driver,
        base URL, key source, catalogue source, billing posture. A half-merged connector would be an
        endpoint nobody described: a user file setting `requires_approval: false` over a package
        `base_url` would read as a decision about *that* endpoint while pointing somewhere else.
        """
        return self.require("connector", name)["path"]

    def driver_ref(self, backend_type):
        """What `backends.load_driver` should be handed for this connector `type`.

        A `type` that already names a `.py` file or carries a separator is an operator path and is
        passed through untouched. Otherwise the cascade decides: a driver resolved under an **outer**
        root comes back as an absolute file path, so `importlib` loads it from there with no edit to
        `scripts/backends/__init__.py`; the packaged driver comes back as its bare type name, so it
        keeps being imported as `backends.<type>` and keeps its module identity.
        """
        name = str(backend_type)
        if name.endswith(".py") or os.sep in name:
            self.note_operator_path("driver:" + os.path.basename(name), name)
            return name
        found = self.find("driver", name)
        if found is None:
            raise PathError(
                "the connector binds this endpoint to `type: {0!r}` and no driver answers to it.\n"
                "  tried, in order:\n{1}\n"
                "  Drop a driver at {2}, or point `type` straight at a .py file.".format(
                    name,
                    "\n".join("    " + os.path.join(root, t.replace("{0}", name))
                              for root in self.roots for t in CANDIDATES["driver"]),
                    os.path.join(self.workspace_root, "backends", name + ".py")))
        self.note(found)
        return name if found["packaged"] else found["path"]

    # --- model files, which merge rather than replacing ---------------------------------------------

    def model_dirs(self, override=None):
        """Every directory of model files, **package first and workspace last** — the merge order.

        `--models` is an operator path and names one directory, taken as given: the registry is a
        directory of one file per model id now, so the flag that used to name `models.json` names
        the folder that replaced it.
        """
        if override:
            self.note_operator_path("models", override)
            return [(None, os.path.abspath(override))]
        found = []
        for root in reversed(self.roots):
            for relative in MODEL_DIRS:
                candidate = os.path.join(root, relative)
                if os.path.isdir(candidate):
                    found.append((root, os.path.abspath(candidate)))
                    break
        return found

    def model_layers(self, override=None):
        """`{model id: [(root, path), ...]}`, each list in merge order, plus the directories tried.

        Returns `(layers, directories)`. A model id present in more than one root appears once, with
        one entry per root that holds a file for it; the caller deep-merges them in list order, so
        the package's facts are the base and an outer root's choices sit on top.
        """
        directories = self.model_dirs(override)
        layers = {}
        for root, directory in directories:
            for entry in sorted(os.listdir(directory)):
                if not entry.endswith(".json"):
                    continue
                layers.setdefault(model_id_for_slug(entry), []).append(
                    (root, os.path.join(directory, entry)))
        return layers, directories

    def note_tier_override(self, record):
        """Record a tier cell an outer root took from a model declared further in.

        The derived map applies the cascade one level down: the outermost root that declares `tiers`
        for a model wins a contested cell. That is the right answer — it is what makes "point this
        family at my own model" one dropped file — but it is a **substitution**, and a substitution
        nobody can see is how a run comes to be judged by a model the reader thought was seated
        elsewhere. So each one is carried in the manifest beside the override lists.
        """
        if record not in self._tier_overrides:
            self._tier_overrides.append(record)
        return record

    def note_model(self, model_id, root):
        """Record which root supplied the outermost layer of one model file."""
        key = "model:{0}".format(model_id)
        if root is None:                      # an operator `--models` directory; already recorded
            return
        self._files[key] = root
        if root != self.skill_dir:
            bucket = self._user_overrides if root == self.user_root else self._overrides
            if key not in bucket:
                bucket.append(key)

    # --- the config, which merges rather than replacing --------------------------------------------

    def config(self, override=None):
        """The merged config, as `(data, path)`.

        `--config` is an operator path and is read as given. Otherwise every root that holds a
        `config.json` contributes, package first and workspace last, deep-merged so an outer
        fragment changes the cells it names and nothing else.
        """
        if override:
            self.note_operator_path("config", override)
            return _read_json(override), os.path.abspath(override)

        layers = []
        for root in reversed(self.roots):           # package first, workspace last, so workspace wins
            for template in CANDIDATES["config"]:
                candidate = os.path.join(root, template)
                if os.path.isfile(candidate):
                    layers.append((root, os.path.abspath(candidate)))
                    break
        if not layers:
            raise PathError(
                "no config.json in any root.\n  tried, in order:\n{0}".format(
                    "\n".join("    " + os.path.join(root, t)
                              for root in self.roots for t in CANDIDATES["config"])))

        merged = {}
        for root, path in layers:
            merged = deep_merge(merged, _read_json(path))
            key = "config" if root == self.skill_dir else "config-fragment"
            self._files[key] = root
            if root != self.skill_dir:
                bucket = self._user_overrides if root == self.user_root else self._overrides
                if key not in bucket:
                    bucket.append(key)
        return merged, layers[-1][1]

    # --- the audit record --------------------------------------------------------------------------

    def roots_block(self):
        """The manifest's `roots`: the search order, every loaded file's root, and the overrides.

        A map of loaded file to root — the smallest shape that answers "which copy of this did the
        run actually read?" for every file — plus the overrides in **two** lists rather than one.
        A project override and a per-owner one are different claims about a run: the first travels
        with the repository and a second builder gets it by checking the repository out, the second
        lives on one machine and nobody else has it. Folding them together would make a run that
        depends on this laptop's `~/.config` look reproducible.
        """
        return {
            "search": list(self.roots),
            "user_root": self.user_root,
            "user_root_present": os.path.isdir(self.user_root),
            "files": dict(self._files),
            "workspace_overrides": list(self._overrides),
            "user_overrides": list(self._user_overrides),
            # Every tier cell an outer root's model file took from a model declared further in:
            # `{tier, family, model, displaced, root}`. Empty on a run whose map nobody contested.
            "tier_map_overrides": list(self._tier_overrides),
        }

    def _key(self, category, name):
        if category in _UNNAMED or not name:
            return category
        return "{0}:{1}".format(category, name)


def deep_merge(base, overlay):
    """`overlay` over `base`, recursing into dicts. Lists and scalars replace; nothing is removed."""
    if not isinstance(base, dict) or not isinstance(overlay, dict):
        return overlay
    merged = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)

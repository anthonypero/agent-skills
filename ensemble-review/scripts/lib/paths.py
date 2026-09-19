"""The workspace-first resolution cascade — framework principle 12's carrier.

**The skill package is read-only.** Nothing in a run ever writes into `skills/ensemble-review/`, and
every file a run loads is resolved through two roots, in this order:

1. `<workspace>/.agents/ensemble-review/` — the project holding the artifact under review, named by
   `--workspace` on `run_panel.py` and `reconcile.py`, else the working directory.
2. `SKILL_DIR` — the package itself, derived from this file's own location.

Two rules, and they are not the same rule:

- **Files** — personas, panel templates, references, schemas, backend drivers, the model registry:
  **first hit wins, whole file**. A same-named file under the workspace root fully replaces the
  packaged one; there is no per-field merge. The root each loaded file came from is recorded and
  lands in the manifest's `roots` block.
- **`config.json`** — **deep merge**, package first, workspace keys winning on collision, at
  connector / tier / family / model-id granularity. A workspace fragment carrying only
  `{"openrouter": {"tiers": {"standard": {"glm": "x"}}}}` changes that one cell and nothing else.

**Where a file goes in the workspace root.** The spec gives one example — a driver at
`.agents/ensemble-review/backends/azure_openai.py` — and no full layout, so this module defines one:
the workspace root mirrors the *logical* category rather than the package's internal nesting.
`config.json` and `models.json` sit at the top of it, panels in `panels/`, drivers in `backends/`,
and personas, references and schemas keep the package's own folder names. Each category also accepts
the package-shaped path as a second candidate under either root, so a workspace that mirrors the
package layout resolves too; the workspace-shaped candidate is tried first.

| Category | Workspace root | Package |
| --- | --- | --- |
| `config` | `config.json` | `templates/config.json` |
| `registry` | `models.json` | `templates/models.json` |
| `panel` | `panels/<name>.json` | `templates/panels/<name>.json` |
| `persona` | `agents/<name>.md` | `agents/<name>.md` |
| `reference` | `references/<name>` | `references/<name>` |
| `schema` | `schemas/<name>.json` | `schemas/<name>.json` |
| `driver` | `backends/<type>.py` | `scripts/backends/<type>.py` |

An explicit `--config` or `--models` path is an **operator path**, not a cascade entry: it is taken
as given and recorded as such. Same for a `type` in the config that already names a `.py` file.
"""

import json
import os

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The workspace root, relative to the project holding the artifact under review.
WORKSPACE_SUBDIR = os.path.join(".agents", "ensemble-review")

# Per category, the relative paths tried inside each root, in order. `{0}` is the name.
CANDIDATES = {
    "config": ("config.json", os.path.join("templates", "config.json")),
    "registry": ("models.json", os.path.join("templates", "models.json")),
    "panel": (os.path.join("panels", "{0}.json"), os.path.join("templates", "panels", "{0}.json")),
    "persona": (os.path.join("agents", "{0}.md"),),
    "reference": (os.path.join("references", "{0}"),),
    "schema": (os.path.join("schemas", "{0}.json"),),
    "driver": (os.path.join("backends", "{0}.py"), os.path.join("scripts", "backends", "{0}.py")),
}

# What a category is called in the manifest's `roots.files` map when it has no name of its own.
_UNNAMED = ("config", "registry")


class PathError(Exception):
    """A file the run needs that neither root holds. Every caller turns this into exit 1."""


class Paths(object):
    """One run's cascade. Resolves files, merges the config, and remembers what came from where."""

    def __init__(self, workspace=None, skill_dir=None):
        self.skill_dir = os.path.abspath(skill_dir or SKILL_DIR)
        self.workspace = os.path.abspath(workspace or os.getcwd())
        self.workspace_root = os.path.join(self.workspace, WORKSPACE_SUBDIR)
        self.roots = [self.workspace_root, self.skill_dir]
        self._files = {}
        self._overrides = []

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
        """`find`, recorded, or `PathError` naming both roots and every candidate that was tried."""
        found = self.find(category, name)
        if found is None:
            raise PathError(
                "no {0} {1!r} in any root.\n  tried, in order:\n{2}\n"
                "  A workspace override goes under {3}; the packaged copy lives in {4}.".format(
                    category, name or category,
                    "\n".join("    " + os.path.join(root, t.replace("{0}", name or ""))
                              for root in self.roots for t in CANDIDATES[category]),
                    self.workspace_root, self.skill_dir))
        return self.note(found)

    def note(self, found):
        """Record a resolved file against the manifest's `roots` block. Returns it unchanged."""
        self._files[found["key"]] = found["root"]
        if not found["packaged"] and found["key"] not in self._overrides:
            self._overrides.append(found["key"])
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

    def registry(self, override=None):
        """The model registry path: `--models` when given, else the cascade."""
        if override:
            return self.note_operator_path("registry", override)
        return self.require("registry")["path"]

    def driver_ref(self, backend_type):
        """What `backends.load_driver` should be handed for this connector `type`.

        A `type` that already names a `.py` file or carries a separator is an operator path and is
        passed through untouched. Otherwise the cascade decides: a driver resolved under the
        **workspace** root comes back as an absolute file path, so `importlib` loads it from there
        with no edit to `scripts/backends/__init__.py`; the packaged driver comes back as its bare
        type name, so it keeps being imported as `backends.<type>` and keeps its module identity.
        """
        name = str(backend_type)
        if name.endswith(".py") or os.sep in name:
            self.note_operator_path("driver:" + os.path.basename(name), name)
            return name
        found = self.find("driver", name)
        if found is None:
            raise PathError(
                "the config binds this connector to `type: {0!r}` and no driver answers to it.\n"
                "  tried, in order:\n{1}\n"
                "  Drop a driver at {2}, or point `type` straight at a .py file.".format(
                    name,
                    "\n".join("    " + os.path.join(root, t.replace("{0}", name))
                              for root in self.roots for t in CANDIDATES["driver"]),
                    os.path.join(self.workspace_root, "backends", name + ".py")))
        self.note(found)
        return name if found["packaged"] else found["path"]

    # --- the config, which merges rather than replacing --------------------------------------------

    def config(self, override=None):
        """The merged config, as `(data, path)`.

        `--config` is an operator path and is read as given. Otherwise every root that holds a
        `config.json` contributes, package first and workspace last, deep-merged so a workspace
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
        for index, (root, path) in enumerate(layers):
            merged = deep_merge(merged, _read_json(path))
            key = "config" if root == self.skill_dir else "config-fragment"
            self._files[key] = root
            if root != self.skill_dir and key not in self._overrides:
                self._overrides.append(key)
            del index
        return merged, layers[-1][1]

    # --- the audit record --------------------------------------------------------------------------

    def roots_block(self):
        """The manifest's `roots`: the search order, every loaded file's root, and the overrides.

        A map of loaded file to root — the smallest shape that answers "which copy of this did the
        run actually read?" for every file, and names the workspace overrides in one list so a reader
        does not have to diff two directories to see that a project changed something.
        """
        return {
            "search": list(self.roots),
            "files": dict(self._files),
            "workspace_overrides": list(self._overrides),
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

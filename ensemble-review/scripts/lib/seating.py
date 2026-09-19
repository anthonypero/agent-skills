"""Seat resolution: which tier a seat runs at, which family it takes, and which model that is.

Two orderings live here and neither is negotiable, because a second builder has to reproduce a run
from the same panel and the same config.

**Tier resolution order**, first match wins:

1. `--model <seat-id>=<model-id>` — pins one seat to a concrete id, overriding everything below it.
2. `--tier` on the command line.
3. the seat's own `tier`.
4. the panel's `tier`.
5. the config's `default_tier`.
6. the persona frontmatter's `model`, when it names a key in the tier map.

This inverts framework §8, which puts agent frontmatter above the config default: here the tier is a
property of the run's stakes, not of the lens, and an operator forcing a whole panel cheap must win.
The level that decided each seat is recorded as `tier_source`, so the manifest says why a seat ran
where it ran. A seat pinned with `--model` records `tier_source: "--model"`; its `tier` still comes
from the levels below, because the manifest and `_meta` both carry one.

**Family constraint resolution**, four passes over the seats in template order, so the result does
not depend on how the template happens to be written:

1. **Named families** take their family and reserve it — all of them, across the whole template,
   before anything re-seats. A named family with **no cell at the resolved tier** reserves nothing
   here and waits for pass 2.
2. **Missing cells.** Each seat pass 1 could not seat re-seats once onto the next family in
   declaration order that has a cell and nobody holds, and records
   `substitution: {kind: "missing_cell", ...}`. This is a separate pass because resolving a missing
   cell inside pass 1 lets it take a family a later named seat is about to ask for by name:
   `[google, claude]` at frontier would put two seats on claude while openai sat free, and
   `[claude, google]` would not — the same panel, two answers, decided by template order.
3. **`non-claude`** takes the first family in the config's declaration order for that seat's tier
   that is not `claude`, has a cell, and no seat already holds.
4. **`distinct`** takes the first family with a cell that no other seat holds, reserving against all
   other seats in both directions. It resolves last and therefore always adds a family the panel did
   not have.

An unsatisfiable constraint does **not** fail the run. The seat falls back to the **least-held**
family — fewest seats already on it, ties broken by declaration order — and records
`substitution: {kind: "constraint_unsatisfied", ...}`. `reconcile.py` names every recorded
substitution in the reconciliation's method caveat.

**No re-seat of any kind lands on `claude` unless the seat asked for `claude` by name.** Missing
cell, unsatisfiable constraint and runtime unavailability all draw from `reseat_candidates()`, which
drops claude. Claude is first in the config's declaration order, so without the rule every first
re-seat would land on it, and the default panel seats no Claude family on purpose. Owner ruling,
2026-09-18. When claude is the only *free* family, the seat doubles up on a non-claude family
instead; only a tier with no other family at all is a composition error.

**`reviewer_id` is minted from the lens and what the seat *asked for*, never from what it got** —
`fidelity-openai` for a named family, `buildability-non-claude` for a constraint. A constrained seat's
resolved family depends on what else the panel seats, so an id derived from it would move between
runs of the same panel; and a seat re-seated mid-run would change its own id, its report filename and
its manifest key half way through a run. The resolved family is in `family`, and the difference is in
`substitution`.
"""

CONSTRAINTS = ("non-claude", "distinct")

DEFAULT_TIER = "frontier"

# In `tier_source`, the level that decided the seat.
TIER_SOURCES = ("--model", "--tier", "seat", "panel", "config", "persona", "default")


class SeatingError(Exception):
    """A composition error: the caller refuses before dispatch and exits 1."""


def families_at(config_entry, tier):
    """Every family with a concrete model at this tier, in the config's declaration order."""
    tiers = config_entry.get("tiers") or {}
    cells = tiers.get(tier) or {}
    return [family for family, model in cells.items() if model]


def model_at(config_entry, tier, family):
    return ((config_entry.get("tiers") or {}).get(tier) or {}).get(family)


def reviewer_id(lens, requested, suffix=""):
    return "{0}-{1}{2}".format(lens, requested, suffix)


def resolve_tier(seat, panel, config_entry, cli_tier, pinned, frontmatter):
    """(tier, tier_source) for one seat, by the order in this module's docstring."""
    levels = [
        ("--tier", cli_tier),
        ("seat", seat.get("tier")),
        ("panel", panel.get("tier")),
        ("config", config_entry.get("default_tier")),
        ("persona", _frontmatter_tier(frontmatter, config_entry)),
    ]
    tier, source = DEFAULT_TIER, "default"
    for name, value in levels:
        if value:
            tier, source = value, name
            break
    return tier, ("--model" if pinned else source)


def _frontmatter_tier(frontmatter, config_entry):
    """The persona's `model:` value, but only when it names a tier the config actually offers.

    Framework §6 wants the value to match a key in the tiers map, and the shipped personas now carry
    `model: frontier`, which does — so this level is live: a seat with no `--model`, no `--tier`, no
    seat tier, no panel tier and no config `default_tier` resolves to `frontier` with
    `tier_source: "persona"`. A persona carrying a value that names no tier in this config (the
    abstract `model: high` the personas shipped with through stage 2b) contributes nothing and the
    seat falls through to the hard default, which is what keeps a workspace persona from forcing a
    tier the config does not offer.
    """
    value = (frontmatter or {}).get("model")
    if value and value in (config_entry.get("tiers") or {}):
        return value
    return None


def resolve(panel, config_entry, cli_tier=None, pinned=None, frontmatter_fn=None, connector=None):
    """Resolve every seat of a panel. Returns the seat records, in template order.

    `pinned` maps a seat id (`<lens>-<requested>`) to a concrete model id — `--model` on the command
    line. `frontmatter_fn(lens)` returns that lens's persona frontmatter, or None.
    """
    pinned = dict(pinned or {})
    connector = connector or config_entry.get("type", "openai_compat")
    template_seats = panel.get("seats") or []
    if not template_seats:
        raise SeatingError("panel {0!r} seats nobody".format(panel.get("name") or "ad-hoc"))

    seats = []
    for index, raw in enumerate(template_seats):
        lens = raw.get("lens")
        requested = raw.get("family")
        if not lens or not requested:
            raise SeatingError("seat {0} of panel {1!r} is missing a `lens` or a `family`".format(
                index + 1, panel.get("name") or "ad-hoc"))
        identity = reviewer_id(lens, requested, raw.get("suffix") or "")
        tier, tier_source = resolve_tier(
            raw, panel, config_entry, cli_tier, identity in pinned,
            frontmatter_fn(lens) if frontmatter_fn else None)
        seats.append({
            "reviewer_id": identity,
            "lens": lens,
            "requested": requested,
            "family": None,
            "tier": tier,
            "tier_source": tier_source,
            "model": pinned.pop(identity, None),
            "connector": connector,
            "effort": None,
            "substitution": None,
        })

    if pinned:
        raise SeatingError(
            "--model named {0} that this panel does not seat. Seats are: {1}".format(
                ", ".join(sorted(repr(k) for k in pinned)),
                ", ".join(seat["reviewer_id"] for seat in seats)))

    offered = config_entry.get("tiers") or {}
    for seat in seats:
        if seat["tier"] not in offered and not seat["model"]:
            raise SeatingError(
                "seat {0} resolves to tier {1!r} (from {2}), which the config does not offer. "
                "It offers: {3}".format(seat["reviewer_id"], seat["tier"], seat["tier_source"],
                                        ", ".join(sorted(offered)) or "no tiers at all"))

    held = {}   # family -> how many seats hold it
    _seat_named(seats, config_entry, held)
    _seat_missing_cells(seats, config_entry, held)
    _seat_constraint(seats, config_entry, held, "non-claude")
    _seat_constraint(seats, config_entry, held, "distinct")

    for seat in seats:
        if not seat["model"]:
            seat["model"] = model_at(config_entry, seat["tier"], seat["family"])
        seat["effort"] = (config_entry.get("effort") or {}).get(seat["model"])
    return seats


# --- the four passes -------------------------------------------------------------------------------

def _seat_named(seats, config_entry, held):
    """Pass 1. Every named family reserves, across the whole template, before anything re-seats.

    A seat whose family has no cell at its tier reserves nothing and is left for pass 2. Resolving
    its replacement here would let it take a family a later named seat is about to ask for by name:
    `[google, claude]` at frontier would put two seats on claude while openai sat free, and
    `[claude, google]` would not — the same panel, two answers, decided by template order.
    """
    for seat in seats:
        if seat["requested"] in CONSTRAINTS:
            continue
        family = seat["requested"]
        if family in families_at(config_entry, seat["tier"]) or seat["model"]:
            # A seat pinned with `--model` carries its own model, so a missing cell cannot reach it.
            _hold(seat, family, held)


def _seat_missing_cells(seats, config_entry, held):
    """Pass 2. Every named seat pass 1 could not seat re-seats once, in template order."""
    for seat in seats:
        if seat["requested"] in CONSTRAINTS or seat["family"]:
            continue
        family = seat["requested"]
        replacement = _pick(reseat_candidates(config_entry, seat["tier"], family), held)
        if replacement is None:
            raise SeatingError(
                "seat {0} asks for family {1!r} at tier {2!r}, which has no model there, and that tier "
                "offers no other family to re-seat onto. Check the config's tier map.".format(
                    seat["reviewer_id"], family, seat["tier"]))
        _hold(seat, replacement, held)
        seat["substitution"] = {
            "kind": "missing_cell",
            "requested": family,
            "resolved": replacement,
            "reason": "family {0!r} has no model at tier {1!r}; re-seated once onto the next "
                      "available family in declaration order".format(family, seat["tier"]),
        }


def _seat_constraint(seats, config_entry, held, constraint):
    """Passes 3 and 4. `non-claude` then `distinct`, each in template order."""
    for seat in seats:
        if seat["requested"] != constraint:
            continue
        available = families_at(config_entry, seat["tier"])
        eligible = [f for f in available if f != "claude"] if constraint == "non-claude" else list(available)
        free = [f for f in eligible if not held.get(f)]
        if free:
            _hold(seat, free[0], held)
            continue
        # Nothing free: this is a re-seat, so claude is out of the running whatever the constraint was.
        fallback = _least_held(reseat_candidates(config_entry, seat["tier"], constraint), held)
        if fallback is None:
            raise SeatingError(
                "seat {0} asks for {1!r} at tier {2!r}, and that tier offers no family it could take. "
                "Check the config's tier map.".format(seat["reviewer_id"], constraint, seat["tier"]))
        _hold(seat, fallback, held)
        seat["substitution"] = {
            "kind": "constraint_unsatisfied",
            "requested": constraint,
            "resolved": fallback,
            "reason": "every family at tier {0!r} that satisfies {1!r} is already held by another "
                      "seat; fell back to the least-held family".format(seat["tier"], constraint),
        }


def reseat_candidates(config_entry, tier, requested):
    """The families a **re-seat** may land on, in the config's declaration order.

    Two exclusions, and they are the same rule from two directions:

    - the family that failed, which is what "re-seat" means;
    - **`claude`**, unless the seat asked for `claude` by name. Claude is first in the config's
      declaration order, so without this every first re-seat would land on it — and the default panel
      seats no Claude family on purpose: the host session that authors and reconciles is a Claude
      model, so a Claude reviewer is the seat most correlated with the artifact. A substitution that
      quietly adds one would undo that decision without anybody choosing it. Owner ruling, 2026-09-18.
      When claude is the only *free* family the seat doubles up on a non-claude family instead.

    An empty list means the tier genuinely has nowhere to put this seat, and the caller says so.
    """
    candidates = [f for f in families_at(config_entry, tier) if f != requested]
    if requested != "claude":
        candidates = [f for f in candidates if f != "claude"]
    return candidates


def _hold(seat, family, held):
    seat["family"] = family
    held[family] = held.get(family, 0) + 1


def _pick(candidates, held):
    """The first candidate nobody holds, in declaration order; else the least-held."""
    free = [family for family in candidates if not held.get(family)]
    if free:
        return free[0]
    return _least_held(candidates, held)


def _least_held(candidates, held):
    """Fewest seats already on it, ties broken by the config's declaration order."""
    if not candidates:
        return None
    return min(candidates, key=lambda family: (held.get(family, 0), candidates.index(family)))


# --- re-seating a family the provider will not serve -------------------------------------------------

def reseat(seat, seats, config_entry, kind="model_unavailable", reason=None, usable=None):
    """Move one seat onto the next available family, once. Returns the mutated seat, or None.

    The runtime twin of the missing-cell path: a 404 or 400 naming the model means that family is
    unreachable for this run, which is the same condition as a family with no cell, so it takes the
    same re-seat-once treatment, the same candidate rule, and the same shape of `substitution`. A
    seat that has already been substituted is not substituted again — "once" is the whole point.

    **The seat's own constraint still binds.** A `non-claude` seat that loses its family at runtime
    is still a `non-claude` seat: it is re-seated against `requested`, not against the family it
    happened to be holding, so it cannot land on `claude` by the back door. Composition time and
    dispatch time therefore answer the same question the same way.

    `usable(family, model)` filters the candidates before one is chosen, so a caller can refuse to
    re-seat onto a model its registry cannot price rather than committing and then refusing the run.
    """
    if seat.get("substitution"):
        return None
    held = {}
    for other in seats:
        if other.get("family"):
            held[other["family"]] = held.get(other["family"], 0) + 1
    was = seat["family"]
    # Against the seat's *request*, so a constraint survives the re-seat; then the family that just
    # failed is dropped, which `reseat_candidates` does not know about when the request is a constraint.
    candidates = [f for f in reseat_candidates(config_entry, seat["tier"], seat.get("requested") or was)
                  if f != was]
    if usable is not None:
        candidates = [f for f in candidates if usable(f, model_at(config_entry, seat["tier"], f))]
    replacement = _pick(candidates, held)
    if replacement is None or replacement == was:
        return None
    held[was] = max(held.get(was, 1) - 1, 0)
    seat["family"] = replacement
    seat["model"] = model_at(config_entry, seat["tier"], replacement)
    seat["effort"] = (config_entry.get("effort") or {}).get(seat["model"])
    seat["substitution"] = {
        "kind": kind,
        "requested": was,
        "resolved": replacement,
        "reason": reason or "the provider would not serve this family's model; re-seated once onto "
                            "the next available family in declaration order",
    }
    return seat


# --- the effort gate ---------------------------------------------------------------------------------

def effort_errors(seats, registry):
    """Every seat whose config effort is outside its model's registry vocabulary, as messages.

    A value the model does not accept is a **composition error**, not a warning: the run would either
    take a 400 from the provider or, worse, be quietly served at a depth nobody chose, and a panel
    whose seats ran at unintended depths is not the comparison this skill exists to make.
    """
    errors = []
    for seat in seats:
        effort = seat.get("effort")
        if not effort:
            continue
        entry = registry.get(seat["model"]) if registry else None
        vocabulary = (entry or {}).get("effort_vocabulary")
        if not isinstance(vocabulary, list) or not vocabulary:
            continue
        if effort not in vocabulary:
            errors.append(
                "seat {0} resolves to {1}, whose registry vocabulary is {2}; the config asks for "
                "effort {3!r}".format(seat["reviewer_id"], seat["model"], "/".join(vocabulary), effort))
    return errors

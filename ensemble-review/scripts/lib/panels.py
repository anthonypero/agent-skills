"""The panel-template contract: which keys a template may carry, and the refusal when it carries another.

**Unknown keys are a composition error.** Owner ruling, 2026-09-19 (SF-12): the loader rejects any
key it does not recognize, at the top level, in a seat, in `optional_seats`, and in the `synthesis`
block, and it does so **before any seat is priced or dispatched**. The reasoning is about who writes
these files. A panel template's author is the orchestrating agent, not a human reading a diff, so a
silent `min_famalies` is the agent's typo spending the owner's money on a panel that quietly ran at
the default — and a refusal that names the key and the file pushes it straight back to the agent to
fix. There is no leniency prefix and no `x-` escape hatch: operator metadata goes in `description`.

**The vocabulary lives here and nowhere else.** `run_panel.py` loads templates and `reconcile.py`
reads one back for its `synthesis` block; a second copy of the key list in either of them is a
second contract that drifts. Everything a template may say is one of the tuples below.

`effort` is the panel-level and seat-level twin of `tier`, and carries an **abstract** level —
`light`, `standard` or `deep` — never a vendor rung. A template that named a vendor word would bind
the panel to one family's ladder, which is the thing the abstraction exists to stop.

Two keys are read by nothing today and are recognized anyway — `optional_seats` and a seat's `note`.
They are documentation of a decision a future edit promotes (`spec-review`'s fifth seat is the live
example), and rejecting them would make the shipped catalog fail its own loader.
"""

# The template itself. `deferred` and `routes_to` belong to a named stub that seats nobody —
# `code-review` — and are recognized on every template rather than only on that one, because the
# loader runs before anything has decided whether this template is a stub.
PANEL_KEYS = (
    "name",
    "description",
    "requires_references",
    "min_families",
    "tier",
    "effort",
    "reconciler",
    "auto_apply",
    "verify_web",
    "seats",
    "optional_seats",
    "synthesis",
    "deferred",
    "routes_to",
)

# One seat, in `seats` or in `optional_seats`. `suffix` is what lets one template seat the same lens
# twice without minting two seats with one `reviewer_id`; `note` is read by nothing and says why an
# optional seat is not seated.
SEAT_KEYS = ("lens", "family", "tier", "effort", "suffix", "note")

# The judgment call's own seat, when a template fixes it rather than letting it follow the run.
# `effort` is here for the same reason `tier` is: a template that pins where the judgment sits should
# be able to pin how deep it thinks, and without it the only way to run a deep judgment over a light
# panel was to move the whole run's `default_effort`.
SYNTHESIS_KEYS = ("family", "tier", "effort")

# Where each vocabulary applies, for the message. The order is the order they are checked in.
_SCOPES = (
    ("the template", PANEL_KEYS),
    ("a seat", SEAT_KEYS),
    ("the `synthesis` block", SYNTHESIS_KEYS),
)


class TemplateError(Exception):
    """An unrecognized key, or a block of the wrong type. The caller turns this into exit 1."""


def unknown_keys(panel):
    """Every unrecognized key in the template, as `(where, key)`, in document order.

    `where` is a human-readable location — the template, `seats[2]`, `optional_seats[0]`, the
    `synthesis` block — so the refusal can name the thing the author has to go and find.
    """
    found = []
    if not isinstance(panel, dict):
        return found
    for key in panel:
        if key not in PANEL_KEYS:
            found.append(("the template", key))

    for block in ("seats", "optional_seats"):
        entries = panel.get(block)
        if not isinstance(entries, list):
            continue
        for index, seat in enumerate(entries):
            if not isinstance(seat, dict):
                continue
            for key in seat:
                if key not in SEAT_KEYS:
                    found.append(("{0}[{1}]".format(block, index), key))

    synthesis = panel.get("synthesis")
    if isinstance(synthesis, dict):
        for key in synthesis:
            if key not in SYNTHESIS_KEYS:
                found.append(("the `synthesis` block", key))
    return found


def validate(panel, path):
    """Raise `TemplateError` naming every unknown key and the template that carries it.

    The message lists the recognized keys for each scope that failed, because the overwhelmingly
    likely cause is a typo of one of them and the fix is then obvious from the message alone.
    """
    if not isinstance(panel, dict):
        raise TemplateError("panel template {0} is not a JSON object".format(path))

    unknown = unknown_keys(panel)
    if not unknown:
        return panel

    lines = ["panel template {0} carries {1} key(s) this loader does not recognize:".format(
        path, len(unknown))]
    for where, key in unknown:
        lines.append("  {0!r} in {1}".format(key, where))
    lines.append("An unrecognized key is refused rather than ignored: a template is written by an "
                 "agent, so a typo would otherwise run the panel at the default and spend the "
                 "money before anybody read the file. Recognized keys are:")
    for where, vocabulary in _SCOPES:
        lines.append("  {0}: {1}".format(where, ", ".join(vocabulary)))
    lines.append("Operator metadata goes in `description`.")
    raise TemplateError("\n".join(lines))

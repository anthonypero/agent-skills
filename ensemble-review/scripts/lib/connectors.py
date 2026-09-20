"""Connector files: one endpoint per file, self-contained and droppable.

A **connector** is everything this skill needs to know to make a call somewhere: the driver module
under `backends/`, the base URL, where the key comes from, where the model catalogue that refreshes
prices lives, whatever extras that endpoint takes (OpenRouter's per-model `provider_routing`), and —
new here — the **billing posture** that decides whether a run may spend on it without being asked.

`openrouter.json` ships as the default instance of the generic `openai_compat` driver. **A second
endpoint is a second file, not a config edit**: a private Azure deployment, a vendor CLI on a
subscription, a harness leg. It is a whole file through the cascade — first hit wins, no merge —
because an endpoint is a bundle and half of one is not an endpoint: a user file that set
`requires_approval: false` over a packaged `base_url` would read as a decision about *that* endpoint
while pointing somewhere else entirely.

**The spend gate.** `billing` is one of:

- `metered` — every call costs money on an account. Ships with `requires_approval: true`, and the
  run refuses to dispatch any seat bound to it without an explicit `--approve-spend`.
- `subscription` — a plan already paid for. The harness leg, a vendor CLI on a seat the owner
  already rents. No gate: the marginal call is free.
- `free` — no charge at all.

This is a different question from the budget gate and gets a different flag. `--approve-budget`
answers "this projection is over the limit you set"; `--approve-spend` answers "you may spend on
this endpoint at all". A run can be well under budget and still be the first time anybody said yes
to paying, which is the case the budget gate never covered. The answer and its source land in the
manifest as `spend_approval`, so the audit trail shows a person said yes rather than a default.

An outer-root connector file may set `requires_approval: false` for a metered endpoint that owner
has decided to trust — files replace whole, so it is one edit — and the manifest then records both
that the gate was off and **which root turned it off**, because "this machine trusts this endpoint"
is a fact about one laptop and a reader of the run needs to see it.
"""

import json
import os

from . import paths as paths_lib

METERED = "metered"
SUBSCRIPTION = "subscription"
FREE = "free"
BILLING = (METERED, SUBSCRIPTION, FREE)

# The connector every shipped config names.
DEFAULT_CONNECTOR = "openrouter"


class ConnectorError(Exception):
    """A connector file that is missing, malformed, or says something this skill cannot act on."""


def load(paths, name):
    """One connector file, as `(entry, path)`.

    `name` is normally a **name** and resolves through the cascade — first hit wins across the
    project's root, this machine's and the package's. A value that already names a `.json` file or
    carries a path separator is an **operator path** instead, taken as given and recorded as one:
    the same escape hatch a connector's own `type` has for pointing straight at a driver file, and
    it exists for the same reason — a file somewhere neither root knows about is still a file
    somebody deliberately wrote.

    A resolved file's own `name` is checked against the name it was asked for: a connector is
    selected by name from `config.json`, so a file that answers to one name while calling itself
    another would make the config and the manifest disagree about which endpoint a run used.
    """
    if name.endswith(".json") or os.sep in name:
        path = os.path.abspath(name)
        if not os.path.isfile(path):
            raise ConnectorError("no connector file at {0}".format(path))
        paths.note_operator_path("connector", path)
        found = {"path": path, "root": os.path.dirname(path), "packaged": False}
        name = None
    else:
        try:
            found = paths.require("connector", name)
        except paths_lib.PathError as failure:
            raise ConnectorError(str(failure))

    with open(found["path"], "r", encoding="utf-8") as handle:
        entry = json.load(handle)
    if not isinstance(entry, dict):
        raise ConnectorError("connector file {0} is not a JSON object".format(found["path"]))

    declared = entry.get("name")
    if name is None:
        name = declared or os.path.basename(found["path"])[:-5]
    elif declared and declared != name:
        raise ConnectorError(
            "connector file {0} calls itself {1!r} but was loaded as {2!r}.\n"
            "  A connector is selected by name in config.json's `default_connector`; the file and "
            "the name have to agree or the manifest records an endpoint the run did not use.".format(
                found["path"], declared, name))
    entry.setdefault("name", name)

    billing = entry.get("billing")
    if billing not in BILLING:
        raise ConnectorError(
            "connector {0} at {1} declares `billing: {2!r}`; it must be one of {3}.\n"
            "  The billing posture decides whether a run may spend on this endpoint without being "
            "asked, so it is required rather than defaulted: an endpoint nobody has classified is "
            "not one this skill will quietly bill.".format(
                name, found["path"], billing, "/".join(BILLING)))

    if not entry.get("type"):
        raise ConnectorError(
            "connector {0} at {1} names no driver `type`".format(name, found["path"]))

    entry["path"] = found["path"]
    entry["root"] = found["root"]
    entry["packaged"] = found["packaged"]
    return entry, found["path"]


def requires_approval(connector):
    """Whether a call on this endpoint needs an explicit yes. Metered endpoints default to yes.

    A `metered` connector that says nothing is treated as needing approval, because the default for
    "this costs money" has to be the safe one. A `subscription` or `free` connector is never gated
    however the field is written: there is nothing to approve.
    """
    connector = connector or {}
    if connector.get("billing") != METERED:
        return False
    value = connector.get("requires_approval")
    return True if value is None else bool(value)


def spend_gate(connector, seats=(), granted=False, source=None, judgment=False):
    """The manifest's `spend_approval` block, and the reason text when the gate refuses.

    Recorded on every run, not only a gated one, because "this endpoint bills nothing" is as much
    part of the audit trail as "a person approved $2.75 of calls".

    **The gate is required only when this run will actually call the endpoint.** `seats` is the
    **dispatched** seats — not the composed ones — and `judgment` says whether the judgment call
    itself lands here. A gate keyed on the connector alone asked for `--approve-spend` over a
    projection of $0.00 on a run with nothing to dispatch: `--skip-claude` on an all-`claude` panel,
    or a draft pass, leaves the metered endpoint resolved and untouched. Approving spend that
    nobody is going to make teaches an operator to answer the question without reading it, which is
    the one thing a spend gate cannot afford.
    """
    connector = connector or {}
    required = requires_approval(connector) and (bool(seats) or bool(judgment))
    block = {
        "connector": connector.get("name"),
        "billing": connector.get("billing"),
        "required": required,
        "granted": bool(granted) if required else None,
        "source": source if required else None,
        "seats": sorted({seat.get("reviewer_id") for seat in seats if seat.get("reviewer_id")}),
    }
    if connector.get("billing") == METERED and not requires_approval(connector):
        # The one case a reader must not have to diff two directories to see: a metered endpoint
        # somebody has switched the gate off for, and which root did it. Keyed on the connector's
        # own posture rather than on `required`, which is also false on a run that dispatched
        # nothing — a run with no seats to gate is not a machine that trusts this endpoint.
        block["gate_disabled_by"] = connector.get("root")
        block["gate_disabled_path"] = connector.get("path")
    return block


def refusal(connector, seats, projection_usd=None, flag="--approve-spend"):
    """The message an ungated metered run refuses with, naming the endpoint, the seats and the flag."""
    connector = connector or {}
    lines = [
        "spend not approved: connector {0!r} is `billing: metered`, so a call on it costs money on "
        "an account, and nothing in this invocation said that is allowed.".format(
            connector.get("name") or "?"),
        "  endpoint: {0}".format(connector.get("base_url") or "?"),
        "  connector file: {0}".format(connector.get("path") or "?"),
    ]
    named = [seat.get("reviewer_id") for seat in seats if seat.get("reviewer_id")]
    if named:
        lines.append("  seats bound to it: {0}".format(", ".join(sorted(named))))
    if projection_usd is not None:
        lines.append("  projected spend: ${0:.2f}".format(projection_usd))
    lines.append(
        "  Pass {0} to allow it. That is a different question from --approve-budget, which answers "
        "\"this projection is over the limit\"; this one answers \"you may spend at all\".".format(flag))
    lines.append(
        "  A machine that trusts this endpoint sets `requires_approval: false` in its own copy of "
        "the connector file, under ~/.config/ensemble-review/connectors/ or the project's "
        ".config/ensemble-review/connectors/.")
    return "\n".join(lines)


def compose(config, connector, registry):
    """The resolved endpoint view every downstream module already takes: a `config_entry`.

    Seating, dispatch and the judge all want one dict carrying `tiers`, `default_tier`, the driver
    `type`, the base URL and the key source. Those used to be one block in `config.json`; they now
    come from three places — the run-wide config, the connector file, and the tiers map derived from
    the model files — and this is where the three are put together. Assembling it once here is what
    keeps `paths.py` the only implementation of the cascade and keeps every consumer unchanged.
    """
    config = config or {}
    connector = connector or {}
    entry = dict(connector)
    entry["connector"] = connector.get("name")
    entry["default_tier"] = config.get("default_tier")
    # Passed through as the config states it, `None` included. A `standard` substituted here would
    # be a config rung that always answers, which makes the persona rung below it unreachable and
    # the hard default below that dead too. The fallback lives at the end of the order instead, in
    # `seating.resolve_effort_level` and `judge.synthesis_effort`, where a reader can see what it
    # falls back *from*.
    entry["default_effort"] = config.get("default_effort")
    entry["tiers"] = registry_tiers(config, connector, registry)
    return entry


def registry_tiers(config, connector, registry):
    from . import registry as registry_lib      # local: registry imports paths, not this module
    return registry_lib.derive_tiers(
        registry,
        family_order=config.get("family_order") or (),
        tier_order=config.get("tier_order") or (),
        connector=(connector or {}).get("name"))


def catalogue_url(connector):
    """Where `refresh_models.py` pulls this endpoint's catalogue from."""
    return (connector or {}).get("catalogue_url")


def available(paths):
    """Every connector name the roots offer, outermost first, each listed once. For the refusal text."""
    names = []
    for root in paths.roots:
        for template in paths_lib.CANDIDATES["connector"]:
            directory = os.path.dirname(os.path.join(root, template))
            if not os.path.isdir(directory):
                continue
            for entry in sorted(os.listdir(directory)):
                if entry.endswith(".json") and entry[:-5] not in names:
                    names.append(entry[:-5])
    return names

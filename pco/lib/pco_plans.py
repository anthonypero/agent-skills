"""
Planning Center Services helpers — service types, plan templates, plans, plan
times. Domain wrappers over pco_api.PCOClient; every path lives under
/services/v2. Song/arrangement helpers live in pco_services.py.

Key API facts (see reference/plans.md):
  - A Plan belongs to a ServiceType. Its date is NOT an attribute: it is
    derived from its PlanTimes (service/rehearsal/other), so creating a plan
    means POST the plan, then POST its plan_times.
  - PlanTemplates are listed at /service_types/<id>/plan_templates. Applying
    one to an existing plan is POST /service_types/<st>/plans/<id>/import_template
    with attributes {plan_id: <template id>, copy_items, copy_people, copy_notes}.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from pco_api import PCOClient

V2 = "/services/v2"


# --- Service types / templates -------------------------------------------------

def get_service_types(client: PCOClient) -> list[dict]:
    return list(client.get_all(f"{V2}/service_types"))


def resolve_service_type(client: PCOClient, ref: str) -> dict:
    """Accept an id or a (case-insensitive, substring) name."""
    types = get_service_types(client)
    for t in types:
        if t["id"] == ref:
            return t
    hits = [t for t in types if ref.lower() in t["attributes"]["name"].lower()]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(f"{t['id']}={t['attributes']['name']!r}" for t in types)
    raise RuntimeError(f"Service type {ref!r} matched {len(hits)} of: {names}")


def get_plan_templates(client: PCOClient, service_type_id: str) -> list[dict]:
    return list(client.get_all(f"{V2}/service_types/{service_type_id}/plan_templates"))


def resolve_template(client: PCOClient, service_type_id: str, ref: str) -> dict:
    """Accept a template id or exact (case-insensitive) name."""
    templates = get_plan_templates(client, service_type_id)
    for t in templates:
        if t["id"] == ref or t["attributes"]["name"].strip().lower() == ref.strip().lower():
            return t
    names = ", ".join(repr(t["attributes"]["name"]) for t in templates)
    raise RuntimeError(f"No plan template {ref!r}; have: {names}")


# --- Plans -----------------------------------------------------------------------

def get_plans(client: PCOClient, service_type_id: str,
              after: dt.date | None = None, before: dt.date | None = None) -> list[dict]:
    """Plans of a service type, ascending by date. Both bounds inclusive."""
    q = "order=sort_date"
    if after:
        q += f"&filter=after&after={after.isoformat()}T00:00:00Z"
    if before:
        q += f"&before={(before + dt.timedelta(days=1)).isoformat()}T00:00:00Z"
    return list(client.get_all(f"{V2}/service_types/{service_type_id}/plans?{q}"))


def plan_date(plan: dict) -> dt.date:
    return dt.date.fromisoformat(plan["attributes"]["sort_date"][:10])


def get_plan_times(client: PCOClient, service_type_id: str, plan_id: str) -> list[dict]:
    return list(client.get_all(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/plan_times"))


def create_plan(client: PCOClient, service_type_id: str, title: str | None = None,
                series_title: str | None = None, public: bool | None = None) -> dict:
    attrs = {k: v for k, v in {"title": title, "series_title": series_title,
                               "public": public}.items() if v is not None}
    body = {"data": {"type": "Plan", "attributes": attrs}}
    return client.post(f"{V2}/service_types/{service_type_id}/plans", body)["data"]


def create_plan_time(client: PCOClient, service_type_id: str, plan_id: str,
                     starts_at: str, ends_at: str, time_type: str,
                     name: str | None = None) -> dict:
    attrs = {"starts_at": starts_at, "ends_at": ends_at, "time_type": time_type}
    if name:
        attrs["name"] = name
    body = {"data": {"type": "PlanTime", "attributes": attrs}}
    return client.post(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/plan_times",
                       body)["data"]


def import_template(client: PCOClient, service_type_id: str, plan_id: str,
                    template_id: str, copy_items: bool = True, copy_people: bool = True,
                    copy_notes: bool = True) -> dict:
    body = {"data": {"type": "PlanImportTemplate", "attributes": {
        "plan_id": template_id, "copy_items": copy_items,
        "copy_people": copy_people, "copy_notes": copy_notes}}}
    return client.post(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/import_template",
                       body)


def get_org_timezone(client: PCOClient) -> ZoneInfo:
    tz = client.get(f"{V2}")["data"]["attributes"].get("time_zone") or "UTC"
    return ZoneInfo(tz)


def shifted_times(times: list[dict], from_date: dt.date, to_date: dt.date,
                  tz: ZoneInfo) -> list[dict]:
    """Copy a plan's times onto a new date, keeping weekday offsets and LOCAL
    clock times (a Thursday 6:30pm rehearsal stays Thursday 6:30pm even
    across a DST change — the API speaks UTC, so shift in the org's zone)."""
    delta = to_date - from_date
    out = []
    for t in times:
        a = t["attributes"]
        def shift(iso: str) -> str:
            local = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz)
            moved = (local + delta).replace(tzinfo=None).replace(tzinfo=tz)
            return moved.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        out.append({"starts_at": shift(a["starts_at"]), "ends_at": shift(a["ends_at"]),
                    "time_type": a["time_type"], "name": a.get("name")})
    return out


# --- Extend: weekly plans through a date -------------------------------------------

def first_sunday_of_month(d: dt.date) -> bool:
    return d.weekday() == 6 and d.day <= 7


def plan_extension(client: PCOClient, service_type_id: str, through: dt.date,
                   communion_rule=first_sunday_of_month,
                   communion_dates: set[dt.date] = frozenset()) -> tuple[dict, list[dict]]:
    """Compute (anchor_plan, [{date, communion}]) for every weekly slot from the
    week after the latest existing plan up to `through`, skipping dates that
    already have a plan. Pure planning — no writes."""
    existing = get_plans(client, service_type_id, after=dt.date.today() - dt.timedelta(days=60))
    if not existing:
        raise RuntimeError("No recent plans to anchor on — create one in PCO first.")
    anchor = existing[-1]
    have = {plan_date(p) for p in existing}
    d = plan_date(anchor) + dt.timedelta(days=7)
    slots = []
    while d <= through:
        if d not in have:
            slots.append({"date": d, "communion": d in communion_dates or communion_rule(d)})
        d += dt.timedelta(days=7)
    return anchor, slots


def extend_plans(client: PCOClient, service_type_id: str, through: dt.date,
                 template_id: str, communion_template_id: str | None,
                 communion_rule=first_sunday_of_month,
                 communion_dates: set[dt.date] = frozenset(),
                 dry_run: bool = False, log=print) -> list[dict]:
    anchor, slots = plan_extension(client, service_type_id, through,
                                   communion_rule, communion_dates)
    anchor_date = plan_date(anchor)
    times = get_plan_times(client, service_type_id, anchor["id"])
    tz = get_org_timezone(client)
    log(f"Anchor: plan {anchor['id']} on {anchor_date} ({len(times)} times). "
        f"{len(slots)} plan(s) to create through {through}.")
    created = []
    for s in slots:
        tpl = communion_template_id if (s["communion"] and communion_template_id) else template_id
        label = "communion" if s["communion"] else "regular"
        log(f"  {s['date']}  {label:9}  template {tpl}" + ("  [dry-run]" if dry_run else ""))
        if dry_run:
            continue
        plan = create_plan(client, service_type_id)
        for t in shifted_times(times, anchor_date, s["date"], tz):
            create_plan_time(client, service_type_id, plan["id"], **t)
        import_template(client, service_type_id, plan["id"], tpl)
        created.append(plan)
    return created

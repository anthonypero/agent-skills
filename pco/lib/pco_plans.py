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


# --- Fill: title / series / scripture onto existing plans ------------------------

def get_plan_items(client: PCOClient, service_type_id: str, plan_id: str) -> list[dict]:
    return list(client.get_all(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/items"))


def update_plan(client: PCOClient, service_type_id: str, plan_id: str, **attrs) -> dict:
    body = {"data": {"type": "Plan", "attributes": attrs}}
    return client.patch(f"{V2}/service_types/{service_type_id}/plans/{plan_id}", body)["data"]


def update_item(client: PCOClient, service_type_id: str, plan_id: str, item_id: str,
                **attrs) -> dict:
    body = {"data": {"type": "Item", "attributes": attrs}}
    return client.patch(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/items/{item_id}",
                        body)["data"]


def parse_sheet_date(s: str) -> dt.date | None:
    """Accept M/D/YYYY (Google Sheets US) or YYYY-MM-DD."""
    s = s.strip()
    try:
        if "/" in s:
            m, d, y = (int(x) for x in s.split("/"))
            return dt.date(y, m, d)
        return dt.date.fromisoformat(s)
    except ValueError:
        return None


def fill_plans(client: PCOClient, service_type_id: str, rows: list[dict],
               scripture_item: str = "Scripture Reading", overwrite: bool = False,
               dry_run: bool = False, log=print) -> int:
    """rows: [{date, title, series, scripture}] (values may be ''). For each
    row with a plan on that date: set plan title/series and the description of
    the first item whose title matches `scripture_item`. Empty fields are
    skipped; filled fields are left alone unless overwrite. Returns count of
    plans changed (or that would change, in dry_run)."""
    by_date = {r["date"]: r for r in rows if r.get("date")}
    if not by_date:
        return 0
    plans = get_plans(client, service_type_id, min(by_date), max(by_date))
    changed = 0
    for plan in plans:
        d = plan_date(plan)
        row = by_date.get(d)
        if not row:
            continue
        a = plan["attributes"]
        plan_attrs = {}
        for field, attr in (("title", "title"), ("series", "series_title")):
            new = (row.get(field) or "").strip()
            if new and (overwrite or not a.get(attr)):
                plan_attrs[attr] = new
        item_change = None
        scripture = (row.get("scripture") or "").strip()
        if scripture:
            for it in get_plan_items(client, service_type_id, plan["id"]):
                if it["attributes"]["title"].strip().lower() == scripture_item.lower():
                    if overwrite or not it["attributes"].get("description"):
                        item_change = (it["id"], scripture)
                    break
            else:
                log(f"  {d}  WARNING: no item titled {scripture_item!r} in plan {plan['id']}")
        if not plan_attrs and not item_change:
            log(f"  {d}  nothing to do")
            continue
        changed += 1
        desc = ", ".join(f"{k}={v!r}" for k, v in plan_attrs.items())
        if item_change:
            desc += f"{', ' if desc else ''}{scripture_item}={item_change[1]!r}"
        log(f"  {d}  {desc}" + ("  [dry-run]" if dry_run else ""))
        if dry_run:
            continue
        if plan_attrs:
            update_plan(client, service_type_id, plan["id"], **plan_attrs)
        if item_change:
            update_item(client, service_type_id, plan["id"], item_change[0],
                        description=item_change[1])
    return changed


# --- Remove an item by title from templates / plans -----------------------------

def get_template_items(client: PCOClient, service_type_id: str, template_id: str) -> list[dict]:
    return list(client.get_all(
        f"{V2}/service_types/{service_type_id}/plan_templates/{template_id}/items"))


def remove_item(client: PCOClient, service_type_id: str, title: str,
                template_ids: list[str] = (), dates: list[dt.date] = (),
                after: dt.date | None = None, before: dt.date | None = None,
                dry_run: bool = False, log=print) -> int:
    """Delete every item whose title matches (case-insensitive) from the
    given templates and from the plans on the given dates and/or in the
    after..before range. Returns count."""
    targets = []  # (label, base_path, items)
    for tid in template_ids:
        base = f"{V2}/service_types/{service_type_id}/plan_templates/{tid}"
        targets.append((f"template {tid}", base, get_template_items(client, service_type_id, tid)))
    plans = {}
    if dates:
        for plan in get_plans(client, service_type_id, min(dates), max(dates)):
            if plan_date(plan) in set(dates):
                plans[plan["id"]] = plan
    if after or before:
        for plan in get_plans(client, service_type_id, after, before):
            plans[plan["id"]] = plan
    for plan in sorted(plans.values(), key=plan_date):
        base = f"{V2}/service_types/{service_type_id}/plans/{plan['id']}"
        targets.append((f"plan {plan_date(plan)}", base,
                        get_plan_items(client, service_type_id, plan["id"])))
    n = 0
    for label, base, items in targets:
        hits = [i for i in items if i["attributes"]["title"].strip().lower() == title.lower()]
        if not hits:
            log(f"  {label}: no item titled {title!r}")
        for it in hits:
            log(f"  {label}: delete item {it['id']} seq {it['attributes']['sequence']} "
                f"{it['attributes']['title']!r}" + ("  [dry-run]" if dry_run else ""))
            if not dry_run:
                client.delete(f"{base}/items/{it['id']}")
            n += 1
    return n

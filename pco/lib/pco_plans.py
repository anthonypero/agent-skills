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


# --- Schedule: put a named person into a team position from a sheet column --------

def plan_leaders(client: PCOClient, service_type_id: str, after: dt.date, before: dt.date,
                 position: str = "Music Director", log=None) -> list[dict]:
    """[{date, plan_id, <position column>}] — who is scheduled into `position`
    on each plan in the range. Feeds `songs plan --leaders`: which Sundays are
    the profile's own to fill (rule 13). One call per plan."""
    rows = []
    plans = get_plans(client, service_type_id, after, before)
    col = position.lower().replace(" ", "_")
    for n, plan in enumerate(plans, 1):
        if log and n % 10 == 0:
            log(f"  ...{n}/{len(plans)} plans")
        members = client.get(f"{V2}/service_types/{service_type_id}/plans/{plan['id']}"
                             f"/team_members?per_page=100")["data"]
        who = [m["attributes"].get("name") or "" for m in members
               if (m["attributes"].get("team_position_name") or "").strip().lower() == position.lower()]
        rows.append({"date": plan_date(plan).isoformat(), "plan_id": plan["id"],
                     col: "; ".join(dict.fromkeys(w for w in who if w))})
    return rows


def get_teams(client: PCOClient, service_type_id: str) -> list[dict]:
    return list(client.get_all(f"{V2}/service_types/{service_type_id}/teams"))


def get_position_roster(client: PCOClient, service_type_id: str, position: str
                        ) -> tuple[dict, dict[str, str]]:
    """Find the team owning `position` (case-insensitive) and return
    (team, {person_id: full_name}) for everyone assigned to that position."""
    for team in get_teams(client, service_type_id):
        rows = client.get(f"{V2}/service_types/{service_type_id}/teams/{team['id']}"
                          f"/person_team_position_assignments?include=person,team_position&per_page=100")
        inc = {(i["type"], i["id"]): i for i in rows.get("included", [])}
        roster = {}
        for a in rows["data"]:
            r = a["relationships"]
            tp = inc[("TeamPosition", r["team_position"]["data"]["id"])]["attributes"]["name"]
            if tp.strip().lower() == position.strip().lower():
                p = inc[("Person", r["person"]["data"]["id"])]
                roster[p["id"]] = p["attributes"]["full_name"]
        if roster:
            return team, roster
    raise RuntimeError(f"No team position {position!r} with assigned people in this service type")


_NAME_PREFIXES = {"rev", "rev.", "dr", "dr.", "pastor", "bishop", "guest", "guest:", "the"}


def _name_tokens(name: str) -> list[str]:
    toks = [t.strip(",.:").lower() for t in name.replace("/", " ").split()]
    return [t for t in toks if t and t not in _NAME_PREFIXES and t != "-"]


def match_person(name: str, roster: dict[str, str]) -> str | None:
    """Person id whose roster full name contains every token of `name`
    (after stripping honorifics), if exactly one does."""
    want = _name_tokens(name)
    if not want:
        return None
    hits = [pid for pid, full in roster.items()
            if all(t in _name_tokens(full) for t in want)]
    return hits[0] if len(hits) == 1 else None


def get_team_members(client: PCOClient, service_type_id: str, plan_id: str) -> list[dict]:
    return list(client.get_all(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/team_members"))


def schedule_person(client: PCOClient, service_type_id: str, plan_id: str, team_id: str,
                    person_id: str, position: str, status: str = "U") -> dict:
    body = {"data": {"type": "PlanPerson",
                     "attributes": {"team_position_name": position, "status": status},
                     "relationships": {"person": {"data": {"type": "Person", "id": person_id}},
                                       "team": {"data": {"type": "Team", "id": team_id}}}}}
    return client.post(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/team_members",
                       body)["data"]


def schedule_from_rows(client: PCOClient, service_type_id: str, rows: list[dict],
                       position: str, status: str = "U", dry_run: bool = False,
                       log=print) -> int:
    """rows: [{date, name}]. For each plan on a row's date whose `position`
    is unfilled, schedule the roster person matching `name`. Names that don't
    match the roster (guests) are skipped. Returns count scheduled."""
    team, roster = get_position_roster(client, service_type_id, position)
    log(f"{position} roster ({team['attributes']['name']}): "
        + ", ".join(sorted(roster.values())))
    by_date = {r["date"]: r for r in rows if r.get("date") and (r.get("name") or "").strip()}
    if not by_date:
        return 0
    n = 0
    for plan in get_plans(client, service_type_id, min(by_date), max(by_date)):
        d = plan_date(plan)
        row = by_date.get(d)
        if not row:
            continue
        current = [m for m in get_team_members(client, service_type_id, plan["id"])
                   if m["attributes"]["team_position_name"].lower() == position.lower()]
        if current:
            log(f"  {d}  {position} already {current[0]['attributes']['name']!r} — skip")
            continue
        pid = match_person(row["name"], roster)
        if not pid:
            log(f"  {d}  {row['name']!r} not on roster — left blank")
            continue
        log(f"  {d}  {position} <- {roster[pid]} ({status})" + ("  [dry-run]" if dry_run else ""))
        if not dry_run:
            schedule_person(client, service_type_id, plan["id"], team["id"], pid, position, status)
        n += 1
    return n


# --- Counterpart rule: "the other one hosts" --------------------------------------

_ABSENT_RE = (r"\b{first}\b\s*(?::|-|–)?\s*(?:@|\bat\b|will not|won'?t|is not|isn'?t|"
              r"sick|ill|away|out\b|off\b|vacation|retreat|conference|not (?:be )?here)")


def is_marked_absent(first_name: str, row_text: str) -> bool:
    import re
    return re.search(_ABSENT_RE.format(first=re.escape(first_name)), row_text, re.I) is not None


def counterpart_name(preacher: str, pair: tuple[str, str], row_text: str,
                     log=print, when: str = "") -> str | None:
    """Given the preacher and a pair of full names, return the other pair
    member's name if they aren't marked absent in row_text; None otherwise."""
    toks = _name_tokens(preacher)
    who = [p for p in pair if all(t in _name_tokens(p) for t in toks)] if toks else []
    if len(who) != 1:
        log(f"  {when}  preacher {preacher!r} is not one of the pair — host left blank")
        return None
    other = pair[0] if who[0] == pair[1] else pair[1]
    if is_marked_absent(other.split()[0], row_text):
        log(f"  {when}  {other} marked absent in sheet — host left blank")
        return None
    return other


# --- Song usage across plans -------------------------------------------------------

def song_usage(client: PCOClient, service_type_id: str, after: dt.date, before: dt.date,
               log=None) -> list[dict]:
    """One row per song item in every plan of the range: date, plan id,
    sequence, song id/title, arrangement, key, length. Header items give the
    section the song sits in (`section`)."""
    rows = []
    plans = get_plans(client, service_type_id, after, before)
    for n, plan in enumerate(plans, 1):
        if log and n % 10 == 0:
            log(f"  ...{n}/{len(plans)} plans")
        page = client.get(f"{V2}/service_types/{service_type_id}/plans/{plan['id']}"
                          f"/items?include=song,arrangement,key&per_page=100")
        inc = {(i["type"], i["id"]): i for i in page.get("included", [])}
        section = None
        for it in page["data"]:
            a = it["attributes"]
            if a["item_type"] == "header":
                section = a["title"]
                continue
            if a["item_type"] != "song":
                continue
            r = it.get("relationships", {})
            song = r.get("song", {}).get("data")
            arr = r.get("arrangement", {}).get("data")
            key = r.get("key", {}).get("data")
            rows.append({
                "date": plan_date(plan).isoformat(), "plan_id": plan["id"],
                "sequence": a["sequence"], "section": section, "title": a["title"],
                "song_id": song["id"] if song else None,
                "arrangement": inc[("Arrangement", arr["id"])]["attributes"]["name"] if arr else None,
                "key": inc[("Key", key["id"])]["attributes"]["starting_key"] if key else a.get("key_name"),
                "length": a.get("length"),
            })
    return rows


# --- Song planning: write a plan CSV's picks into the plans ---------------------

def pick_key(keys: list[dict]) -> dict | None:
    """Rule 14: a key whose name says Congregational wins; else 'Original Key'; else the first."""
    if not keys:
        return None
    for k in keys:
        if "congregational" in (k["attributes"].get("name") or "").lower():
            return k
    for k in keys:
        if "original" in (k["attributes"].get("name") or "").lower():
            return k
    return keys[0]


def song_slots(items: list[dict], placeholder: str = "Song(s)") -> list[dict]:
    """The plan's song slots in service order: items that already hold a song plus
    the untouched `placeholder` items the template left behind."""
    return [i for i in sorted(items, key=lambda i: i["attributes"]["sequence"])
            if i["attributes"]["item_type"] == "song" or (i["attributes"].get("title") or "").strip() == placeholder]


def set_item_song(client: PCOClient, service_type_id: str, plan_id: str, item_id: str,
                  song: dict, arrangement: dict, key: dict | None) -> dict:
    """Turn a plan item into a song item — the same PATCH the UI does when a song is
    dropped onto a placeholder: item_type, title, length and the three relationships."""
    rel = {"song": {"data": {"type": "Song", "id": song["id"]}},
           "arrangement": {"data": {"type": "Arrangement", "id": arrangement["id"]}}}
    if key:
        rel["key"] = {"data": {"type": "Key", "id": key["id"]}}
    body = {"data": {"type": "Item", "id": item_id,
                     "attributes": {"title": song["attributes"]["title"],
                                    "length": arrangement["attributes"].get("length") or 0},
                     "relationships": rel}}
    return client.patch(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/items/{item_id}", body)["data"]


def clear_item_song(client: PCOClient, service_type_id: str, plan_id: str, item_id: str,
                    placeholder: str = "Song(s)", length: int = 300) -> dict:
    """Undo set_item_song: null the three relationships and restore the placeholder title.
    PCO flips item_type back to `item` on its own."""
    body = {"data": {"type": "Item", "id": item_id, "attributes": {"title": placeholder, "length": length},
                     "relationships": {"song": {"data": None}, "arrangement": {"data": None}, "key": {"data": None}}}}
    return client.patch(f"{V2}/service_types/{service_type_id}/plans/{plan_id}/items/{item_id}", body)["data"]


def apply_plan_songs(client: PCOClient, service_type_id: str, rows: list[dict], library: list[dict],
                     *, overwrite: bool = False, clear: bool = False, dry_run: bool = False,
                     placeholder: str = "Song(s)", log=print) -> dict:
    """Write the `song` column of a `songs plan` CSV into the plans.

    rows: the CSV (date, slot, song, ...). Only rows with a song are touched. Slot n
    is the n-th song slot of that date's plan in service order (`song_slots`), so the
    template's four `Song(s)` items map to slots 1–4 whether or not some are already
    filled. A slot that already holds a song is skipped unless `overwrite`. Songs are
    With `clear`, a row whose `song` is blank empties a filled slot back to the placeholder
    (rows with a song still need `overwrite` to replace). Songs are
    resolved by exact title against `library` (an export_library() dump, for the id);
    arrangement = the song's first arrangement, key = `pick_key` over the live keys.
    Returns {"set": n, "cleared": n, "skipped": n, "errors": [..]}.
    """
    by_title = {s["title"]: s for s in library}
    wanted = {}
    for r in rows:
        title = (r.get("song") or "").strip()
        d = parse_sheet_date(r.get("date") or "")
        if d and (title or clear):
            wanted.setdefault(d, {})[int(r["slot"])] = title
    if not wanted:
        return {"set": 0, "cleared": 0, "skipped": 0, "errors": ["no rows with a song"]}
    plans = {plan_date(p): p for p in get_plans(client, service_type_id, min(wanted), max(wanted))}
    out = {"set": 0, "cleared": 0, "skipped": 0, "errors": []}
    cache = {}
    for d in sorted(wanted):
        plan = plans.get(d)
        if not plan:
            out["errors"].append(f"{d}: no plan"); log(f"  {d}  no plan — skipped"); continue
        slots = song_slots(get_plan_items(client, service_type_id, plan["id"]), placeholder)
        for n, title in sorted(wanted[d].items()):
            if n > len(slots):
                out["errors"].append(f"{d}: slot {n} but plan has {len(slots)} song slots"); log(f"  {d}  slot {n}: only {len(slots)} slots — skipped"); continue
            item = slots[n - 1]
            cur = item["attributes"].get("title")
            if not title:
                if item["attributes"]["item_type"] == "song":
                    log(f"  {d}  slot {n}: clear {cur!r}" + ("  [dry-run]" if dry_run else ""))
                    if not dry_run:
                        clear_item_song(client, service_type_id, plan["id"], item["id"], placeholder)
                    out["cleared"] += 1
                continue
            if item["attributes"]["item_type"] == "song" and cur == title:
                out["skipped"] += 1; continue
            if item["attributes"]["item_type"] == "song" and not overwrite:
                out["skipped"] += 1; log(f"  {d}  slot {n}: already {cur!r} — skip (use --overwrite)"); continue
            s = by_title.get(title)
            if not s:
                out["errors"].append(f"{d}: {title!r} not in library"); log(f"  {d}  slot {n}: {title!r} not in library — skipped"); continue
            if s["id"] not in cache:
                arrs = client.get(f"{V2}/songs/{s['id']}/arrangements?per_page=100")["data"]
                if not arrs:
                    out["errors"].append(f"{d}: {title!r} has no arrangement"); log(f"  {d}  slot {n}: {title!r} has no arrangement — skipped"); continue
                arr = arrs[0]
                keys = client.get(f"{V2}/songs/{s['id']}/arrangements/{arr['id']}/keys?per_page=100")["data"]
                cache[s["id"]] = ({"id": s["id"], "attributes": {"title": s["title"]}}, arr, pick_key(keys))
            song, arr, key = cache[s["id"]]
            kd = f"{key['attributes'].get('starting_key')} ({key['attributes'].get('name') or 'unnamed'})" if key else "no key"
            log(f"  {d}  slot {n}: {title}  [{arr['attributes']['name']}, {kd}]" + ("  [dry-run]" if dry_run else ""))
            if not dry_run:
                set_item_song(client, service_type_id, plan["id"], item["id"], song, arr, key)
            out["set"] += 1
    return out

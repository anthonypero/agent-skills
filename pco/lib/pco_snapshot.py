"""
Plan snapshots — "has this plan changed since I last looked, and how?"

PCO's public API exposes only a plan's last editor (`updated_by`) and
per-record `updated_at` stamps; the full edit-history footer in the UI is not
available. So we keep our own history: fetch a plan's full state (plan, items
with song/arrangement/key, notes, team members, plan times), normalize it to
the fields a human cares about, and store it only when it differs from the
previous snapshot. Diffing two snapshots then answers "what changed".

Layout:  <dir>/<service_type_id>/<plan_id>/<fetched-at>.json
         <dir>/<service_type_id>/<plan_id>/checked   (last fetch time, even if unchanged)
         <dir>/index.json                            (plan id -> date/title/type for listing)

Retention: at most MAX_VERSIONS per plan (oldest dropped); plan folders whose
service date is more than KEEP_DAYS in the past are removed on each run.
"""

import datetime as dt
import json
import os
import shutil

from pco_api import PCOClient
import pco_plans as P

V2 = "/services/v2"
MAX_VERSIONS = 20
KEEP_DAYS = 365
CHECKED = "checked"


# --- Fetch -------------------------------------------------------------------------

def _included_index(page: dict) -> dict[tuple[str, str], dict]:
    return {(i["type"], i["id"]): i for i in page.get("included", [])}


def _rel_id(rec: dict, name: str) -> tuple[str, str] | None:
    d = (rec.get("relationships", {}).get(name) or {}).get("data")
    return (d["type"], d["id"]) if d else None


def fetch_plan_state(client: PCOClient, service_type: dict, plan_id: str) -> dict:
    """Everything about one plan, with included records resolved to names."""
    st_id = service_type["id"]
    base = f"{V2}/service_types/{st_id}/plans/{plan_id}"

    page = client.get(f"{base}?include=updated_by,created_by")
    inc = _included_index(page)
    plan = page["data"]
    for rel in ("updated_by", "created_by"):
        key = _rel_id(plan, rel)
        plan["attributes"][f"{rel}_name"] = (inc[key]["attributes"].get("full_name")
                                             or inc[key]["attributes"].get("name")) if key in inc else None

    items = []
    url = f"{base}/items?include=song,arrangement,key&per_page=100"
    while url:
        pg = client.get(url)
        inc = _included_index(pg)
        for it in pg["data"]:
            a = it["attributes"]
            for rel, field in (("song", "title"), ("arrangement", "name"), ("key", "name")):
                key = _rel_id(it, rel)
                a[f"{rel}_name"] = inc[key]["attributes"].get(field) if key in inc else None
            items.append(it)
        url = pg.get("links", {}).get("next")

    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "service_type": {"id": st_id, "name": service_type["attributes"]["name"]},
        "plan": plan,
        "items": items,
        "notes": list(client.get_all(f"{base}/notes")),
        "team_members": list(client.get_all(f"{base}/team_members")),
        "plan_times": list(client.get_all(f"{base}/plan_times")),
    }


# --- Normalize ---------------------------------------------------------------------

def normalize(state: dict) -> dict:
    """The comparable view: only fields whose change a person would call an edit."""
    pa = state["plan"]["attributes"]
    out = {
        "plan": {
            "title": pa.get("title"), "series_title": pa.get("series_title"),
            "dates": pa.get("dates"), "public": pa.get("public"),
            "rehearsal_times": pa.get("rehearsal_times"), "service_times": pa.get("service_times"),
        },
        "items": {}, "team": {}, "notes": {}, "times": {},
    }
    for it in state["items"]:
        a = it["attributes"]
        out["items"][it["id"]] = {
            "seq": a.get("sequence"), "type": a.get("item_type"), "title": a.get("title"),
            "song": a.get("song_name"), "arrangement": a.get("arrangement_name"),
            "key": a.get("key_name"), "length": a.get("length"),
            "description": a.get("description"), "position": a.get("service_position"),
        }
    for tm in state["team_members"]:
        a = tm["attributes"]
        out["team"][tm["id"]] = {
            "name": a.get("name"), "position": a.get("team_position_name"),
            "status": a.get("status"), "decline_reason": a.get("decline_reason"),
            "notes": a.get("notes"),
        }
    for n in state["notes"]:
        a = n["attributes"]
        out["notes"][n["id"]] = {"category": a.get("category_name"), "content": a.get("content")}
    for t in state["plan_times"]:
        a = t["attributes"]
        out["times"][t["id"]] = {"name": a.get("name"), "type": a.get("time_type"),
                                 "starts": a.get("starts_at"), "ends": a.get("ends_at")}
    return out


# --- Diff --------------------------------------------------------------------------

_STATUS = {"C": "confirmed", "U": "unconfirmed", "D": "declined"}


def _item_label(v: dict) -> str:
    s = f"#{v['seq']} {v['title'] or '(untitled)'}"
    if v.get("song"):
        s += f" [{v['song']}"
        if v.get("arrangement"):
            s += f" / {v['arrangement']}"
        if v.get("key"):
            s += f" in {v['key']}"
        s += "]"
    return s


def _team_label(v: dict) -> str:
    return f"{v['name']} ({v['position']}) {_STATUS.get(v['status'], v['status'])}"


def _note_label(v: dict) -> str:
    return f"{v['category']}: {(v['content'] or '').strip()[:80]}"


def _time_label(v: dict) -> str:
    return f"{v['type']} {v['name'] or ''} {v['starts']}".strip()


_LABEL = {"items": _item_label, "team": _team_label, "notes": _note_label, "times": _time_label}


def diff(old: dict, new: dict) -> list[str]:
    """Human lines describing how normalized view `new` differs from `old`."""
    lines = []
    for k, nv in new["plan"].items():
        ov = old["plan"].get(k)
        if ov != nv:
            lines.append(f"plan {k}: {ov!r} -> {nv!r}")
    for section, label in _LABEL.items():
        o, n = old.get(section, {}), new.get(section, {})
        for id_ in n.keys() - o.keys():
            lines.append(f"+ {section[:-1] if section != 'team' else 'team'}: {label(n[id_])}")
        for id_ in o.keys() - n.keys():
            lines.append(f"- {section[:-1] if section != 'team' else 'team'}: {label(o[id_])}")
        for id_ in o.keys() & n.keys():
            if o[id_] == n[id_]:
                continue
            changed = [f"{f}: {o[id_].get(f)!r} -> {n[id_].get(f)!r}"
                       for f in n[id_] if o[id_].get(f) != n[id_].get(f)]
            if section == "items" and set(f.split(":")[0] for f in changed) == {"seq"}:
                lines.append(f"~ item moved: {label(n[id_])} (was #{o[id_]['seq']})")
            else:
                lines.append(f"~ {section[:-1] if section != 'team' else 'team'}: {label(n[id_])} — "
                             + "; ".join(changed))
    return lines


# --- Store -------------------------------------------------------------------------

def _plan_dir(root: str, st_id: str, plan_id: str) -> str:
    return os.path.join(root, st_id, plan_id)


def versions(root: str, st_id: str, plan_id: str) -> list[str]:
    """Snapshot file paths for a plan, oldest first."""
    d = _plan_dir(root, st_id, plan_id)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _stamp(path: str) -> str:
    return os.path.basename(path)[:-5].replace("_", ":")


def last_checked(root: str, st_id: str, plan_id: str) -> str | None:
    p = os.path.join(_plan_dir(root, st_id, plan_id), CHECKED)
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return None


def _write_index(root: str, st: dict, plan: dict):
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "index.json")
    idx = load(path) if os.path.isfile(path) else {}
    a = plan["attributes"]
    idx[plan["id"]] = {"service_type_id": st["id"], "service_type": st["attributes"]["name"],
                       "date": a["sort_date"][:10], "title": a.get("title"),
                       "series": a.get("series_title")}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1, sort_keys=True)


def store(root: str, state: dict) -> tuple[str | None, list[str], str | None]:
    """Save `state` if its normalized view differs from the latest stored one.

    Returns (saved_path_or_None, diff_lines, previous_snapshot_path_or_None).
    Always records the check time."""
    st_id, plan_id = state["service_type"]["id"], state["plan"]["id"]
    d = _plan_dir(root, st_id, plan_id)
    os.makedirs(d, exist_ok=True)
    prev = versions(root, st_id, plan_id)
    prev_path = prev[-1] if prev else None
    new_norm = normalize(state)
    lines = []
    saved = None
    if prev_path:
        lines = diff(normalize(load(prev_path)), new_norm)
    if not prev_path or lines:
        saved = os.path.join(d, state["fetched_at"].replace(":", "_") + ".json")
        with open(saved, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
        for old in (prev + [saved])[:-MAX_VERSIONS]:
            os.remove(old)
    with open(os.path.join(d, CHECKED), "w", encoding="utf-8") as f:
        f.write(state["fetched_at"])
    return saved, lines, prev_path


def prune(root: str, today: dt.date | None = None) -> list[str]:
    """Remove plan folders whose service date is older than KEEP_DAYS."""
    today = today or dt.date.today()
    path = os.path.join(root, "index.json")
    if not os.path.isfile(path):
        return []
    idx = load(path)
    gone = []
    for pid, meta in list(idx.items()):
        if (today - dt.date.fromisoformat(meta["date"])).days > KEEP_DAYS:
            shutil.rmtree(_plan_dir(root, meta["service_type_id"], pid), ignore_errors=True)
            gone.append(f"{meta['date']} {pid}")
            del idx[pid]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(idx, f, indent=1, sort_keys=True)
    return gone


# --- Top-level operations ------------------------------------------------------------

def snapshot_plans(client: PCOClient, root: str, st: dict, after: dt.date, before: dt.date,
                   log=lambda m: None) -> list[dict]:
    """Fetch every plan in range, store changed ones, return per-plan reports."""
    reports = []
    for pl in P.get_plans(client, st["id"], after, before):
        state = fetch_plan_state(client, st, pl["id"])
        _write_index(root, st, pl)
        saved, lines, prev = store(root, state)
        pa = state["plan"]["attributes"]
        reports.append({
            "plan_id": pl["id"], "date": pa["sort_date"][:10], "title": pa.get("title"),
            "updated_at": pa.get("updated_at"), "updated_by": pa.get("updated_by_name"),
            "first": prev is None, "changed": bool(lines), "lines": lines,
            "previous": _stamp(prev) if prev else None,
        })
        log(f"{pa['sort_date'][:10]} {pl['id']}: "
            + ("first snapshot" if prev is None else f"{len(lines)} change(s)" if lines else "unchanged"))
    for g in prune(root):
        log(f"pruned {g}")
    return reports


def changes_since(root: str, st_id: str, plan_id: str, since: dt.datetime | None = None
                  ) -> tuple[str | None, str | None, list[str]]:
    """Offline: diff the latest snapshot against the newest one at or before
    `since` (default: the previous snapshot). Returns (old_stamp, new_stamp, lines)."""
    vs = versions(root, st_id, plan_id)
    if len(vs) < 2:
        return (None, _stamp(vs[-1]) if vs else None, [])
    new = vs[-1]
    if since is None:
        old = vs[-2]
    else:
        cutoff = since.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()
        older = [v for v in vs if _stamp(v) <= cutoff]
        if not older:
            old = vs[0]          # everything we have is newer than `since`
        elif older[-1] == new:
            return (_stamp(new), _stamp(new), [])
        else:
            old = older[-1]
    return (_stamp(old), _stamp(new), diff(normalize(load(old)), normalize(load(new))))

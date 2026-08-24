"""
Planning Center Services helpers — songs, arrangements, keys, attachments.
Domain wrappers over pco_api.PCOClient; every path lives under /services/v2.

The Services song hierarchy (and where things attach):

    Song                          metadata: title, author, ccli_number, copyright, themes
      └─ Arrangement              a genuinely different chart; attrs: name,
         │                        chord_chart, chord_chart_key, bpm
         └─ Key                   attrs: name, starting_key (e.g. 'Bb')

Attachments exist at ALL THREE levels. Audio belongs at the arrangement or
key level — a song-level attachment shows up as a generic "file" across every
arrangement instead of as that arrangement's playable reference recording.
See reference/services.md in this skill for the full lessons list.
"""

import sys

from pco_api import PCOClient

V2 = "/services/v2"


# --- Attachable paths ----------------------------------------------------------

def song_path(song_id: str) -> str:
    return f"{V2}/songs/{song_id}"


def arrangement_path(song_id: str, arrangement_id: str) -> str:
    return f"{song_path(song_id)}/arrangements/{arrangement_id}"


def key_path(song_id: str, arrangement_id: str, key_id: str) -> str:
    return f"{arrangement_path(song_id, arrangement_id)}/keys/{key_id}"


# --- Songs / arrangements / keys ------------------------------------------------

def iter_songs(client: PCOClient):
    """Yield every Song resource in the account."""
    yield from client.get_all(f"{V2}/songs")


def get_song(client: PCOClient, song_id: str) -> dict:
    return client.get(song_path(song_id))["data"]


def create_song(client: PCOClient, title: str, author: str = "",
                ccli_number: str = "", copyright_: str = "") -> dict:
    """POST a new Song. PCO auto-creates a default arrangement (sometimes
    asynchronously — poll get_arrangements briefly after creating)."""
    attrs: dict = {"title": title}
    if author:
        attrs["author"] = author
    if ccli_number:
        attrs["ccli_number"] = ccli_number
    if copyright_:
        attrs["copyright"] = copyright_
    return client.post(f"{V2}/songs", {"data": {"type": "Song", "attributes": attrs}})


def get_arrangements(client: PCOClient, song_id: str) -> list[dict]:
    """All Arrangement resources for a song."""
    return client.get(f"{song_path(song_id)}/arrangements")["data"]


def get_arrangement(client: PCOClient, song_id: str, arrangement_id: str) -> dict:
    """One Arrangement resource (attributes include chord_chart)."""
    return client.get(arrangement_path(song_id, arrangement_id))["data"]


def create_arrangement(client: PCOClient, song_id: str, name: str) -> dict:
    """POST a new (empty) Arrangement onto a song."""
    body = {"data": {"type": "Arrangement", "attributes": {"name": name}}}
    return client.post(f"{song_path(song_id)}/arrangements", body)


def update_chord_chart(client: PCOClient, song_id: str, arrangement_id: str,
                       chord_chart: str) -> dict:
    """PATCH an arrangement's chord chart text (PCO 'Lyrics & Chords' format —
    see reference/services.md for its dialect: TRANSPOSE KEY +n, PAGE_BREAK,
    ALL-CAPS section headings)."""
    body = {"data": {"type": "Arrangement",
                     "attributes": {"chord_chart": chord_chart}}}
    return client.patch(arrangement_path(song_id, arrangement_id), body)


def update_arrangement(client: PCOClient, song_id: str, arrangement_id: str,
                       attrs: dict) -> dict:
    """PATCH arbitrary Arrangement attributes (chord_chart, chord_chart_key, bpm, ...)."""
    body = {"data": {"type": "Arrangement", "attributes": attrs}}
    return client.patch(arrangement_path(song_id, arrangement_id), body)


def get_keys(client: PCOClient, song_id: str, arrangement_id: str) -> list[dict]:
    """All Key resources of an arrangement."""
    return client.get(f"{arrangement_path(song_id, arrangement_id)}/keys")["data"]


def create_key(client: PCOClient, song_id: str, arrangement_id: str,
               key_name: str) -> dict:
    """POST a new Key (e.g. 'Bb') onto an arrangement."""
    body = {"data": {"type": "Key", "attributes": {
        "name": key_name, "starting_key": key_name}}}
    return client.post(f"{arrangement_path(song_id, arrangement_id)}/keys", body)


# --- Attachments ----------------------------------------------------------------
# attachable_path is one of song_path() / arrangement_path() / key_path().

def get_attachments(client: PCOClient, attachable_path: str) -> list[dict]:
    """All Attachment resources on an attachable."""
    return client.get(f"{attachable_path}/attachments")["data"]


def create_link_attachment(client: PCOClient, attachable_path: str,
                           remote_link: str, filename: str) -> dict:
    """POST a link attachment (a URL, not an upload) to an attachable.

    NOTE: filename must end in .mp3 for PCO to mark the attachment
    streamable (filetype=audio) so it plays in the PCO/Music Stand player.
    Idempotency is the caller's job — match existing attachments on their
    'remote_link' attribute before creating.
    """
    body = {"data": {"type": "Attachment", "attributes": {
        "remote_link": remote_link, "filename": filename}}}
    return client.post(f"{attachable_path}/attachments", body)


# --- Tags and library export ------------------------------------------------------

def get_tag_groups(client: PCOClient) -> list[dict]:
    """Tag groups with their tags inlined as [{id, name}] under 'tags'."""
    page = client.get(f"{V2}/tag_groups?include=tags&per_page=100")
    tags = {t["id"]: t["attributes"]["name"] for t in page.get("included", [])}
    out = []
    for g in page["data"]:
        a = g["attributes"]
        ids = [r["id"] for r in g["relationships"]["tags"]["data"]]
        out.append({"id": g["id"], "name": a["name"], "tags_for": a.get("tags_for"),
                    "allow_multiple": a.get("allow_multiple_selections"),
                    "required": bool(a.get("required")),
                    "tags": sorted(({"id": i, "name": tags[i]} for i in ids), key=lambda t: t["name"])})
    return out


def get_song_tags(client: PCOClient, song_id: str) -> list[str]:
    return [t["attributes"]["name"] for t in client.get(f"{song_path(song_id)}/tags?per_page=100")["data"]]


def get_arrangement_tags(client: PCOClient, song_id: str, arrangement_id: str) -> list[str]:
    return [t["attributes"]["name"] for t in
            client.get(f"{arrangement_path(song_id, arrangement_id)}/tags?per_page=100")["data"]]


def get_attachment_names(client: PCOClient, attachable_path: str) -> list[str]:
    return [a["attributes"].get("filename") or "" for a in get_attachments(client, attachable_path)]


def export_library(client: PCOClient, include_hidden: bool = False, log=None,
                   lyrics: bool = True, attachments: bool = True) -> list[dict]:
    """Every song with its tags, and every arrangement with its tags, keys,
    lyrics and attachment filenames (song-level attachments too).
    Listing endpoints ignore `include`, so this is ~3 calls per song plus one
    per arrangement; expect minutes for a few hundred songs (rate-limited)."""
    songs = []
    for n, s in enumerate(iter_songs(client), 1):
        a = s["attributes"]
        if a.get("hidden") and not include_hidden:
            continue
        if log and n % 25 == 0:
            log(f"  ...{n} songs")
        arrs_page = client.get(f"{song_path(s['id'])}/arrangements?include=keys&per_page=100")
        keys_by_id = {k["id"]: k["attributes"] for k in arrs_page.get("included", [])
                      if k["type"] == "Key"}
        arrangements = []
        for arr in arrs_page["data"]:
            aa = arr["attributes"]
            key_ids = [r["id"] for r in arr.get("relationships", {}).get("keys", {}).get("data", [])]
            arrangements.append({
                "id": arr["id"], "name": aa.get("name"), "bpm": aa.get("bpm"),
                "length": aa.get("length"), "meter": aa.get("meter"),
                "chord_chart_key": aa.get("chord_chart_key"),
                "has_chord_chart": bool(aa.get("chord_chart")),
                "sequence": aa.get("sequence") or [],
                "lyrics": (aa.get("lyrics") or "") if lyrics else None,
                "attachments": get_attachment_names(client, arrangement_path(s["id"], arr["id"])) if attachments else None,
                "keys": [{"starting": keys_by_id[k].get("starting_key"),
                          "ending": keys_by_id[k].get("ending_key"),
                          "name": keys_by_id[k].get("name")} for k in key_ids if k in keys_by_id],
                "tags": get_arrangement_tags(client, s["id"], arr["id"]),
            })
        songs.append({
            "id": s["id"], "title": a.get("title"), "author": a.get("author"),
            "ccli_number": a.get("ccli_number"), "themes": a.get("themes"),
            "hidden": a.get("hidden"), "last_scheduled_at": a.get("last_scheduled_at"),
            "notes": a.get("notes"), "tags": get_song_tags(client, s["id"]),
            "copyright": a.get("copyright"), "admin": a.get("admin"),
            "attachments": get_attachment_names(client, song_path(s["id"])) if attachments else None,
            "arrangements": arrangements,
        })
    return songs


# --- Tag writes -----------------------------------------------------------------------

class TagIndex:
    """Name → tag lookup built from tag groups. Names are unique within a
    scope ('song' / 'arrangement'); a name may be qualified 'Group:Tag'."""

    def __init__(self, client: PCOClient):
        self.groups = get_tag_groups(client)
        self.by_id = {}
        for g in self.groups:
            for t in g["tags"]:
                self.by_id[t["id"]] = {"id": t["id"], "name": t["name"], "group": g["name"],
                                       "group_id": g["id"], "scope": g["tags_for"],
                                       "multiple": bool(g["allow_multiple"])}

    def resolve(self, name: str, scope: str) -> dict:
        group = None
        if ":" in name:
            group, name = (x.strip() for x in name.split(":", 1))
        hits = [t for t in self.by_id.values() if t["scope"] == scope
                and t["name"].lower() == name.lower()
                and (group is None or t["group"].lower() == group.lower())]
        if len(hits) != 1:
            have = sorted(f"{t['group']}:{t['name']}" for t in self.by_id.values() if t["scope"] == scope)
            raise RuntimeError(f"Tag {name!r} matched {len(hits)} {scope} tags; have: {', '.join(have)}")
        return hits[0]


def _assign_tags(client: PCOClient, path: str, tag_ids: list[str]) -> None:
    body = {"data": {"type": "TagAssignment", "attributes": {}, "relationships": {
        "tags": {"data": [{"type": "Tag", "id": i} for i in tag_ids]}}}}
    client.post(f"{path}/assign_tags", body)


def retag(client: PCOClient, index: TagIndex, path: str, scope: str,
          add: list[str] = (), remove: list[str] = (), dry_run: bool = False) -> tuple[list, list]:
    """Add/remove tags by name on a song (scope 'song') or arrangement
    (scope 'arrangement') at `path`. Adding a tag from a single-select group
    drops that group's other tag. Returns (before_names, after_names)."""
    current = [t["id"] for t in client.get(f"{path}/tags?per_page=100")["data"]]
    wanted = list(current)
    for name in remove:
        t = index.resolve(name, scope)
        wanted = [i for i in wanted if i != t["id"]]
    for name in add:
        t = index.resolve(name, scope)
        if not t["multiple"]:
            wanted = [i for i in wanted if index.by_id.get(i, {}).get("group_id") != t["group_id"]]
        if t["id"] not in wanted:
            wanted.append(t["id"])
    names = lambda ids: sorted(index.by_id[i]["name"] for i in ids if i in index.by_id)
    if wanted != current and not dry_run:
        _assign_tags(client, path, wanted)
    return names(current), names(wanted)


def find_song(client: PCOClient, ref: str) -> dict:
    """Song by id or exact (case-insensitive) title."""
    if ref.isdigit():
        return get_song(client, ref)
    import urllib.parse
    page = client.get(f"{V2}/songs?where[title]={urllib.parse.quote(ref)}&per_page=25")
    hits = [s for s in page["data"] if s["attributes"]["title"].strip().lower() == ref.strip().lower()]
    if len(hits) != 1:
        raise RuntimeError(f"Song {ref!r} matched {len(hits)} songs: "
                           + ", ".join(f"{s['id']}={s['attributes']['title']!r}" for s in page["data"]))
    return hits[0]


def find_arrangement(client: PCOClient, song_id: str, ref: str | None) -> dict:
    arrs = get_arrangements(client, song_id)
    if ref is None:
        if len(arrs) == 1:
            return arrs[0]
        raise RuntimeError("Song has several arrangements; name one: "
                           + ", ".join(f"{a['id']}={a['attributes']['name']!r}" for a in arrs))
    hits = [a for a in arrs if a["id"] == ref or a["attributes"]["name"].strip().lower() == ref.strip().lower()]
    if len(hits) != 1:
        raise RuntimeError(f"Arrangement {ref!r} matched {len(hits)}: "
                           + ", ".join(f"{a['id']}={a['attributes']['name']!r}" for a in arrs))
    return hits[0]


def audit_library(library: list[dict], groups: list[dict]) -> dict:
    """From an export_library() dump: per tag group, which songs/arrangements
    have no tag from that group. Returns {group_name: [labels]}."""
    missing = {}
    for g in groups:
        names = {t["name"] for t in g["tags"]}
        gaps = []
        if g["tags_for"] == "song":
            gaps = [f"{s['id']} {s['title']}" for s in library if not names & set(s["tags"])]
        elif g["tags_for"] == "arrangement":
            gaps = [f"{s['id']} {s['title']} / {a['name']}" for s in library
                    for a in s["arrangements"] if not names & set(a["tags"])]
        else:
            continue
        missing[g["name"]] = gaps
    return missing


# --- song mix (usage buckets vs a target) ------------------------------------

def advent_windows(after, before) -> list[tuple]:
    """(Advent 1, Epiphany Jan 6) date ranges overlapping [after, before].
    Advent 1 is the 4th Sunday before Christmas (Nov 27 – Dec 3)."""
    import datetime as dt
    out = []
    for y in range(after.year - 1, before.year + 1):
        xmas = dt.date(y, 12, 25)
        advent1 = xmas - dt.timedelta(days=(xmas.weekday() + 1) % 7 or 7) - dt.timedelta(weeks=3)
        end = dt.date(y + 1, 1, 6)
        if advent1 <= before and end >= after:
            out.append((advent1, end))
    return out


def mix_report(usage: list[dict], library: list[dict], after, before, *,
               new_threshold: int = 4, new_months: int = 24, ccli_tag: str = "CCLI Top 100",
               exclude: str | None = None, exclude_tags: tuple[str, ...] = (),
               exclude_ranges: list[tuple] = ()) -> dict:
    """Bucket every song slot in [after, before] as ccli / new / historical.

    `usage` is the full song-usage history (all dates, not just the window) — the
    new/historical call needs cumulative counts. Per slot, in date order:
      ccli        song carries `ccli_tag` today (the tag is current, not historical)
      new         uses through this slot <= new_threshold AND first use within new_months
      historical  everything else
    Slots with no song id, whose title matches `exclude`, or whose song carries any of
    `exclude_tags` (seasonal songs — they never age out under a count rule), or that fall
    inside any of `exclude_ranges` (e.g. Advent → Epiphany, planned separately) are dropped
    and counted; excluded slots still accrue history so nothing else is affected.
    Returns {'slots': [...], 'totals': {bucket: n}, 'by_section': {section: {bucket: n}},
             'songs': {song_id: {...}}, 'dropped': n}.
    """
    import datetime as dt
    import re
    from collections import Counter, defaultdict
    tags = {s["id"]: set(s.get("tags") or []) for s in library}
    ex = re.compile(exclude, re.I) if exclude else None
    rows = sorted((r for r in usage if r.get("song_id")), key=lambda r: (r["date"], int(r["sequence"] or 0)))
    seen: dict[str, list] = defaultdict(list)          # song_id -> dates used (all history)
    slots, dropped = [], 0
    totals, by_section = Counter(), defaultdict(Counter)
    songs: dict[str, dict] = {}
    for r in rows:
        d = dt.date.fromisoformat(r["date"])
        sid = r["song_id"]
        seen[sid].append(d)
        if not (after <= d <= before):
            continue
        if ((ex and ex.search(r["title"] or "")) or (tags.get(sid, set()) & set(exclude_tags))
                or any(a <= d <= b for a, b in exclude_ranges)):
            dropped += 1
            continue
        n = len(seen[sid]); first = seen[sid][0]
        recent = first >= d - dt.timedelta(days=30.44 * new_months)
        if ccli_tag in tags.get(sid, ()):
            b = "ccli"
        elif n <= new_threshold and recent:
            b = "new"
        else:
            b = "historical"
        slots.append({**r, "bucket": b, "uses_through": n, "first_used": first.isoformat()})
        totals[b] += 1; by_section[r.get("section") or "?"][b] += 1
        s = songs.setdefault(sid, {"title": r["title"], "buckets": Counter(), "slots": 0,
                                   "uses_total": 0, "first_used": first.isoformat()})
        s["buckets"][b] += 1; s["slots"] += 1
    for sid, s in songs.items():
        s["uses_total"] = len(seen[sid]); s["last_used"] = seen[sid][-1].isoformat()
    return {"slots": slots, "totals": dict(totals), "by_section": {k: dict(v) for k, v in by_section.items()},
            "songs": songs, "dropped": dropped}


# --- song lifecycle (Queued -> Introducing -> Rotation -> Dormant · Retired) ---

LIFECYCLE_TAGS = ("Queued", "Introducing", "Rotation", "Dormant", "Retired")


def lifecycle_states(usage: list[dict], library: list[dict], as_of, *,
                     new_threshold: int = 4, dormant_months: int = 24, intro_weeks: int = 8,
                     seasonal_tags: tuple[str, ...] = ("Christmas", "Easter", "Patriotic"),
                     skip_intro_arr_tags: tuple[str, ...] = ("Modernized Hymn",),
                     group: str = "Lifecycle",
                     lifecycle_tags: tuple[str, ...] = LIFECYCLE_TAGS,
                     current_tag: str | None = None) -> dict:
    """Compute each song's lifecycle state from its usage history. Offline:
    `usage` is a full-history song-usage CSV (dicts with date/song_id) for the
    one service type that drives the lifecycle, `library` an export_library()
    dump (for today's tags). Uses = DISTINCT plan dates <= `as_of`; future
    plans in the CSV are ignored.

    States, in decision order (exactly one per song):
      Retired      already tagged so by a human — never computed away
      Queued       already tagged so and still unused; its first use hands the
                   song to the rules below (normally -> Introducing)
      Dormant      never used
      seasonal     a song tagged with any of `seasonal_tags` (a count rule would
                   leave it looking new forever), or any of whose arrangements
                   carries one of `skip_intro_arr_tags` (a modernized hymn is
                   known repertoire, not a new song), skips Introducing:
                   Rotation if used within `dormant_months`, else Dormant
      Introducing  uses <= `new_threshold` AND last use within `intro_weeks` — an
                   ACTIVE intro run (3-of-4 + a follow-up due 4–8 weeks later).
                   A low-count song not sung for longer than that has finished
                   (or abandoned) its run and is simply a rarely-used Rotation
                   song; a human retires it if it didn't work.
      Rotation     used within `dormant_months`
      Dormant      last use older than `dormant_months`

    Returns {'songs': {id: {...}}, 'counts': {state: n}, 'transitions':
    {'from -> to': n}, 'never_used': [ {id, title, state} ], 'rows': [
    {song, arrangement, add, remove} ], 'unknown_songs': [ids in usage but not
    in the library]}. `rows` is the retag CSV apply-tags consumes: a row only
    where the state changes or `current_tag` (a superseded tag such as
    Usage:Current) has to come off. Adds are qualified "Group:Tag"; the
    single-select group drops the old value on its own.
    """
    import datetime as dt
    from collections import Counter, defaultdict

    cutoff = as_of - dt.timedelta(days=30.44 * dormant_months)
    intro_cutoff = as_of - dt.timedelta(weeks=intro_weeks)
    known = {s["id"] for s in library}
    dates: dict[str, set] = defaultdict(set)
    unknown = set()
    for r in usage:
        sid = r.get("song_id")
        if not sid:
            continue
        if dt.date.fromisoformat(r["date"]) > as_of:
            continue
        if sid not in known:
            unknown.add(sid)
            continue
        dates[sid].add(dt.date.fromisoformat(r["date"]))

    lc, seasonal, skip_arr = set(lifecycle_tags), set(seasonal_tags), set(skip_intro_arr_tags)
    songs, rows, never = {}, [], []
    counts, transitions = Counter(), Counter()
    for s in library:
        tags = set(s.get("tags") or [])
        have = sorted(tags & lc)
        current = have[0] if have else None
        used = sorted(dates.get(s["id"], ()))
        n = len(used)
        first, last = (used[0], used[-1]) if used else (None, None)
        if "Retired" in tags:
            state, why = "Retired", "set by hand"
        elif "Queued" in tags and n == 0:
            state, why = "Queued", "set by hand, not yet used"
        elif n == 0:
            state, why = "Dormant", "never used"
        elif tags & seasonal or any(set(a.get("tags") or ()) & skip_arr for a in s.get("arrangements") or ()):
            state = "Rotation" if last >= cutoff else "Dormant"
            why = f"seasonal/modernized hymn, last {last}"
        elif n <= new_threshold and last >= intro_cutoff:
            state, why = "Introducing", f"{n} use(s), first {first}, last {last}"
        elif last >= cutoff:
            state, why = "Rotation", f"{n} uses, last {last}"
        else:
            state, why = "Dormant", f"{n} uses, last {last}"

        counts[state] += 1
        songs[s["id"]] = {"title": s.get("title"), "state": state, "was": current,
                          "uses": n, "first_used": first.isoformat() if first else None,
                          "last_used": last.isoformat() if last else None,
                          "seasonal": sorted(tags & seasonal), "why": why}
        if n == 0 and state == "Dormant":
            never.append({"id": s["id"], "title": s.get("title"), "state": state})
        remove = [current_tag] if current_tag and current_tag in tags else []
        add = [] if state == current else [f"{group}:{state}"] if group else [state]
        if add:
            transitions[f"{current or '(none)'} -> {state}"] += 1
        if add or remove:
            rows.append({"song": s["id"], "arrangement": "",
                         "add": ";".join(add), "remove": ";".join(remove)})
    return {"songs": songs, "counts": dict(counts), "transitions": dict(transitions),
            "never_used": sorted(never, key=lambda x: (x["title"] or "").lower()),
            "rows": rows, "unknown_songs": sorted(unknown), "cutoff": cutoff.isoformat()}


# --- worklist flag (a script-owned scratch tag for UI filtering) --------------

def flag_songs(client: PCOClient, index: TagIndex, tag: str, song_ids: list[str], state_path: str,
               note: str = "", dry_run: bool = False, log=None) -> dict:
    """Make `tag` (e.g. 'Worklist:Flagged') sit on exactly `song_ids`: it comes
    off every song this op flagged last time (recorded in `state_path`, a JSON
    file keyed by tag — no export needed) and goes onto the new list. A human
    filters the PCO songs view by the tag to work the list. Returns
    {'added': [...], 'removed': [...], 'kept': [...]}."""
    import datetime as dt, json, os
    t = index.resolve(tag, "song")
    key = f"{t['group']}:{t['name']}"
    state = {}
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    old = list(state.get(key, {}).get("ids", []))
    new = list(dict.fromkeys(song_ids))
    added = [i for i in new if i not in old]; removed = [i for i in old if i not in new]
    kept = [i for i in new if i in old]
    for sid in removed:
        if log: log(f"  - {sid}")
        if not dry_run:
            retag(client, index, song_path(sid), "song", remove=[key])
    for sid in added:
        if log: log(f"  + {sid}")
        if not dry_run:
            retag(client, index, song_path(sid), "song", add=[key])
    if not dry_run:
        state[key] = {"ids": new, "note": note, "set_at": dt.date.today().isoformat()}
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2); f.write("\n")
    return {"added": added, "removed": removed, "kept": kept}

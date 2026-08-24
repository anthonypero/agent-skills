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

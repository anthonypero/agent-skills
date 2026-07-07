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

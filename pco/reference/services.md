# Planning Center Services API — field notes

Hard-won knowledge from real integrations (first: song-library, 2026-07).
API root: `https://api.planningcenteronline.com/services/v2`. Official docs:
https://developer.planning.center/docs/#/apps/services

## Data model (songs side)

```
Song                        title, author, ccli_number, copyright, themes
  └─ Arrangement            name, chord_chart, chord_chart_key, bpm
       └─ Key               name, starting_key (e.g. 'Bb', 'F#m')
```

- A **Song** is the umbrella record. Creating one (`POST /songs`) auto-creates
  a default arrangement — sometimes asynchronously, so poll
  `GET /songs/<id>/arrangements` briefly after creating.
- An **Arrangement** is a genuinely different chart of the song (e.g. the
  Cohen vs. Buckley "Hallelujah"). Its `chord_chart` attribute holds the
  chart text; PATCH it to update what Music Stand shows.
- A **Key** belongs to an arrangement. Create with BOTH `name` and
  `starting_key` set to the key string (`{"name": "Bb", "starting_key": "Bb"}`).
- Keys are attributes-of-performance, not charts: charts transpose live, so
  key-specific resources mainly matter for key-specific attachments (audio).

## Attachments — the three levels rule

Attachments hang off an "attachable": `/songs/<id>`,
`/songs/<id>/arrangements/<id>`, or `.../keys/<id>` (`GET|POST
<attachable>/attachments`).

- **Audio belongs at the arrangement or key level.** A song-level attachment
  renders as a generic "file" across every arrangement instead of as that
  arrangement's playable reference recording.
- **Link attachments** (a URL, no upload): POST with attributes
  `remote_link` + `filename`.
- **The filename MUST end `.mp3`** for PCO to mark a link attachment
  streamable (`filetype: audio`, `streamable: true`, `web_streamable: true`)
  so it plays in the PCO / Music Stand player. Without the extension it's
  `filetype: file`, `streamable: false` — content sniffing is not performed.
- Idempotency is yours: list the attachable's attachments and match on
  `remote_link` before creating.
- Arrangements carry an auto-generated `lyric_chart-<arr_id>` PDF attachment;
  ignore it.
- The remote host must be publicly reachable (no auth) for PCO's player to
  stream it. Unlisted-but-public object storage (e.g. R2 with robots.txt and
  no bucket listing) works.

## chord_chart dialect ("Lyrics & Chords" format)

PCO's knockoff-ChordPro, NOT standard ChordPro:

- Section headings are bare ALL-CAPS lines (`VERSE 1`, `CHORUS`), not
  `{comment:}` directives. A heading is preceded by a blank line.
- Chords stay inline in brackets: `Let [E2]no one...`.
- Transpose is `TRANSPOSE KEY +2` — **`TRANSPOSE +2` is silently ignored.**
- `PAGE_BREAK` / `COLUMN_BREAK` on their own lines control Music Stand
  layout. Users place these deliberately — round-trip them, never drop them.
- `{note}` (single braces) = performer note; `{{note}}` (double) = a note
  visible only in the editor.

## API mechanics

- **Auth**: HTTP Basic with a Personal Access Token —
  `app_id:secret` from https://api.planningcenteronline.com/oauth/applications.
  Identity check: `GET /people/v2/me` (works with the same token).
- **Rate limit**: ~100 requests / 20 s. On 429, honor the `Retry-After`
  header and retry.
- **Pagination**: `?per_page=100` max; follow the absolute
  `links.next` URL until absent.
- **Sideloading**: `?include=arrangements` on `/songs` returns the related
  resources in a top-level `included` array (each with
  `relationships.song.data.id` to join on) — one paginated sweep instead of
  a request per song.
- **DELETE** returns an empty body (204) — don't try to parse JSON from it.
- Errors return JSON with a `detail`; truncate when logging, they can be long.

## Tags (songs and arrangements)

- Tag groups: `GET /tag_groups?include=tags` — each has `tags_for` (song | arrangement |
  person | media) and `allow_multiple_selections`.
- Read: `GET /songs/<id>/tags`, `GET /songs/<id>/arrangements/<id>/tags`. **Listing endpoints
  ignore `include=tags`** (and `include=arrangements`); only
  `/songs/<id>/arrangements?include=keys` sideloads. A full library export is therefore ~3
  calls per song (+1 per arrangement) — minutes, rate-limited.
- Write: `POST <song|arrangement>/assign_tags` with
  `{"data":{"type":"TagAssignment","attributes":{},"relationships":{"tags":{"data":[{"type":"Tag","id":..},..]}}}}`.
  It **replaces the entire set** (so read-modify-write); unknown ids are silently ignored;
  the response body is `{}`.
- Song attrs worth knowing: `last_scheduled_at`, `themes` (free text from CCLI), `hidden`.
  Arrangement: `bpm`, `length`, `meter`, `chord_chart_key`, `sequence`; keys via
  `.../keys` (`starting_key`, `ending_key`).
- Plan items link songs: `.../plans/<id>/items?include=song,arrangement,key` → item
  `relationships.song/arrangement/key`; `key_name` attr is the display key.

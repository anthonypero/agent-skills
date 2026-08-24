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

## Teams and availability

- A team's roster: `.../service_types/<st>/teams/<id>/person_team_position_assignments?include=person,team_position`
  — one row per person × position, attributes `schedule_preference` + `preferred_weeks`. `/teams/<id>/people`
  gives the deduplicated headcount the UI shows.
- "Preferred Weeks" in the UI is `schedule_preference`. To keep a fill-in on the team but out of
  auto-scheduling, PATCH it to exactly **`"Unavailable"`** (`services availability`). Any other
  unrecognized string ("Do Not Schedule" etc.) returns 200 with the value unchanged — check the
  response, not the status. Default is "As often as needed".
- Editing the same assignment in the PCO UI while patching it wins over the API (learned 2026-08-24).

## Scheduling notifications — no API (verified 2026-08-24)
The public API only *prepares* requests (`prepare_notification`, `notification_prepared_at`).
Probed and dead: POST `.../team_members/<id>/send_notification|notifications|send`, POST
`.../plans/<id>/send_notifications`, `.../team_members/send_notifications` → 404; PATCH
`notification_sent_at` → 422 Forbidden Attribute. Sending is done from the matrix view in the web
app (Tony does this by hand — select weeks → Email). Don't re-probe.

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
- **Tag groups and tags are read-only via the API** (verified 2026-08-24 as org owner): PATCH
  `/tag_groups/<id>`, POST `/tag_groups/<id>/tags`, DELETE a tag → all 403 ("cannot create a
  Tag" / "cannot update TagGroup"). Taxonomy edits (new tag, rename, single↔multi-select)
  happen in the web UI: Services → Settings → Tags. Only assignment is scriptable.
- A tag group also carries `required` (bool) alongside `allow_multiple_selections` —
  `get_tag_groups()` surfaces it. **`required` is a UI constraint only**: the API happily
  leaves a song with no tag from a required group, and `assign_tags` never rejects a set for
  omitting one. Don't rely on it; compute the full set yourself.
- **Deleting a tag in the UI cascades to every assignment** (verified 2026-08-24: removing
  `Usage:Current` stripped it from all 71 songs that had it). So a `songs export` dump goes
  stale the moment the taxonomy changes — an offline op that plans a *removal* should confirm
  the tag still exists via `get_tag_groups()` first, or `TagIndex.resolve` will raise
  mid-run and abort the whole CSV.
- Song attrs worth knowing: `last_scheduled_at`, `themes` (free text from CCLI), `hidden`.
  Arrangement: `bpm`, `length`, `meter`, `chord_chart_key`, `sequence`; keys via
  `.../keys` (`starting_key`, `ending_key`).
- Plan items link songs: `.../plans/<id>/items?include=song,arrangement,key` → item
  `relationships.song/arrangement/key`; `key_name` attr is the display key.
- Who is scheduled on a plan: `.../plans/<id>/team_members?per_page=100` — attributes
  `team_position_name` and `name`. No `include` on the plans listing brings them along, so
  it is one call per plan (`plan_leaders()` / `pco services plans <type> --leaders`).

## Offline song planning (`songs mix` / `lifecycle` / `plan`)

`songs export` → `library.json` plus a full-history `song-usage` CSV is enough to run the
whole song-planning chain with **zero API calls**. Things learned building it:

- **A "use" is a distinct plan date**, not a row: a song listed twice in one service is one
  use. The usage CSV also contains FUTURE plans — `lifecycle` ignores dates past `--as-of`
  (a song scheduled for next Sunday has not been sung), but `plan` counts them, because a
  scheduled debut is already an obligation.
- **Section headers carry the slot.** The Modern template's four `Song(s)` fields land under
  `WE GATHER` (opener) and `WE RESPOND` (2 and 3 after the message, 4 after the offering,
  sometimes under a communion variant of the header). `plan_slots()` reads them back:
  opener = the GATHER row, closer = the LAST row in sequence. Older plans use other section
  names, so slot inference only trusts plans with GATHER/RESPOND headers.
- **Liturgy is not a song choice.** `The Great Thanksgiving` is a communion setting that would
  otherwise look like the most-repeated song in the library — `plan` excludes the title regex
  `Great Thanksgiving` by default, `mix` takes it via `--exclude`.
- **Key names are the congregational signal.** Arrangement keys carry a free-text `name`;
  the ones a congregation can actually sing are named `Congregational: Male Lead` /
  `Congregational - Female Lead` (spelling varies — match the substring). Most keys are
  unnamed, so a planner should mark an unvetted key rather than pretend.
- **Worship-sheet headers drift** (`Sermon Scripture/`, doubled spaces, a garbled date
  header). Match them normalized and by prefix (`sheet_column()`), and find the date column
  by which one parses as a date most often rather than by its name.

### Applying a plan (`songs apply-plan`)

- A plan item becomes a song item by PATCHing the `song` relationship; `item_type` is **not
  assignable** (422 "Forbidden Attribute") — PCO flips it to `song` itself. Send `title`, `length`,
  and the `song` / `arrangement` / `key` relationships in one PATCH.
- Slot n = the n-th item in service order that is either already `item_type: song` or a template
  placeholder titled `Song(s)`; filled slots are skipped unless `--overwrite`.
- The export has no key ids, so keys are fetched live per song (cached per run); `pick_key` prefers a
  name containing "congregational", then "original", else the first key (rule 14).
- **The CSV is the desired state.** `songs plan` writes already-scheduled songs into `song`, so a
  blank cell means "empty slot" — with `--clear-empty` it clears. (Lesson 2026-08-24: an older CSV
  with blank cells for an already-planned Sunday wiped it; restored by hand from item ids.) Always
  `--dry-run` first after hand-editing.

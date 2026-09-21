---
name: multitracks-library-management
description: Manage the user's song library on MultiTracks.com — create cloud songs, upload stems, fill in metadata (track names, sections, time-signature changes, tempo changes) from a REAPER project-MIDI export, and enter chord charts from whatever chord chart the user points to (ChordPro preferred), driving the site with Chrome automation. Use this whenever the user mentions uploading a song to MultiTracks, entering sections/markers/click/chart data on multitracks.com, making the dynamic guide or click follow their tracks, parsing a REAPER MIDI export for markers or time signatures, or generating marker/timesig CSVs for a song — even if they only paste a .MID file path or a multitracks.com URL.
---

# MultiTracks.com library management

The user records custom stems in REAPER and uploads them to MultiTracks.com so the platform's dynamic guide and click follow their arrangement. The site requires hand-entering track names, song sections with timestamps, time-signature changes, and tempo changes. This skill automates that data entry from a single REAPER MIDI export, eliminating manual transcription errors.

The user watches both screens during this workflow. Go slow, narrate what you see, and ask before anything ambiguous. Decisions they make (label choices, what to skip) are per-song — re-ask when a new song presents the same ambiguity, unless they've stated a standing rule.

## Inputs and where to find them

Steps 0–4 and the *Legacy MultiTracks purchases* section work from a small set of inputs. This skill assumes nothing about where they live:

- **The stems** — a zip, or a folder of audio files (Step 0 and the legacy section; the legacy section also needs `ffmpeg`).
- **The REAPER project-MIDI export** — a `.MID`/`.mid` file (Step 1).
- **The chord chart** — ChordPro preferred, but any readable chart the user points to is acceptable (Steps 2 and 4).
- **An output location** for generated files — the marker/timesig CSVs and the downloaded chart PDF.

Resolve each input in this order and stop at the first answer:

1. **A path or URL the user gave in the request.** Always wins.
2. **The current project's own conventions.** If the project's instructions (`CLAUDE.md`/`AGENTS.md`) or its private MultiTracks notes file say where charts, stems, MIDI exports, or archives live, use that — if it's being opened in a project that has charts, the project itself knows where to look.
3. **Otherwise ask the user**, in one question naming exactly what is missing ("Where is the stem zip for this song?"). Do not go hunting across the disk.

Defaults when nothing says otherwise: write generated CSVs next to the MIDI export; leave the downloaded chart PDF where the browser put it and tell the user the path, unless the project's conventions name an archive location.

Step 5 (setlists) is pure browser work against multitracks.com and Planning Center — it needs no local files and runs from any project. Script paths in this document are relative to this skill's own directory.

Song-, account-, and machine-specific facts — library IDs, PCO arrangement IDs, which stem set a given song currently runs on, where stems and archives live on disk — do **not** belong in this skill. Record them in the project's private notes file, conventionally `.agents/documentation/multitracks/library-notes.md`, and read that file at the start of a run if it exists.

## Step 0: Create the song entry and upload stems (first-time songs)

Skip this step when the song already exists in the user's library — go straight to Step 1.

**Finding the stems:** resolve them as described in *Inputs and where to find them* — the path the user gave, else the project's own conventions (e.g. a project might keep stems in each song's `audio/` folder, often a symlink into cloud storage, as a zip named for MultiTracks like "Stems for multritracksdotcom.zip"), else ask. If multiple stem sets exist, verify the right version before anything else: extract to the scratchpad and compare durations (`afinfo ... | grep duration`) across sets and against the MIDI-derived song length, and confirm the choice with the user. The site requires **one .zip** containing only audio files (MP3/M4A/WAV), under 500 MB, all stems the same length. A zip with a nested folder, `__MACOSX/`, and `.DS_Store` entries works fine — the junk is ignored.

**Creating the song:** navigate to `multitracks.com/premium/library/` (the bare `/premium/library/cloud/` path 403s). Click **Add Song** (JS-driven, href="#" — must be clicked, not navigated) → **Create Cloud Song**. The Song Name field is a live search box: typing opens a suggestion overlay that covers the rest of the form and swallows clicks aimed at other fields. Type the name, click the **Create Song "<name>"** button inside the overlay to commit it, and only then fill the rest: Artist, Key (button grid + ♭/#/Major/Minor), Tempo (BPM), Time Signature. Genre, Theme, Album, and artwork are optional — ask; the user left all of them unset for an original. **CREATE** lands on the song's Tracks page (`tracks/uploadFiles.aspx?libraryID=<id>` — note the libraryID).

**Uploading the zip is a manual step for the user.** Clicking Upload opens a native macOS picker the Chrome extension cannot see, and the `file_upload` injection tool caps at 10 MB per call — a real stem zip is far larger, and the site rejects loose MP3s pushed at the input ("Wrong file type"). Reveal the zip for them (`open -R "<path>"`) and ask them to drag it onto the Upload area. (Claude Cowork operates at desktop level and may automate this — untested as of 2026-08.) Progress shows as "Uploading (N%)" then "Analyzing Tracks", then the track rows appear. Files keep processing server-side for up to ~30 min (the site emails when done), but **track naming and all other tabs work during processing** — don't wait. After the Tracks tab is saved, the header Length populates; check it against the MIDI.

## Step 1: Get and parse the MIDI export

The user exports from REAPER via **File → Export project MIDI** (with "Embed project tempo/time signature changes" enabled, the default). Resolve the export as described in *Inputs and where to find them*; if there is no `.MID`/`.mid` file where the user or the project points, ask them to export one — it carries the complete tempo map, time-signature events, and all markers with exact tick positions, so no screenshots of the Region/Marker Manager are needed.

Parse it:

```bash
python3 scripts/parse_midi.py "<path/to/export.MID>"
```

This prints tempo changes, time signatures, and markers (in both `MM:SS.mmm` and the site's `MM:SS:mmm` form), and writes `<song-slug>-markers.csv` and `<song-slug>-timesig.csv` beside the MIDI export (or wherever the project's conventions put generated files). MIDI-derived times are exact — trust them over anything read from a REAPER screenshot, which rounds differently by ±1 ms.

REAPER writes a redundant tempo event at every time-signature change; the script dedups consecutive identical tempos. A song usually has one real tempo and a handful of meter changes (often single 2/4 pickup bars).

## Step 2: Reconcile section names with the chart

The chart — resolved as described in *Inputs and where to find them* — is what musicians actually read, so its section labels are the reference point; but the user treats either side as editable ("change MultiTracks to match the chart, or change the chart to match; either is acceptable"). **When the chart is ChordPro**, compare the marker names against its `{comment: ...}` sections (older files may use bare uppercase headers instead — see *Gotchas*). **For any other chart format**, read the section labels as written; if the section boundaries are ambiguous, ask rather than guessing. **If no chart is available at all**, use the MIDI marker names as-is and skip Step 4 entirely.

Surface every mismatch with AskUserQuestion before entering anything. Typical mismatches seen so far:

- REAPER "Channel" vs chart "Interlude" (user historically calls these turnarounds and doesn't care much — ask)
- Verse numbering: the user numbers verses by the *hymn's* verse numbers on charts, which can skip (Verse 2 → Verse 4). Ask whether to keep chart numbering or renumber sequentially — and apply the choice to both the chart and MultiTracks so they agree.
- Extra 1–2 bar markers (a short channel before an instrumental) that the chart doesn't show: offer to fold them into the adjacent section, or add them both places.

Also fix obvious typos from the REAPER project ("Bbridge", "Pre-Chours") silently in what you enter, but tell the user so they can fix the REAPER project — the typo will otherwise reappear in every future export. The user's standing preference: "always good to correct things."

Sections repeated identically stay un-numbered (plain "Chorus" three times) unless the chart numbers them. The user's convention: number variants only when the content differs.

## Step 3: Drive the MultiTracks site

Read `references/vocabularies.md` for the exact dropdown option lists and the file-prefix → track-name mappings before filling anything.

**Getting to the page:** You cannot see tabs the user attached to a different Claude conversation — each session has its own tab group. Create a tab (`tabs_context_mcp` with `createIfEmpty`) and ask the user to paste the song's URL into it, or navigate directly if they give you the URL. The page is `multitracks.com/premium/library/cloud/tracks/files.aspx?libraryID=<id>`, and the user's Chrome profile is already logged in.

The song page has four sub-tabs: **Tracks, Sections, Time Changes, Tempo Changes**. Each tab has its own Save button and **must be saved separately** — a green banner ("Your tracks/sections/... have been saved.") confirms each save. The user has authorized saving each tab as part of this workflow; confirm the *data* with them before entering it, not each Save click.

All four tabs are plain DOM under the styled widgets — **native `<select>`s and text inputs** — so the fastest reliable driver is `javascript_tool`: set `.value`, then dispatch events (`change` for selects; `input`+`change`+`blur` for time fields). The site's own UI reacts (e.g. "Type Part Name" disappears), which confirms the events registered. `form_input` works too but is one call per field.

**Tracks tab:** Each uploaded stem gets a "Name Your Track" dropdown (`<select id="tracks_part_N">`, in file order). Match the stem filename's prefix to a dropdown option (see vocabularies reference). Propose the full mapping to the user in a table and get a yes before setting them, then set all rows in one JS pass — map each select to its filename by walking up to the row container and matching the `.mp3` text. Ignore the "Type Part Name" field when a dropdown option matched (it disappears).

**Sections tab:** Rows are strict fill-before-add: exactly one empty row exists at a time, and the "Add Section" link does nothing until the current row's dropdown *and* time are filled. The first row is `#sections_section_0`, but rows created by "Add Section" are **clones with no id/name** — target the *last* select whose options include "Count Off". A single JS loop handles the whole table: fill the last row's select + time input (time in **MM:SS:mmm** — colons, not a dot, minutes zero-padded, e.g. `02:46:956`), click "Add Section" (JS `.click()` works), then poll until the count of section selects increments before filling the next. Don't add after the final row.

After the last row, screenshot for a visual check, then Save. The header's section-pill chain (I, V1, C, …) confirms what was stored.

**Time Changes tab:** A first row pre-exists with the song's base meter at 00:00:000 (an "Add Signature" button, not a link — same clone-and-poll pattern as Sections, except here you click Add *first*, then fill the new row). Enter every meter change from the timesig CSV, **including each return to the base meter**. A constant-meter song needs nothing added — just hit Save to register the tab.

**Tempo Changes tab:** Same shape — pre-filled base BPM at 00:00:000 (shown as e.g. `69.0000`) and an "Add Tempo" button; each row is a BPM text input (`input.js-form-bpm`) + a time input (`input.js-form-time`), mirrored into the hidden `#tempoList` JSON. Enter only real tempo changes (ignore REAPER's redundant restated tempos). Save even if unchanged. The Save link keeps its `is-disabled` class after JS-driven edits but still works when clicked.

**The site RAMPS linearly between consecutive tempo rows — it does not step.** Confirmed in Playback (2026-08-22): rows `114 @ 0:00` and `111 @ 1:57` made the click slide toward 111 from the very first bar (≈1.5 s behind by 1:57). So every tempo change needs a **pair of rows**: restate the current BPM a few hundred ms before the change, then the new BPM at the change (e.g. `114 @ 01:57:000`, `111 @ 01:57:388`). The ramp is then confined to that small window — an effective step. `tempo_distill.py` prints the site-ready paired rows already.

**Variable-tempo songs (per-beat tempo maps):** A track produced from a live-feel performance (e.g. exported from Suno into REAPER) carries hundreds of per-beat `set_tempo` events. Entering them all is impractical, and there is no ingestion path — the song page's top-level **MIDI tab is "MIDI Out"** (trigger cues for lighting/ProPresenter in Playback), not tempo import. But most of the density is zero-mean jitter, which never accumulates; only *sustained* deviations (a slower breakdown section, a closing ritard) make a static click drift. Real-world reference: a 3:50 song with a 14-second ~111 section in an otherwise ~114 map put a static-114 click a full beat behind by the end.

Distill the map instead:

```bash
python3 scripts/tempo_distill.py "<path/to/export.MID>" [tolerance_ms ...]
```

It fits the fewest constant-tempo segments that keep the click within each tolerance of the true beat grid, prints the site-ready rows in `MM:SS:mmm` form (each segment as a ramp-defeating pair — see the Tempo Changes tab above), and reports the verified worst-case drift (including for a static tempo, for comparison). Show the user the row-count-vs-drift tradeoff and let them pick; ~50 ms max drift (a tenth of a beat) is a reasonable starting point, verified by them testing in Playback. Offer the choice between exact transition times and musically aligned ones (section boundaries).

**Skip "Link Song":** the yellow banner offering to link the song to the MultiTracks catalog is skipped for the user's originals — they report through Planning Center (CCLI), not MultiTracks.

## Step 4: Enter the chord chart (CHARTS tab → Chart Data)

Skip this step entirely when no chart could be resolved (see Step 2). Otherwise work from whatever chart was resolved there.

The song page's top-level **CHARTS** tab (`charts.aspx?libraryID=<id>`) has three sub-tabs: **Chart Data** (enter the chart as text), **PDF Charts** (the site-generated Cloud Chart), and **Upload Charts** (drag-and-drop your own PDF instead).

**Chart Data structure:** the page auto-creates one card per Sections-tab row — *except Count Off* — in order, timecodes pre-filled. Each card (`.cloud-charts--edit`, inside a `.cloud-charts--row.js-chart-section` row) holds a section-type select (`select.js-chart-section-type`, ~62 options — a richer vocabulary than the Sections tab, including "Pre Chorus 2", "Breakdown", "Exhortation"), a timecode input, the chart textarea (`textarea.js-chart-chordpro`), and an optional MD Notes input. Don't touch the selects or timecodes — they're already right from the Sections tab.

**Format** (per the official tutorial PDF, `https://mtracks.azureedge.net/public/content/en/Quick_Start_Guide_Cloud_Charts.pdf` — fetchable and readable): lyrics one phrase per line, chords in square brackets before the word/syllable they land on — i.e. ChordPro inline chords with **no directives** (section identity comes from the card, not `{comment:}`). Trailing chords (`great[Cmaj7]`), space chords (`[Cmaj7] great`), and split-syllable chords (`ma[Cmaj7]jestic`) all render as chords-over-lyrics. Instrumental/intro/outro sections take bare chord lines (`[D] [E] [F#m] [E] [D]`). Leave the Ending card empty.

**Chord spelling:** the site's vocabulary uses no parentheses — convert a chart's `D2add(#4)` to `D2#4`. Additions are appended last (`add4`, `add9`, `no3`, `no5`, `#4`); extensions `b5 #5 b9 #9 #11` attach to 7th-and-above chords; slash chords (`A/C#`) and bare `Esus` work. Verify odd spellings in the rendered preview after saving.

**Deriving from the chart:** when the chart is ChordPro, map each `{comment:}` section to its card, strip directives, keep chorus repetitions separate (they may differ — e.g. a trailing `[E]` leading into the Turnaround on the first chorus only). For any other format, read the section labels and the chords as written and map them the same way; if the section boundaries are ambiguous, ask. Fix chart typos in both places, per the user's standing "always good to correct things".

**Verify the chart with the user BEFORE entering it.** Confirm the chart is current and correct (chords *and* lyrics), and surface anything you converted or fixed — chord-spelling changes, suspected typos, section mismatches — and wait for a yes. Entering first and correcting after means redoing the whole entry pass; the user has explicitly asked for this gate.

**Entry:** the cards all exist up front — no clone-and-poll dance. One JS pass sets every `textarea.js-chart-chordpro`'s `.value` (walk `.cloud-charts--edit` cards in order) and dispatches `input`+`change`; then click **Save & Preview** (`a.js-save-chart`, by find→ref) and wait for the green "Your chart has been saved" banner. Per-card rendered previews appear to the right of each card (lazily, on scroll) after a save/reload — spot-check the tricky chords there or on the PDF.

**Archive the PDF after entry:** click **Download** on the PDF Charts sub-tab (it lands in `~/Downloads` named like `<Title>-<Artist>-.<Key>.<n>.pdf`), then file it wherever the project's conventions or its private notes file say chart PDFs belong — typically a local copy alongside the song's other files plus a copy in a cloud-drive MultiTracks archive (e.g. `<Title> - Chart (Key <X>).pdf`). If nothing names a location, leave the download where the browser put it and tell the user its path. Why archive at all: Cloud Songs are *not* auto-linked to Planning Center (only MultiTracks' pro catalog is), so the user uploads the chart to Planning Center manually — the cloud-drive copy makes it reachable from any computer.

**PDF Charts sub-tab:** shows the generated Cloud Chart with a Download button and display controls — view (Chords + Lyrics / Lyrics / Song Map / Chords Only), chord display (Chords / Numbers / Numerals / Do Re Mi), 1/2-column layout, Full/Condensed style, font, color, toggles for Song Map / Section Outline / MD Notes, plus Key transposition and Capo. **The PDF always draws a box for every Sections row — including an empty Count Off box at the top and the Ending — and the site offers no way to hide them**: the Arrangement dropdown's custom arrangements are created in Playback or ChartBuilder, not on the site. The user has accepted the Count Off box ("I will just live with it"). Never remove Count Off from the Sections tab to get it off the chart — Playback needs it.

## Legacy MultiTracks purchases (no MIDI, fixed click + cue stems)

Old catalog downloads (a folder named like `<catalogID>_multi_<key>/`, located as described in *Inputs and where to find them*) come as WAV stems with a `CLICK` and a `CUES` file and no project. They are worth re-uploading as a Cloud Song when the catalog no longer lists the song (so Planning Center can't link it). What differs from the REAPER flow:

- **Get the tempo from the click, not the filename.** `scripts/click_grid.py <CLICK.wav>` detects every click, fits a constant grid, and reports the true BPM and worst-case drift (one purchased click labelled "90" measured 89.80 — a static 90 drifts a full eighth by the end). The site takes decimals; enter the fitted value.
- **Section times come from the user's REAPER markers converted to beats**, then mapped onto the click grid — never from REAPER seconds, since a project built from a different audio source (a live recording, Moises stems) has its own start offset and timing fixes. Beat 0 of the REAPER project ↔ the count-in bar of the click file; find the intro downbeat from the count-in words in `CUES` (energy-segment the file, the four count words land on quarter notes, the next quarter is bar 1). 2/4 bars in the REAPER MIDI carry over by beat count.
- **The CUES track verifies the map.** Whisper hallucinates on the near-silent file as a whole; energy-segment it (20 ms RMS, ~5% threshold) and transcribe each half-second clip separately. Spoken cues land on beat 1 of the bar *before* each section — every cue should sit ~4 beats ahead of the derived section time.
- **Stems are usually unequal lengths** (one legacy set ran 285–299 s); the site requires equal. Pad all to the longest with `ffmpeg -af apad -t <max>` while converting to 320k MP3 (444 MB of WAV became an 88 MB zip). Name the files `<PART>_<Title>.mp3` so the Tracks tab mapping is obvious: CLICK → Click Track, CUES → Guide (Non-Dynamic), AGTR → Acoustic Guitar, EGTR1/2 → Electric Guitar 1/2, PAD → Synth Pad.

## Step 5: Put the song in the Sunday setlist (Planning Center import)

Setlists live at `/premium/setlists/` (the `/premium/library/setlists/` guess 404s); each is `details.aspx?setlistID=<id>` and is imported from Planning Center. Workflow after the user has changed arrangements in PCO:

1. **Refresh first**: the setlist card's `…` menu → **Update From Planning Center**. It re-reads keys, tempos, and arrangements ("Setlist updated successfully"); PCO songs with a catalog match auto-link on refresh.
2. **Unlinked rows show a yellow "Link Song"** in the Content column. Clicking it opens *Select Song Versions* (the PCO arrangements — the one used by the plan is pre-checked) → Next → *Link Song to MultiTracks.com*, whose search lists catalog and Cloud Songs together (a CLOUD SONGS tab filters). Tick the cloud song → LINK SONG. The row then shows the cloud song's key and BPM. This link is remembered for future plans using that PCO arrangement.
3. **A row auto-linked to the wrong catalog entry can't be re-pointed.** Both the row's `…` → Edit and the title link open *Edit Song*, which only edits key/tempo/Tracks Product for that catalog song (e.g. a song whose original arrangement auto-linked to a retired, click-only catalog entry). The sidebar **Song Link** page lists only *unlinked* PCO versions. The user's standing practice, confirmed 2026-09-16: **Edit Setlist → row `…` → Remove → Add Item → Cloud filter → search → Add (key pre-filled, Tracks Product "Cloud Upload") → Save to Cloud** (a note is optional). Verify by reloading: `li.mod-setlist-row` rows carry `data-library-id`, which should equal the cloud song's libraryID.
4. **Every *Update From Planning Center* re-adds the catalog row and drops a manually swapped cloud one** (confirmed twice 2026-09-17). The link MultiTracks stores is per PCO *arrangement*; an arrangement that ever matched the catalog (e.g. a song whose original arrangement auto-linked to a retired catalog entry, presumably matched by CCLI number) keeps that link forever: renaming the arrangement changes nothing, and the sidebar Song Link page lists only *unlinked* arrangements, so it cannot be redirected. **The fix that sticks: give the PCO song a brand-new arrangement** (song page → Arrangements → Add → custom name, key, *no* SongSelect imports; do not duplicate — a copy may carry the match), point the plan item at it, then refresh. The new arrangement arrives as a yellow "Link Song" row (tempo shows 120 until the PCO arrangement has a BPM) — link it to the cloud song via Select Song Versions → Next → CLOUD SONGS tab → tick → LINK SONG. Verified 2026-09-17: the link survived a further refresh. Afterwards set Length/BPM/Meter on the new arrangement, copy the chord chart across (open the old arrangement's `/chord_chart/edit`, stash the textarea text in `localStorage`, paste it into the new arrangement's editor — it auto-saves), and re-upload any audio the old arrangement had (native picker: the user drags it).

## Gotchas

- **Replacing stems on an existing song: use Tracks tab → Delete Files, not Delete Song.** The libraryID survives, so nothing downstream has to be re-created, and the Sections / Time Changes / Tempo Changes rows all survive too — you only overwrite the values (a single JS pass over the existing inputs, no clone-and-poll). Delete Files opens an in-page modal ("Are you sure you want to delete this file?" → Yes), not a native confirm. **But the site silently removes the song from every setlist it was in** (confirmed 2026-09-17: a setlist dropped the song the moment its files were deleted, logged as "Changes have been made to one or more items"). After the re-upload, re-add it via Edit Setlist → Add Item → Cloud filter (Step 5.3); the row lands at the bottom, so drag it back into plan order if that matters.
- **Tempo Changes BPM input needs a `keyup`**, not just `input`/`change`, for the site to mirror the new value into the hidden `#tempoList` JSON (which is what Save actually submits). The header BPM follows the Tempo Changes tab on save — Edit Song Details is not needed to change it.
- **A legacy catalog click can be too loose to play to, which makes a rebuild the right call.** A purchased click whose fitted BPM sits off its labelled value (see *Legacy MultiTracks purchases*) can be variable enough in practice that the user cannot lock to it (seen once so far); the fix is to rebuild the song on straighter stems (e.g. a stem separation, grid-straightened in REAPER) and re-upload at a static tempo. Whenever a song is rebuilt that way, record in the project's private notes which stem set it now runs on and which MIDI export is the source of truth for its sections and meter — the marker/timesig CSVs derived from the old grid become historical and must not be reused.

- **Chrome must be on a profile, not the profile picker**, or the extension reports "not connected". `open -a "Google Chrome"` alone lands on the picker when it is enabled at startup; the user has to pick the profile.

- **Sections/Time/Tempo rows can't be added until the header Length populates** — "Add Section" silently does nothing while the audio is still processing after upload (typically 5–10 min). Poll the header, then enter. (Track naming does work during processing.)
- Sub-tab URLs: `tracks/files.aspx`, `tracks/sections.aspx`, `tracks/time.aspx`, `tracks/tempo.aspx`, top-level `charts.aspx` — all `?libraryID=<id>`. Navigating straight to them is more reliable than clicking the tab links.
- A REAPER project MIDI export with **no markers** parses to one tempo + one meter and writes blank-slug CSVs (`-markers.csv`); ask the user to add markers and re-export rather than scaling another version's times — arrangements differ more than the tempo ratio suggests.
- The "Add Song" link on the library page ignores a ref-click; click it by coordinate, then `find` the "Create Cloud Song" button.
- Save buttons: after a Save the extension often throws the chrome-extension URL error; instead of reading the banner, re-`navigate` to the tab URL and read the rows back — that is the real persistence check. If the reload shows only the default row, the save was lost; re-enter and Save from inside the same JS pass.
- Marker named "End" (not "Ending") → enter as Ending; tell the user to rename it in REAPER.
- Upload speed varies wildly (55 MB took 25 min once, 88 MB took 4 min an hour later). Poll with a background `sleep` timer rather than in-page JS waits, which time out at 45 s.

- `read_page` on the Tracks page is enormous (each row repeats a ~200-option dropdown). Use `find` with specific queries or targeted JS instead of full page reads.
- If a set value doesn't match an option, re-read that dropdown's options — don't guess variants.
- Time fields: the site truncates/validates oddly on bad formats; always `MM:SS:mmm` with both colons.
- The song header (Key / BPM / Time Sig / Length) is derived from the uploaded audio and the saved tabs — use it to sanity-check against the MIDI-derived values.
- If browser tools suddenly fail with "Cannot access a chrome-extension:// URL of different extension" (screenshots, clicks, and JS all die while `find`/`read_page` still work), re-`navigate` the tab to its own URL — the reload clears it.
- The user watches (and scrolls) the page during the run, so coordinates from an earlier screenshot go stale. Click Save buttons by `find` → ref, not coordinates.
- An **"Ending" marker after the Outro** is a MultiTracks playback convention — it marks the point after the last beat, used for crossfading. Enter it on the site; the chart doesn't carry it.
- Songs entered before this skill existed may have section times off by ±1 ms (screenshot rounding). Leave them — 1 ms is inaudible and not worth a re-save — unless the user asks.
- Older ChordPro files may use bare uppercase headers (`VERSE 1`) instead of `{comment: ...}` directives; reconcile against those the same way.

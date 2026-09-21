# MultiTracks.com fixed vocabularies

Both dropdowns on the site are fixed lists. Values entered via `form_input` must match an option label exactly. These lists were captured 2026-08-21 from the live forms; if a `form_input` ever fails to match, re-read the dropdown with `read_page` rather than guessing.

## Section names (Sections tab dropdown)

Count Off, Intro, Verse, Verse 1–8, Chorus, Chorus 1–8, Turnaround, Interlude, Pre Chorus, Pre Chorus 1–4, Post Chorus, Post-Chorus 1–4, Bridge, Bridge 1–8, Instrumental, Vamp, Solo, Refrain, Refrain 1–4, Tag, Tag 1–4, Outro, Ending, Breakdown, Channel, Exhortation, Rap, Acapella, Pad, Click

Common mappings from the user's REAPER marker names:

| REAPER marker | MultiTracks section |
|---|---|
| Count In | Count Off |
| Ending / End | Ending |
| Channel | Channel — but check the chart; it may say Interlude or Turnaround (see SKILL.md) |

Note the spelling quirks: "Pre Chorus" has no hyphen but "Post-Chorus 1" does.

## Track names (Tracks tab dropdown)

Special (top of list): Click Track, Guide (Dynamic), Guide (Non-Dynamic)

Instruments (alphabetical): Accordion, Accordion 1, Acoustic Guitar, Acoustic Guitar 2–3, Alto, Alto 1–2, Arps, Arps 1–2, Aux Drums, Aux Drums (Live), Background Vocals, Background Vocals 1–6, Background Vocals FX, Background Vocals FX 1, Banjo, Banjo 1–2, Bar Chimes, Baritone, Bass, Bass 2, Beatbox, Bells, Bongo, Brass, Brushes, Cajon, Cello, Cello 1–3, Chimes, Choir, Choir 1–2, Claps, Clarinet, Clarinet 1–3, Congas, Cowbell, CP 70, Crash Cymbals, Daegeum, Djembe, Double Bass, Double Bass 1–3, Drums, Drums (Live), Drums 1–2, Dulcimer, Dulcimer 1–2, Electric Guitar, Electric Guitar 1–9, Electric Guitar Group, Electric Guitar Group 1–2, Electric Piano, Fiddle, Flute, Flute 1–3, French Horn, French Horn 1–3, FX, FX 2, Gayageum, Guiro, Guitars, Harmonica, Harmonium, Harp, Horns, Horns 1–2, Janggu, Keys, Keys 1–10, Keys Group, Lap Steel, Lap Steel 1–2, Lead Vocal, Lead Vocal 1–3, Loop, Loop 1–4, Mandolin, Mandolin 1, MD Cues, Oboe, Oohs, Oohs 1, Orchestra, Organ, Organ 1–2, Original Song, Perc (Live), Perc (Live) 1–2, Percussion, Percussion 1–4, Piano, Piano 1–2, Piano FX, Piri, Rhodes, Rubab, Saxophone, Saxophone 1–3, Shaker, Sitar, Sleigh Bells, Slide Guitar, Snaps, Soprano, Soprano 1–2, Spoken Word, Steel Drums, Strings, Strings 1–4, Suspended Cymbals, Synth Bass, Synth Bass 1–2, Synth Bells, Synth FX, Synth Group, Synth Horns, Synth Lead, Synth Loop, Synth Loop 1–2, Synth Pad, Synth Strings, Tambourine, Tambourine 1–2, Tenor, Tenor 1–3, Timbales, Timpani, Toms, Trombone, Trombone 1–3, Trumpet, Trumpet 1–3, Tuba, Tuba 1–3, Turntables, Ukulele 1–2, Upright Bass, Viola 1–3, Violin, Violin 1–3, Vocal FX, Vocals, Vocoder, VoxChop, Whistle, Woodwinds

Common mappings from the user's stem-file prefixes:

| File prefix | Dropdown selection |
|---|---|
| Click_ | Click Track |
| Synth Grp_ | Synth Group |
| Synth_ | no plain "Synth" option — ask the user (Synth Pad is the usual answer) |
| Perc_ | Percussion |
| Lead Vocal_ | Lead Vocal |
| Backing Vocals_ | Background Vocals (site's wording) |
| Original Song_ | Original Song |
| `Drums_` / `Bass_` / `Piano_` / `Strings_` / `Electric Guitar_` | same-named option |

The "Type Part Name" text field next to each track dropdown is only for tracks with no matching dropdown option — it disappears as soon as a named option is selected. When every stem matches an option, no part names are needed.

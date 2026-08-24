---
name: pco
description: "Planning Center (PCO) API toolkit: Use whenever a task reads or writes Planning Center data from any project — Services songs/arrangements/keys/attachments/plans, People, Check-Ins, Giving, or any other PCO product. Handles credential resolution (per-project PROJECT_SECRETS.md, env vars, or ~/.config/pco profiles), rate-limit retry, and pagination. reference/ holds hard-won PCO API knowledge — read it before writing to a product."
---

# pco — Planning Center API toolkit

One implementation, two surfaces:

- **CLI** (`bin/pco`) — generic authenticated verbs for agents, humans, and
  non-Python scripts. Any endpoint of any PCO product works; no domain code
  needs to exist first.
- **Python lib** (`lib/`) — `pco_api.py` (generic client) + per-product
  helper modules (`pco_services.py` songs, `pco_plans.py` plans/templates, ...). Python projects import these
  instead of shelling out.

Both are stdlib-only python3 — nothing to install.

## Credentials

Resolution order (first hit wins) — full detail in `lib/pco_api.py`:

1. Explicit `PCOClient(app_id=..., secret=...)` arguments
2. `PCO_APP_ID` / `PCO_SECRET` environment variables
3. Nearest `.agents/PROJECT_SECRETS.md` walking up from the cwd — so each
   project automatically talks to the PCO account its own secrets name
4. `~/.config/pco/credentials.md` with optional `## <profile>` sections
   (`pco --profile church ...` / `$PCO_PROFILE`)

Secrets lines use the standard convention: ``- **PCO_APP_ID** = `value` ``.
`pco whoami` verifies auth and reports which source was used. NEVER commit
credentials into this (public) skills repo.

**Multiple churches**: `pco init` writes a template `~/.config/pco/credentials.md`
(mode 600) — one `## <church>` section per account. `pco profiles` lists them
and shows which source is in effect for the current cwd/profile. Working in a
project for one church? Put that church's token in the project's
`PROJECT_SECRETS.md` and no `--profile` is ever needed there.

## CLI usage

```
pco get /services/v2/songs --all          # every song, pagination handled
pco get /services/v2/songs/123?include=arrangements
pco post /services/v2/songs --data '{"data":{"type":"Song","attributes":{"title":"X"}}}'
pco patch <path> --data @body.json        # or --data - for stdin
pco delete <path>
pco whoami                                # who am I, which credentials
pco profiles                              # configured churches + source in effect
pco -P grace get /people/v2/people        # pick a church explicitly
pco init                                  # first-time credentials template
```

## Layer-1 named operations

Product subcommands wrap the generic client with real-world knowledge (see
`reference/`). Currently:

```
pco services tags                                   # tag groups (song/arrangement/person/media)
pco services songs export --out library.json        # whole library w/ tags, arrangements, keys
pco services songs tag <id|title> [--arrangement A] --add "Group:Tag" --remove Tag
pco services songs apply-tags --csv retag.csv       # bulk (song, arrangement, add, remove)
pco services songs audit --from library.json        # what lacks a tag per group
pco services songs chart <id|title> [--arrangement A] [--file chart.txt --key E]  # read / replace Lyrics & Chords
pco services song-usage <type> --after D --csv u.csv # songs used per plan: section, arrangement, key
pco services songs mix --usage all.csv --from library.json --after D [--before D] [--target 40/40/20] [--new-threshold 4] [--exclude RE] [--exclude-tags Christmas,Easter,Patriotic] [--keep-advent] [--songs]  # offline: slots bucketed ccli/historical/new vs target
pco services songs flag --ids 1,2 | --csv list.csv [--tag Worklist:Flagged] [--note "why"] [--clear] [--dry-run]  # scratch tag = exactly this list; prior list unflagged from local state
pco services songs lifecycle --usage all.csv --from library.json [--out lifecycle.csv] [--as-of D] [--new-threshold 4] [--dormant-months 24] [--intro-weeks 8] [--seasonal-tags Christmas,Easter,Patriotic] [--skip-intro-arr-tags "Modernized Hymn"] [--current-tag Current]  # offline: Queued/Introducing/Rotation/Dormant/Retired per song from usage -> retag CSV
pco services songs plan --usage all.csv --from library.json --sheet worship.csv --after D --before D [--as-of D] [--period-start D] [--out plan.csv] [--leaders leaders.csv --self "Your Name"] [--lookback-weeks 12] [--run 3/4] [--follow-up-weeks 4-8] [--candidates 8] [--overrides 2] [--exclude RE]  # offline: a period's first-pass song plan — obligations, mix tilt, 4 annotated slots per Sunday
pco services types                                  # service types
pco services templates <type>                       # plan templates
pco services plans <type> [--after D] [--before D]  # plans by date
pco services songs apply-plan --csv plan.csv --from library.json [--type "01. Modern"] [--overwrite] [--clear-empty] [--dry-run]  # make the plans match the CSV's `song` column (plan writes already-scheduled songs into it, so the CSV IS the desired state): slot n -> n-th Song(s)/song item; --overwrite replaces a different song, --clear-empty empties slots whose cell is blank; first arrangement; Congregational-named key if any
pco services plans <type> --leaders [--position "Music Director"] [--csv leaders.csv]  # who leads each plan (feeds songs plan)
pco services extend <type> --through 2026-10-31 --template Modern \
    --communion-template "Modern - Communion" [--communion-dates D,D] \
    [--no-communion-rule] [--dry-run]
pco services fill <type> --csv worship.csv [--after D] [--before D] \
    [--date-col d --title-col "Sermon Title" --series-col "Sermon Series" \
     --scripture-col "Sermon Scripture/"] [--blank-series "Stand Alone"] \
    [--overwrite] [--dry-run]
pco services set <type> 2026-10-18 --title T --series S --scripture "Mark 10:46-52"
```

pco services schedule <type> --csv worship.csv --position Speaker [--name-col Preacher] [--dry-run]
pco services schedule <type> --csv worship.csv --position Host --other-of "Justin Lowe,Melissa Lowe"
pco services remove-item <type> "Countdown" --all-templates --after 2026-08-24 [--dry-run]
```

`schedule` fills a team position from a name column, matching only against
that position's roster (honorifics stripped) — guests not on the roster stay
blank, filled positions are skipped, status U, no notification. `--other-of`
schedules the pair member NOT named (the non-preaching pastor hosts) unless
the row's text marks them absent ("Melissa @ Reynolds", "Justin at ...").
Song tagging: `assign_tags` replaces the whole set, so `tag`/`apply-tags` read
current tags first, apply add/remove by name, and enforce single-select
groups. Always `--dry-run` a bulk CSV. Tag taxonomies are per church —
keep them in the church's project, not here.
`remove-item` deletes items by exact title from templates (`--template`,
repeatable, or `--all-templates`) and/or plans (`--dates`, `--after/--before`).
`fill`/`set` write plan title + series and the "Scripture Reading" item's
description (the FUMC convention; `--scripture-item` to change). CSV column
defaults match the FUMC worship spreadsheet exported as CSV.

`plan` is the offline first-pass song planner for one scheduling period. It
never picks a song — it pins the carry-over **obligations** (an intro run under
3 uses continues on the Sundays left in its 4-Sunday window; a finished run owes
a follow-up 4–8 weeks later; Queued songs are listed as the pool a human debuts
from), prints the trailing-year **mix tilt** vs `--target` and a per-Sunday
theme summary, and writes a CSV of the template's four `Song(s)` slots per
Sunday. Columns: `date, liturgical_date, season, sermon_title, scripture,
theme_source` (`sheet`, or `lectionary-needed` when the sheet has neither title
nor scripture), `leader, slot` (1–4), `slot_name` (WE GATHER / WE RESPOND ×2 /
WE RESPOND-close), `obligation` (the song a rule pinned here), `song` (**blank —
a human fills it**), `candidates` (`;`-joined `Title [annotations]`).
Annotations are short tokens: the mix bucket (`ccli` / `hist` / `new:2/4`), the
Lifecycle state, `pero`, `speed:Fast`, `key:G(M)/E(F)` (the congregational key —
a trailing `?` means no key is named congregational, so it is unvetted),
`unsung`, `out-of-season`, and every rule the song would break if chosen —
`repeat-in-period`, `pero-cap`, `2nd-new-song`, `week1-opener`. Nothing is
filtered except `Lifecycle:Retired`: the perfect song wins (rule 0), so each
slot lists `--candidates` songs that break nothing **plus** `--overrides` that
do, merged back into merit order — the rule-breaking options stay visible and
never crowd out the clean ones.

`--after`/`--before` are the Sundays rows come out for; `--period-start`
(default `--after`) opens the wider window the period's own rules are scoped
to — `repeat-in-period` and the Pero 1-in-3 average — so planning the back half
of a period still counts the front half's songs as spent. With `--leaders` (a
`date,music_director` CSV from `plans --leaders`) plus `--self`, only your own
Sundays get candidates — someone else's carries its obligations and nothing more.
Ranking is deterministic: slot feel (each slot's empirical Speed mix, slot 4
inferred from history), mix tilt, recency, then usage count.

`extend` creates weekly plans after the latest existing one (times copied
from it in the org's local zone, template imported; communion template on
first Sundays or listed dates). `<type>` is an id or unique name substring.
Always `--dry-run` first. **Prefer adding a named operation here over
one-off API calls in a session** — the CLI is the deliverable.

## New machine setup

```
sh install.sh          # symlinks bin/pco into ~/.local/bin (stdlib python3 only)
pco init               # then paste each church's PAT into ~/.config/pco/credentials.md
pco profiles           # confirm; pco -P <church> whoami to verify auth
```

## Consuming the lib from a project

Symlink the skill into the project (`.agents/skills/pco -> this folder`),
then in the project's scripts:

```python
sys.path.insert(0, "<project>/.agents/skills/pco/lib")
from pco_api import PCOClient
from pco_services import get_arrangements, create_link_attachment, ...
```

A project adapter may subclass `PCOClient` to pin credentials to its own
secrets file regardless of cwd (see song-library's
`.agents/scripts/pco_client.py` for the pattern).

## Before writing to a product, read its reference

- `reference/services.md` — Services: the Song → Arrangement → Key
  hierarchy, where attachments belong, the `.mp3`-streamable rule, the
  chord_chart dialect (`TRANSPOSE KEY +n`, `PAGE_BREAK`, ALL-CAPS sections),
  rate limits, pagination/sideloading.
- `reference/plans.md` — Services plans: no date attribute (PlanTimes
  derive it), import_template after create, DST-safe time copying, date
  filtering, template quirks, FUMC ids.
- `reference/products.md` — map of every product's path prefix, universal
  conventions (discovery, pagination, filtering, rate limits, write envelope),
  and the rule for adding a product's notes.
- Other products (People, etc.): no field notes yet — start with
  `pco get /<product>/v2` for discovery and the official docs, and add
  `reference/<product>.md` when lessons accumulate.

## Growing the toolkit

Named helpers are added lazily, per real project need, to the matching
`lib/pco_<product>.py` — only operations a project actually uses, factored
generically (would another of my projects call this verbatim?). Project
business logic (file formats, naming schemes, sync flows) stays in the
project.

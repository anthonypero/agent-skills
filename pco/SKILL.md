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
pco services types                                  # service types
pco services templates <type>                       # plan templates
pco services plans <type> [--after D] [--before D]  # plans by date
pco services extend <type> --through 2026-10-31 --template Modern \
    --communion-template "Modern - Communion" [--communion-dates D,D] \
    [--no-communion-rule] [--dry-run]
pco services fill <type> --csv worship.csv [--after D] [--before D] \
    [--date-col d --title-col "Sermon Title" --series-col "Sermon Series" \
     --scripture-col "Sermon Scripture/"] [--blank-series "Stand Alone"] \
    [--overwrite] [--dry-run]
pco services set <type> 2026-10-18 --title T --series S --scripture "Mark 10:46-52"
```

`fill`/`set` write plan title + series and the "Scripture Reading" item's
description (the FUMC convention; `--scripture-item` to change). CSV column
defaults match the FUMC worship spreadsheet exported as CSV.

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

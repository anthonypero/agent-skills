---
name: pco
description: "Personal Planning Center (PCO) API toolkit: an authenticated CLI (`pco get|post|patch|delete <path>` + `pco whoami`) and an importable stdlib-Python client library. Use whenever a task reads or writes Planning Center data from any project — Services songs/arrangements/keys/attachments/plans, People, Check-Ins, Giving, or any other PCO product. Handles credential resolution (per-project PROJECT_SECRETS.md, env vars, or ~/.config/pco profiles), rate-limit retry, and pagination. reference/ holds hard-won PCO API knowledge — read it before writing to a product."
---

# pco — Planning Center API toolkit

One implementation, two surfaces:

- **CLI** (`bin/pco`) — generic authenticated verbs for agents, humans, and
  non-Python scripts. Any endpoint of any PCO product works; no domain code
  needs to exist first.
- **Python lib** (`lib/`) — `pco_api.py` (generic client) + per-product
  helper modules (`pco_services.py`, ...). Python projects import these
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

## CLI usage

```
pco get /services/v2/songs --all          # every song, pagination handled
pco get /services/v2/songs/123?include=arrangements
pco post /services/v2/songs --data '{"data":{"type":"Song","attributes":{"title":"X"}}}'
pco patch <path> --data @body.json        # or --data - for stdin
pco delete <path>
pco whoami
```

Install globally (optional): `sh install.sh` symlinks `bin/pco` into
`~/.local/bin`.

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
- Other products (People, etc.): no reference written yet — consult
  https://developer.planning.center/docs/ and add a reference file when
  lessons accumulate.

## Growing the toolkit

Named helpers are added lazily, per real project need, to the matching
`lib/pco_<product>.py` — only operations a project actually uses, factored
generically (would another of my projects call this verbatim?). Project
business logic (file formats, naming schemes, sync flows) stays in the
project.

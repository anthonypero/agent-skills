---
name: lastpass
description: "Fetch or store any API key, token, password, or credential the agent does not already have, from the shared LastPass vault that every machine in the fleet can reach. Use whenever a task needs a secret you cannot find, when a script or API call fails for want of a key, when the user says to put something in LastPass or asks where a credential lives, when setting up secrets on a new or freshly imaged machine, whenever `lpass` or lastpass-cli is mentioned or its session has expired, and when sweeping a project's PROJECT_SECRETS.md into the vault."
---

# LastPass

The fleet's secrets tier. Every API key and token the agents need lives in the LastPass shared folder `Shared-agent`, and the bundled `scripts/lp` wrapper reads one by name with a single command from any machine. The login self-heals from a master-password file, so an unattended session never stalls on a password prompt.

This is tier 0. **Before telling the user you are missing a credential, look here.**

```bash
scripts/lp get song-library/PCO_APP_ID     # a project's key
scripts/lp get GEMINI_API_KEY              # scope inferred from the cwd, then global
eval "$(scripts/lp env song-library)"      # the whole project's secrets into the shell
```

## Why it exists

Secrets used to live in per-project `.agents/PROJECT_SECRETS.md` files, which are gitignored and therefore absent on every machine but the one they were written on. A second machine could not run the same task. LastPass gives one vault that every machine reads, with the master password stored once per machine as a file instead of typed by a human who may not be there.

The `lpass` agent quits after one hour by default, which is what used to break unattended sessions on the secondary machine: the vault was there, the session was not. `lp` forces `LPASS_AGENT_TIMEOUT=0` (never expire) and re-logs-in from the master file whenever `lpass status` says it is logged out, so a stale session is a non-event.

Two subcommands deliberately do **not** log in: `lp status`, whose whole job is to report whether a session exists, and `lp sweep --dry-run`, which has to be runnable on a machine that has no session and no master file yet. Everything else logs in first.

## Naming convention

```text
Shared-agent/<scope>/<NAME>
```

- **`<scope>`** — the project folder name (`song-library`, `handyman`, `draftmark`), or `global` for a secret the whole fleet shares.
- **`<NAME>`** — the environment-variable name the code already expects: `PCO_APP_ID`, `GEMINI_API_KEY`, `R2_SECRET_ACCESS_KEY`. Uppercase, underscores, nothing else.

Keeping `<NAME>` identical to the env var is what makes `lp env` and the resolver contract below work without a translation table.

## Setup on a new machine

Install the CLI:

```bash
brew install lastpass-cli                 # macOS
sudo apt-get install -y lastpass-cli      # Debian / Ubuntu
```

Then get the master-password file onto the machine. It is copied host-to-host and never travels through a chat window, a note, a commit, or a command line:

```bash
mkdir -p ~/.secret-drop && chmod 700 ~/.secret-drop
scp <a-logged-in-host>:~/.secret-drop/lastpass-master ~/.secret-drop/
chmod 600 ~/.secret-drop/lastpass-master
scripts/lp login
```

`lp login` is idempotent — run it as often as you like. If the master file is missing, `lp` exits 2 and prints exactly these steps rather than hanging on a prompt.

Defaults are `agent@anthonypero.com` and `~/.secret-drop/lastpass-master`. Override either with `LP_USERNAME` / `LP_MASTER_FILE` in the environment, or in `~/.config/lastpass/env` as `KEY=value` lines. That file is parsed, not sourced, so it cannot execute anything; only those two keys are read, and a value already in the environment wins.

## Commands

| Command | What it does |
| --- | --- |
| `lp status` | Prints the logged-in identity, or `not logged in` and exits 1 |
| `lp login` | Ensures a session exists; self-heals from the master file |
| `lp get <scope>/<NAME>` | Prints one value and nothing else |
| `lp get <NAME>` | Same, trying the inferred project scope, then `global` |
| `lp put <scope>/<NAME>` | Creates or updates; the value is read from **stdin** |
| `lp ls [scope]` | Item names under `Shared-agent/[scope/]` |
| `lp env <scope> [NAME ...]` | `export NAME='value'` lines, shell-escaped, for `eval` |
| `lp sweep <PROJECT_SECRETS.md> --scope S` | Imports a project secrets file into the vault |
| `lp rm <scope>/<NAME> --yes` | Deletes an item |

`lp get` takes `--field password|username|url|notes|json` (default `password`). `lp put` takes `--username`, `--url` and `--notes`; any field not passed is left as it was, and `--notes` is sent only when you pass it, so an update never clobbers notes written by hand. `lp sweep` takes `--dry-run` and `--overwrite`. `lp env` takes `--secrets-md` to emit `` - **NAME** = `value` `` lines in the repo's `PROJECT_SECRETS.md` format instead of exports; a value containing a backtick is emitted as `- **NAME** = "value"` instead, with `\"` and `\\` escaped, because it could not survive the backtick form — `lp sweep` reads both.

Exit codes: `0` ok, `1` not logged in or an `lpass` failure, `2` master file missing, `3` item not found, `4` bad usage or bad input.

Reading and writing:

```bash
printf '%s' "$KEY" | scripts/lp put song-library/PCO_APP_ID --notes 'from the PCO dev console'
scripts/lp get song-library/PCO_APP_ID
scripts/lp ls song-library
```

The value always arrives on stdin. There is no flag to pass it as an argument, deliberately — see Security.

### Scope inference

Given a bare `NAME`, `lp get` tries `Shared-agent/<project>/<NAME>` first, where `<project>` is the basename of the nearest ancestor directory of the cwd that contains a `.agents/` folder (falling back to the git root), and then `Shared-agent/global/<NAME>`. When it finds neither it exits 3 and names both paths it tried, so the failure tells you where to put the secret.

Writes never infer a scope. `lp put` and `lp rm` require an explicit `<scope>/<NAME>`.

### Sweeping a PROJECT_SECRETS.md

```bash
scripts/lp sweep ~/Projects/song-library/.agents/PROJECT_SECRETS.md --scope song-library --dry-run
scripts/lp sweep ~/Projects/song-library/.agents/PROJECT_SECRETS.md --scope song-library
```

An entry is a **list item** whose first bold span is the name, followed by `=`: `` - **NAME** = `value` ``. Bare and `"double-quoted"` values work too. The match is anchored to that shape on purpose — prose that merely mentions a bold name (`The **GEMINI_API_KEY** env var = the key the code reads`) and markdown table rows are not entries and are never swept.

**Always dry-run first.** The dry run sorts every entry into four lists and prints no values:

| List | Meaning |
| --- | --- |
| would write | New in the vault; a real run creates it |
| already in vault (skipped) | An item of that name exists; a real run leaves it alone unless you pass `--overwrite` |
| skipped as placeholders | Empty, `...`, anything in angle brackets, `your-…`, `xxx`, `TODO` |
| unparsed entry-like lines | Looks like an entry but the name is not `A-Z 0-9 _` — shown by line number, truncated at the `=`, so you can fix the line or import it by hand |

Each swept name is reported with its value's character count and whether it was quoted. A surprising length is how you catch a line whose trailing prose comment got swallowed into the secret; unquoted values are flagged for exactly that reason. A dry run on a machine with no session says so and leaves the "already in vault" list empty, because it never logs in.

**A sweep never replaces a value already in the vault** unless you pass `--overwrite`. The vault copy may be the newer one, and its notes may have been written by hand. A real run notes the source file and date on items it creates (and on items it replaces under `--overwrite`), counts any `lp put` that fails without abandoning the rest of the file, and exits 1 if anything failed.

The source file is never modified. Verify a sweep with `lp ls <scope>` for the names, and `lp get <scope>/NAME | wc -c` for a byte count if you want to confirm a specific value landed — never with `lp env`, which prints the values themselves. Only then consider trimming the local file.

## Smoke test

Run this after changing `scripts/lp`, or on a machine you have not used the vault from before. It writes and then deletes one throwaway item, `Shared-agent/_test/PROBE`, and touches nothing else. The probe value is synthetic, so unlike a real secret it is safe to print.

```bash
cd ~/Projects/skills-developer/skills/lastpass

# 1. session
scripts/lp login
scripts/lp status                                  # expect: Logged in as agent@anthonypero.com.

# 2. create, read back, list, export
printf 'v1' | scripts/lp put _test/PROBE           # expect: created Shared-agent/_test/PROBE
scripts/lp get _test/PROBE                         # expect: v1
scripts/lp ls _test                                # expect: PROBE
scripts/lp env _test                               # expect: export PROBE='v1'

# 3. update in place (the edit path, not a second item)
printf 'v2' | scripts/lp put _test/PROBE           # expect: updated Shared-agent/_test/PROBE
scripts/lp get _test/PROBE                         # expect: v2
scripts/lp ls _test                                # expect: PROBE, once — not twice

# 4. sweep, dry run only, against a real secrets file
scripts/lp sweep ~/Projects/song-library/.agents/PROJECT_SECRETS.md --scope song-library --dry-run

# 5. clean up and confirm
scripts/lp rm _test/PROBE                          # expect: refused, exit 4, no --yes
scripts/lp rm _test/PROBE --yes                    # expect: removed Shared-agent/_test/PROBE
scripts/lp get _test/PROBE; echo "exit $?"         # expect: not found, exit 3
scripts/lp ls _test                                # expect: nothing
```

Error paths, all of which should print a message and the exit code shown:

```bash
scripts/lp get _test/PROBE --field bogus; echo "exit $?"          # 4, bad --field
scripts/lp get _test/PROBE --field; echo "exit $?"                # 4, --field needs a value
printf 'v' | scripts/lp put PROBE; echo "exit $?"                 # 4, scope never inferred on write
printf 'a\nb\n' | scripts/lp put _test/PROBE; echo "exit $?"      # 4, multi-line value
scripts/lp ls --bogus; echo "exit $?"                             # 4, unknown option
LP_MASTER_FILE=/nonexistent scripts/lp get _test/PROBE; echo "exit $?"   # 2 once logged out
```

Then the second machine, which is the whole point of the skill — `lpass` there is 1.3.7, old enough to behave differently:

```bash
scp scripts/lp virtuosicmini:/tmp/lp
ssh virtuosicmini '/tmp/lp login && /tmp/lp status && /tmp/lp ls && rm /tmp/lp'
```

A cross-machine check is worth doing at least once per change to the write path: `printf 'x' | scripts/lp put _test/PROBE` on the Studio, then `ssh virtuosicmini '/tmp/lp get _test/PROBE'`, then `scripts/lp rm _test/PROBE --yes`.

## Adopting this as tier 0 in another skill

A skill that needs a credential should resolve it in this order, and say which tier it used when it fails:

- **Tier 0** — **`lp get <project>/<VAR>`**, then `lp get global/<VAR>`: the fleet vault, works on every machine.
- **Tier 1** — the nearest `.agents/PROJECT_SECRETS.md` walking up from the cwd.
- **Tier 2** — the environment variable.
- **Tier 3** — a machine-local config such as `~/.config/<tool>/env`.

Tier 0 first is the point: it is the only tier that is true on a machine the user has not touched yet. Tiers 1 through 3 stay as fallbacks so nothing breaks while the vault is still filling up, and so a project can still pin a different key locally when it means to.

In practice, from a script:

```bash
KEY="$(lp get "$PROJECT/GEMINI_API_KEY" 2>/dev/null || true)"
[ -n "$KEY" ] || KEY="$(lp get global/GEMINI_API_KEY 2>/dev/null || true)"
[ -n "$KEY" ] || KEY="<fall back to PROJECT_SECRETS.md / env / ~/.config>"
```

When you find a secret in a lower tier that is not yet in the vault, offer to `lp put` it — that is how the vault fills in.

## Security

These are hard rules, not preferences.

- **Never print, `cat`, `echo`, copy or log the master-password file.** Redirecting it into `lpass login` on stdin is its only legitimate use. It does not go into a scratch file, a session note, a commit, or the conversation.
- **Never move the master file through chat.** Host-to-host `scp` only. If a machine cannot reach a logged-in host, ask the user to place the file themselves.
- **Never pass a secret value as a command-line argument.** `argv` is world-readable through `ps`, and it lands in shell history. `lp put` reads from stdin for this reason; keep it that way.
- **Never write a value to disk outside the vault** — not into session notes, restart files, logs, commits, or a scratch file "just to check it".
- **Never put a secret in the conversation.** Not to confirm it, not to show the user what you found, not truncated. Refer to it by name. `lp get` writes to stdout so a script can consume it; do not paste that output into a reply.
- **Never run the script under `set -x`**, and never add a debug mode that echoes values.
- This skills repo is **public**. No real credential belongs in any file here.

## Notes on lastpass-cli behavior

Learned the hard way; the script already handles all of these.

- **1.3.7 silently drops the whole-record "template" form for shared folders.** Piping a `Name:`/`Username:`/`Password:` block into `lpass add` for a `Shared-agent/...` name exits 0 and creates nothing on Ubuntu's 1.3.7, while working fine on 1.6.1. `lp` uses the one-field-per-call form (`--password`, then `--username` etc.) instead, which works on both.
- **`--sync=now` does not block.** Until the upload queue drains, the local cache serves a placeholder record — name `0`, empty password — so a read straight after a write returns nothing and a second write against it leaves a phantom entry. `lp` calls `lpass sync` explicitly after every write.
- **`lpass edit` refuses a `Name:` line that carries a share prefix**, answering "Use lpass mv to move items to/from shared folders".
- **`lpass add` does not deduplicate.** Adding an existing fullname creates a second item with the same name, after which every lookup reports "Multiple matches found". `lp put` resolves the name first and edits when it exists.
- **A password field is truncated at the first newline.** `lp put` rejects a multi-line value rather than storing a silently-truncated one; multi-line material belongs in `--notes`.
- Item lookups resolve the exact fullname through `lpass ls` and then act on the item id, never on `lpass`'s fuzzy name matching, which would happily resolve `API_KEY` to another project's item.
- **lastpass-cli older than 1.6 cannot update an existing shared-folder item.** On Ubuntu's 1.3.7, `lpass edit` exits 0 and changes nothing (by id or by name), and remove-then-re-add leaves a duplicate because the removal never reaches the server before the add does (both verified 2026-09-18). Plain creates, reads and lists work there; a create with extra fields, and removes, are unreliable. `lp put` refuses to update on such a version and says so. Do writes from a 1.6.x machine, or build lastpass-cli 1.6 from source on the Ubuntu box.
- **`lpass ls` and `show` serve the local cache.** A write from another machine is invisible until a sync, so `lp` syncs once per process before resolving anything.
- **LastPass rate-limits bursts of syncs.** After a dozen or so in quick succession every network call fails with `HTTP response code said error` for a minute or two, and a write sitting in the local upload queue can be discarded. `lp` syncs at most once per process, waits for the upload queue to drain after every write (warning if it does not), and treats a failed listing as an error rather than as "not found". If you see that message, wait a minute; the verified recovery is `lpass logout -f` then `lp login`.
- **Only a read from another machine proves a write.** A machine's own read after a write can come from its local blob with the queued edit already applied. The smoke test's cross-machine step exists for that reason.

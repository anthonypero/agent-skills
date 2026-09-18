# lastpass

One shared vault for every API key the agents need, reachable from any machine.

Secrets live in the LastPass shared folder `Shared-agent`, named `<scope>/<NAME>` — scope being a project folder name or `global`, and NAME being the environment variable the code already expects. `scripts/lp` wraps `lastpass-cli` so an agent can fetch one by name without a password prompt, on a laptop or on a headless box at three in the morning.

```bash
lp get song-library/PCO_APP_ID        # print one secret
lp get GEMINI_API_KEY                 # infer the scope from the current project
eval "$(lp env song-library)"         # load a whole project's secrets
printf '%s' "$KEY" | lp put song-library/NEW_TOKEN
lp ls song-library
```

The point is the login. The `lpass` agent expires after an hour, which used to leave unattended sessions on the second machine staring at a password prompt nobody was there to answer. `lp` holds the session open and, if it has lapsed anyway, logs back in from a master-password file at `~/.secret-drop/lastpass-master` before doing anything else. No command that touches the vault has to care whether a session already existed. The two that do not touch it — `lp status` and `lp sweep --dry-run` — never log in, so they still work on a machine with no session and no master file.

## Setup

```bash
brew install lastpass-cli                        # Linux: build 1.6 from source, see SKILL.md (apt ships 1.3.7, which cannot update)
scp <a-logged-in-host>:~/.secret-drop/lastpass-master ~/.secret-drop/
chmod 600 ~/.secret-drop/lastpass-master
lp login
```

The master file moves host to host and nowhere else — never through a chat window, a note, or a commit. Without it `lp` exits 2 and prints these steps instead of hanging.

## Moving a project onto it

`lp sweep <path/to/PROJECT_SECRETS.md> --scope <project>` imports an existing secrets file, skipping placeholders and names already in the vault, and leaving the source untouched. Run it with `--dry-run` first: it sorts the file's entries into what it would write, what is already in the vault, what it read as a placeholder, and what looked like an entry but could not be parsed — with each value's length and whether it was quoted, and no values printed. Pass `--overwrite` to replace vault items that already exist.

Full documentation, including the resolver contract for other skills and the `lastpass-cli` quirks the script works around, is in `SKILL.md`.

---
name: godaddy
description: "Read or change DNS records at GoDaddy with the `gddy` CLI. Use whenever a task touches DNS for a GoDaddy domain, whenever `gddy` or GoDaddy is mentioned, or when `gddy` hangs on a browser login."
---

# GoDaddy

Authenticate with a Personal Access Token in `GDDY_PAT`, never with `gddy auth login`. Get the token through the `lastpass` skill. It is stored under the **handyman** scope, not global: `export GDDY_PAT="$(~/.claude/skills/lastpass/scripts/lp get handyman GODADDY_PAT)"`.

```bash
gddy dns list example.com --type TXT < /dev/null
```

- If `GDDY_PAT` is empty, `gddy` hangs waiting for a browser login. Kill it and set the token.
- `dns add` appends. `dns set` and `dns delete` replace or remove every record of that type and name, so run them with `--dry-run` first.
- `gddy --help` and `gddy guide dns` cover everything else.

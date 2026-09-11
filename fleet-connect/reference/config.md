# The fleet-connect config

Everything host-specific — which sessions exist, what they are called on the phone, how they are kept alive, and how to relaunch them — lives in **one JSON file per machine**. The skill and its scripts read that file and dispatch on what it says. They contain no session names, labels, paths or job names of their own.

## Where it lives, and why

```text
${XDG_CONFIG_HOME:-~/.config}/fleet-connect/fleet.json
```

`$FLEET_CONNECT_CONFIG` overrides the path (useful for testing an alternate registry).

The config is **host-local state, not repo content.** It describes one machine's running services, it differs on every box, and on personal machines it names private project paths — so it belongs in the user's config directory, next to the machine's other per-host state, and never in the skill's repo. `~/.config/` (rather than `~/.claude/`) because nothing about this file is vendor-specific: the same file serves any runtime or channel adapter.

A host that already has a fleet registry should **symlink** rather than copy:

```bash
scripts/install.sh --link /var/agent-fleet/fleet.json
```

One source of truth, and the registry's own schema is a superset of this one.

## Schema

The schema is the `agent-fleet` **v2 `fleet.json`** shape, plus an optional `host` block and an optional `keepalive` block per agent. A fleet registry validates as a fleet-connect config unchanged; a launchd host's config is the same shape with different keep-alive fields. Unknown keys are ignored, so a registry's extra fields (`user`, `timers`, `data`, …) cost nothing here.

### `host` (optional)

| Field | Meaning |
| --- | --- |
| `keepalive` | `"launchd"` or `"systemd"` — which dispatch path this host takes |
| `session_prefix` | **launchd hosts only** — tmux prefix used to find sessions the config does not list (e.g. `cc-`) |
| `fleet_root` | systemd hosts: the fleet repo root holding `infra/`; defaults to the resolved config's own directory |

Omit the whole block and the host kind is **inferred**: if `infra/channel-up.sh` sits next to the resolved config (i.e. the config is a symlink into a fleet repo), the host is a fleet/systemd host; otherwise it is treated as launchd. That inference is what lets a fleet registry be symlinked in verbatim.

`session_prefix` is a safety net, not the inventory. Sessions found by prefix but absent from `agents` are still operated on, and reported as `unlisted:` so the config gets fixed. Only the launchd scripts implement this discovery — a systemd host dispatches to the fleet's own `infra/` machinery, which works from the registry alone, so `session_prefix` is ignored there.

### `defaults` (optional)

Merged under every agent entry — put `channel.type` here rather than repeating it.

### `agents` (required)

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | short identifier; also what you pass on the command line |
| `session` | yes | tmux session name (never rename a live one — the keep-alive keys on it) |
| `dir` | no | working directory, for humans reading the config |
| `channel.type` | no | channel adapter; defaults to whatever `defaults` says |
| `channel.label` | yes\* | the card name on the phone — what `/rc <label>` re-arms; falls back to `name` |
| `keepalive.managed_by` | no | `launchd` / `systemd` / `bootstrap` — informational per agent |
| `keepalive.job` | no | the keep-alive job's label or unit name |
| `keepalive.arm_job` | no | a job that re-creates in-session timers; kicked after a restart |
| `keepalive.script` | no | the session-up script whose `LAUNCH=` line is the launch command (launchd hosts) |
| `status` | no | registry lifecycle field; anything starting with `staged` marks an agent that is not yet stood up. Missing means live. A default run targets live agents only; naming a staged agent explicitly overrides that |
| `notes` | no | free text |

`keepalive.script` matters: a restart reconstructs the launch command from that script's `LAUNCH=` assignment, **never** from the running process's arguments — a process can be running with wrong or partial flags, and `ps`-reconstruction would faithfully relaunch the mistake. Without it, the restart falls back to a `*-resume` alias in `$FLEET_CONNECT_ALIAS_FILE` (default `~/.zshrc`), and skips the session if that also fails.

`~` in `dir` and `keepalive.script` is expanded.

## Minimal launchd example

```json
{
  "version": 2,
  "host": { "keepalive": "launchd", "session_prefix": "cc-" },
  "defaults": { "channel": { "type": "claude-remote-control" } },
  "agents": [
    {
      "name": "example",
      "session": "cc-example",
      "dir": "~/Projects/example",
      "channel": { "label": "Example" },
      "keepalive": {
        "managed_by": "launchd",
        "job": "com.example.example.session",
        "script": "~/Projects/example/bin/ex-session-up.sh"
      }
    }
  ]
}
```

Full templates: `assets/fleet.launchd.example.json`, `assets/fleet.systemd.example.json`.

## Checking it

```bash
scripts/fleet-connect.sh --where     # config path, host kind, resolved agent list
scripts/fleet-connect.sh --dry-run   # what it would do, no keystrokes
```

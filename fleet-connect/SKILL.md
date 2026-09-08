---
name: fleet-connect
description: "Use for the always-on, phone-driven agent sessions on this machine — persistent tmux sessions kept alive by launchd or systemd that surface as Remote Control cards on the phone. Trigger when the user wants to reconnect or re-arm Remote Control after a `/login` or re-auth (so sessions reappear on the phone), restart sessions stranded on the wrong account, add / rename / remove a persistent session, or diagnose this keep-alive + channel system (sessions missing from the phone, duplicate or ghost cards, empty or unresponsive sessions). Reads a per-host config listing the sessions and dispatches to that host's keep-alive machinery."
---

# fleet-connect

Reconnect and repair the **always-on agent sessions** — the tmux sessions that stay up around the clock and appear on the phone as chat cards.

The skill is deliberately thin, and deliberately knows nothing about any particular machine. Every host-specific fact lives in one config file; the deep architecture and failure-mode material lives in the `agent-fleet` docs. This file is the capability and the dispatch rule, nothing more.

## The capability

Four jobs, in escalating cost:

| Job | When |
| --- | --- |
| **Re-arm the channel** (`/rc <label>` into every session) | After a manual `/login` token refresh — the tunnel drops for every running session |
| **Restart stranded sessions** | Re-arming reports sessions still dark, or cards answer on the phone but do nothing |
| **Add / rename / remove a session** | Standing up a new persistent agent, or retiring one |
| **Diagnose** | A card is missing, duplicated ("ghost"), empty, or unresponsive |

The rule that explains most of it: **Remote Control binds to the account active at process launch and never re-binds while the process lives.** A token refresh can be fixed by typing `/rc` (re-arm); an account switch under a live process cannot — that needs a restart.

## Everything host-specific is in the config

```text
${XDG_CONFIG_HOME:-~/.config}/fleet-connect/fleet.json     # $FLEET_CONNECT_CONFIG overrides
```

One entry per persistent session: name, tmux session, phone label, keep-alive job, launch script. The schema is the `agent-fleet` v2 `fleet.json` shape plus an optional `host` block — so a fleet host can symlink its registry in unchanged, and a launchd host writes the same shape with launchd fields. **Read [reference/config.md](reference/config.md) before touching it.**

First run on a machine:

```bash
scripts/install.sh                                    # config from the template for this OS
scripts/install.sh --link /path/to/fleet.json         # or point at an existing registry
scripts/fleet-connect.sh --where                      # confirm: config, host kind, agents
```

If a session shows up as `unlisted:` in the output, the config is behind the machine — add it.

## Use it

```bash
scripts/fleet-connect.sh              # re-arm the channel on every agent of this host
scripts/fleet-connect.sh --dry-run    # show targets, send no keystrokes
scripts/fleet-connect.sh --restart    # escalate: restart the processes, then re-arm + verify
scripts/fleet-connect.sh NAME...      # limit to these agents (config name or session name)
```

Always run it from a shell **outside** the sessions it operates on — a plain session over SSH is the natural orchestrator. A session cannot reliably re-arm or restart itself; the scripts skip self.

Escalate only after re-arming fails: `--restart` quits each runtime process and relaunches it with `--continue` (history preserved, but it costs a resume), so it is the second move, never the first.

## How dispatch works

`scripts/fleet-connect.sh` reads `host.keepalive` from the config — or infers it when absent — and hands off:

| Host kind | Sessions | Dispatches to |
| --- | --- | --- |
| **`systemd`** | a fleet registry | the fleet's own `infra/channel-up.sh` and `infra/agent-restart.sh` |
| **`launchd`** | per-project scripts | this skill's `scripts/launchd-connect.sh` / `scripts/launchd-restart.sh` |

The launchd scripts are the substantive local code: they discover live sessions, type `/rc <label>`, verify against the footer status line, and — for a restart — reconstruct each launch command from its session-up script's `LAUNCH=` line (never from the running process's arguments, which may already be wrong). They take the same flags as the dispatcher and can be run directly.

A hook matching `auth_success` in `~/.claude/settings.json` can fire the re-arm automatically after every `/login`, in the session the login was typed into — which is exactly the outside-tmux orchestrator the scripts want. Keep it `async` so `/login` returns immediately.

## Read this before debugging

The architecture and the hard-won failure modes are maintained in the `agent-fleet` repo, not here:

- [docs/channel-and-keepalive.md](https://github.com/anthonypero/agent-fleet/blob/main/docs/channel-and-keepalive.md) — keep-alive vs channel, the `/login` re-arm, the account-switch failure mode, ghost cards, push suppression, the version-dependent armed signature.
- [docs/creating-an-agent.md](https://github.com/anthonypero/agent-fleet/blob/main/docs/creating-an-agent.md) — the add-an-agent runbook and the naming rules.

Adding a session is a two-sided change: the host's own machinery (a registry entry plus generated units, or a session-up script plus a keep-alive job) **and** an entry in the fleet-connect config. Renaming a live session is the classic self-inflicted wound — the keep-alive keys on the session name and will spawn an empty duplicate within its next cycle, so rename the session, its scripts, and the config together or not at all.

## Caveats

- **Sessions must be idle.** Sending keys into a session mid-generation corrupts input, and it appends to any half-typed draft left in the composer. There is no reliable tmux signal for idle-vs-busy; a fleet host gates this in its own send primitive, the launchd path does not.
- **A keystroke suppresses the mobile push.** Poking a session makes push notification delivery believe a human is active, so a scheduled push around that moment is swallowed. Keep scripted pokes rare and away from the hours when pushes are due.
- **Verification is version-dependent.** The armed indicator has changed shape across Claude Code TUI releases; the scripts read the footer status line only — never a whole-pane grep, which the transcript can spoof. If verification suddenly reports everything dark right after an upgrade, suspect the signature before the sessions.
- **Ghost cards are server-side.** Duplicate cards after a hard restart are relay registrations, not local processes — archive them on the phone; re-running the restart only mints more.
- **Version floor.** Remote Control needs Claude Code v2.1.51+.

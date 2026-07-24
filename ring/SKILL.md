---
name: ring
description: "Install, change, or remove a loud audible alert — a 'ring' — that plays whenever Claude Code finishes a turn and hands control back, so the user notices from across the room or another room. Use when the user asks to be pinged/alerted/notified with a SOUND when Claude is done or needs them (e.g. 'ring me when you need me', 'play a bell/horn/chime when you're done', 'make a noise when you hand back', 'audible notification', 'alert me when it's my turn'), or asks to change that sound's volume/repeat, scope it globally vs to one project, or turn it off. Implemented as a Claude Code Stop hook; ships a bundled car-horn sound so it works on any Mac even without iMovie."
---

# ring

Plays a sound every time Claude finishes responding (a Claude Code **Stop** hook), so the user
knows it's their turn without watching the screen. Ships a bundled horn sound and copies it to a
stable per-machine location, so the hook never depends on where this skill's checkout lives.

macOS only (uses `afplay`).

## Install

Run the installer (in this skill's directory). Default scope is **global** — rings in every
project on this machine:

```bash
scripts/install-ring.sh
```

Then **reload to activate**: tell the user to open `/hooks` once, or restart Claude Code. Claude
Code's settings watcher only reliably picks up a settings file that already existed when the
session started, so a freshly written hook usually needs one reload before its first ring.

Global scope writes to `~/.claude/settings.json` — every other setting there is preserved (safe
merge; the previous version is backed up to `settings.json.bak`). Because that is a machine-wide
change, confirm with the user before installing global if there's any doubt.

## Options

| Flag | Effect | Default |
| --- | --- | --- |
| `--scope global` | Ring in every project → `~/.claude/settings.json` | (default) |
| `--scope project` | Ring only in the current project → `./.claude/settings.local.json` | |
| `--volume N` | `afplay` volume multiplier (`1.0` = normal) | `4` |
| `--repeat N` | Play N times in a row | `3` |
| `--sound PATH` | Use a different sound file | bundled `comedy-horns.caf` |
| `--settings PATH` | Write to an exact settings file (overrides `--scope`) | |
| `--uninstall` | Remove the ring hook, keep everything else | |
| `--print` | Show what would be written, change nothing | |

Examples: `scripts/install-ring.sh --volume 2 --repeat 1` (gentler); `scripts/install-ring.sh
--scope project` (this project only); `scripts/install-ring.sh --uninstall` (remove — match the
`--scope`/`--settings` it was installed with).

## Across machines

The skill travels with the skills-developer repo (git). Settings files do **not** sync, so run the
installer **once per machine**. Re-running is safe and idempotent — it replaces its own hook rather
than stacking duplicates (matched by the `# ring-skill` marker in the command).

## Notes

- **Double-ring:** installing both global *and* project scope makes that one project ring twice.
  Pick one; uninstall the redundant scope.
- **Change the default sound for everyone:** replace `assets/comedy-horns.caf` and re-run the
  installer on each machine. Preview any candidate first with `afplay "<path>"`.

---
name: ring
description: "Play a loud, one-off attention sound (a 'ring') at the moment you hand control back with something the user is waiting for — so they notice from across the room or another room. Use ONLY on demand: when the user says they're stepping away / will be away from their desk / going AFK, or explicitly asks to be pinged or alerted with a sound when a specific long-running task finishes or when you next need them. It is a single alert for that one occasion, NOT a standing setting. Also use when the user asks to change the ring's sound/volume, preview options, or when they complain a previous alert fired too often."
---

# ring

Raises a popup dialog on demand and **keeps a loud sound playing until the user clicks Dismiss** — so the user is pulled back from another room, and sees what they were called back for when they get there. Clicking Dismiss silences the sound immediately. Ships a bundled car-horn sound. macOS only (uses `afplay` and `osascript`).

## The model: one-off, never automatic

This is a deliberate, single alert — **not** a persistent behavior. Do **NOT** install a `Stop` hook (or any settings-based hook) for this: a Stop hook fires after _every_ turn and quickly becomes annoying. Instead, just run the play script in the one turn where it's warranted.

## When to ring

Ring when **both** are true:

1. The user has signalled they're away or asked to be pinged — e.g. "I'm stepping away," "ring me when the build's done," "AFK, honk when you need me," "let me know when this finishes."
2. You are handing back the thing they were waiting for, or you genuinely need their input to continue (task complete, a decision needed, an error that blocks progress).

Ring **once** for that occasion, then consider it spent — do not ring on subsequent turns unless they ask again. Never ring during ordinary back-and-forth work.

## How to ring

Run the play script (in this skill's directory):

```bash
scripts/ring.sh --message "Build finished — 2 tests failing, see terminal."
```

Always pass `--message` with a one-line summary of _why_ you're ringing (done / decision needed / blocked on what). It becomes the popup text; the popup persists until dismissed, so it should stand on its own for someone walking back to the desk.

**The script blocks until the user clicks Dismiss.** That is intended: the sound loops the whole time the popup is up, and Dismiss stops it at once (within about 0.2 s) and returns. Nothing is left running — no orphan `afplay`, loop or timer. Run it as the _last_ thing in the turn.

**The sound starts only once the popup is confirmed on screen.** Each popup route gets ~8 s to be confirmed (`RING_CONFIRM_SECONDS`, default 8), and when the System Events route blocks, the plain dialog is tried after it, so in the worst case the script waits ~17 s before giving up. If no popup can be confirmed (Automation permission denied or its consent prompt still unanswered, no GUI session), the sound plays `--repeat` times instead of looping and the script **exits 3** with the reason on stderr. On exit 3, tell the user in the terminal that the popup failed and why; if the reason names an Automation prompt, they need to allow it in System Settings > Privacy & Security > Automation.

Exit codes — only `0` means the user clicked Dismiss:

| Code | Meaning |
| --- | --- |
| `0` | Dismiss clicked (or `--no-popup` / `--dry-run` finished) |
| `1` | sound file or `afplay` missing |
| `2` | usage error: unknown option, or a missing or non-numeric value |
| `3` | no popup could be confirmed; the sound played `--repeat` times instead |
| `4` | the popup was up but ended without a Dismiss click (dialog error or Apple event timeout, osascript killed); the sound was stopped, and a System Events dialog may still be on screen |
| `129` / `130` / `143` | interrupted by SIGHUP / SIGINT / SIGTERM; the sound and the popup were stopped |

On `4`, tell the user the alert may not have been seen. A `RING_CONFIRM_SECONDS` that is not a positive whole number is ignored with a warning, and the default is used.

Defaults: bundled `comedy-horns.caf`, amplified (`--volume 4`), looping under a persistent popup, with the sound capped at 300 s. Options:

| Option | Meaning |
| --- | --- |
| `--message TEXT` | popup text (always pass this) |
| `--volume N` | `afplay` multiplier, `1.0` = normal (default 4) |
| `--max-seconds N` | silence the sound after N seconds even if the popup is still up; the popup stays. Default 300, `0` = no cap. The dialog's Apple event timeout is set to the cap plus 60 s (a week with no cap) instead of AppleScript's default 120 s, which would otherwise end the wait, and the sound, early. If that timeout is reached the script exits 4 |
| `--sound PATH` | alternate sound file |
| `--no-popup` | sound only: play `--repeat` times and exit, no dialog (the old behavior) |
| `--repeat N` | number of plays with `--no-popup`, and in the no-popup fallback (default 3); a confirmed popup loops instead |
| `--dry-run` | print what would happen; no sound, no dialog |

The dialog is raised through System Events, activated first, so it comes to the front rather than opening behind whatever window is on screen. If System Events is refused or blocked, a plain dialog is tried (stderr says so); it can open behind other windows. Neither form can be pinned to a particular display: macOS chooses where it opens, so on a multi-display Mac it may be on the other screen.

If a long-running task is what they're waiting on, kick it off, let it run, and call `ring.sh` in the same response where you report that it finished.

## Changing the sound

Preview any candidate first with `afplay "<path>"`, then either pass `--sound PATH` for a one-time change, or replace `assets/comedy-horns.caf` to change the default everywhere (it travels with the skills repo). macOS system sounds live in `/System/Library/Sounds/`; the iLife set (if iMovie is installed) is under `/Applications/iMovie.app/Contents/Resources/iLife Sound Effects/`.

---
name: ring
description: "Play a loud, one-off attention sound (a 'ring') at the moment you hand control back with something the user is waiting for — so they notice from across the room or another room. Use ONLY on demand: when the user says they're stepping away / will be away from their desk / going AFK, or explicitly asks to be pinged or alerted with a sound when a specific long-running task finishes or when you next need them. It is a single alert for that one occasion, NOT a standing setting. Also use when the user asks to change the ring's sound/volume, preview options, or when they complain a previous alert fired too often."
---

# ring

Raises a popup dialog on demand and **keeps a loud sound playing until the user clicks Dismiss** —
so the user is pulled back from another room, and sees what they were called back for when they get
there. Clicking Dismiss silences the sound immediately. Ships a bundled car-horn sound. macOS only
(uses `afplay` and `osascript`).

## The model: one-off, never automatic

This is a deliberate, single alert — **not** a persistent behavior. Do **NOT** install a `Stop`
hook (or any settings-based hook) for this: a Stop hook fires after *every* turn and quickly
becomes annoying. Instead, just run the play script in the one turn where it's warranted.

## When to ring

Ring when **both** are true:
1. The user has signalled they're away or asked to be pinged — e.g. "I'm stepping away," "ring me
   when the build's done," "AFK, honk when you need me," "let me know when this finishes."
2. You are handing back the thing they were waiting for, or you genuinely need their input to
   continue (task complete, a decision needed, an error that blocks progress).

Ring **once** for that occasion, then consider it spent — do not ring on subsequent turns unless
they ask again. Never ring during ordinary back-and-forth work.

## How to ring

Run the play script (in this skill's directory):

```bash
scripts/ring.sh --message "Build finished — 2 tests failing, see terminal."
```

Always pass `--message` with a one-line summary of *why* you're ringing (done / decision needed /
blocked on what). It becomes the popup text; the popup persists until dismissed, so it should stand
on its own for someone walking back to the desk.

**The script blocks until the user clicks Dismiss.** That is intended: the sound loops the whole
time the popup is up, and Dismiss stops it instantly and returns. Nothing is left running — no
orphan `afplay`. Run it as the *last* thing in the turn.

Defaults: bundled `comedy-horns.caf`, amplified (`--volume 4`), looping under a persistent popup,
with the sound capped at 300 s. Options:

| Option | Meaning |
| --- | --- |
| `--message TEXT` | popup text (always pass this) |
| `--volume N` | `afplay` multiplier, `1.0` = normal (default 4) |
| `--max-seconds N` | silence the sound after N seconds even if the popup is still up; the popup stays. Default 300, `0` = no cap |
| `--sound PATH` | alternate sound file |
| `--no-popup` | sound only: play `--repeat` times and exit, no dialog (the old behavior) |
| `--repeat N` | number of plays — **only meaningful with `--no-popup`** (default 3); with the popup the sound loops instead |
| `--dry-run` | print what would happen; no sound, no dialog |

The dialog is raised through System Events so it comes to the front rather than opening behind
whatever window is on screen.

If a long-running task is what they're waiting on, kick it off, let it run, and call `ring.sh` in
the same response where you report that it finished.

## Changing the sound

Preview any candidate first with `afplay "<path>"`, then either pass `--sound PATH` for a one-time
change, or replace `assets/comedy-horns.caf` to change the default everywhere (it travels with the
skills repo). macOS system sounds live in `/System/Library/Sounds/`; the iLife set (if iMovie is
installed) is under `/Applications/iMovie.app/Contents/Resources/iLife Sound Effects/`.

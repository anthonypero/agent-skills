---
name: ring
description: "Play a loud, one-off attention sound (a 'ring') at the moment you hand control back with something the user is waiting for — so they notice from across the room or another room. Use ONLY on demand: when the user says they're stepping away / will be away from their desk / going AFK, or explicitly asks to be pinged or alerted with a sound when a specific long-running task finishes or when you next need them. It is a single alert for that one occasion, NOT a standing setting. Also use when the user asks to change the ring's sound/volume, preview options, or when they complain a previous alert fired too often."
---

# ring

Plays a loud sound **once**, on demand, so the user knows it's their turn without watching the
screen. Ships a bundled car-horn sound. macOS only (uses `afplay`).

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
scripts/ring.sh
```

Defaults: bundled `comedy-horns.caf`, amplified (`--volume 4`), played 3× in a row. Options:
`--volume N` (`1.0` = normal), `--repeat N`, `--sound PATH`, `--dry-run` (print, make no sound).

If a long-running task is what they're waiting on, kick it off, let it run, and call `ring.sh` in
the same response where you report that it finished.

## Changing the sound

Preview any candidate first with `afplay "<path>"`, then either pass `--sound PATH` for a one-time
change, or replace `assets/comedy-horns.caf` to change the default everywhere (it travels with the
skills repo). macOS system sounds live in `/System/Library/Sounds/`; the iLife set (if iMovie is
installed) is under `/Applications/iMovie.app/Contents/Resources/iLife Sound Effects/`.

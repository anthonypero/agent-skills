# Start / Restart

**Sync first.** If the project is a git repo with a remote, run `git pull --ff-only` before reading anything — another machine may own the newest handoff. If it fails (the branch has diverged, or local changes block it), say so and stop; do not merge, rebase, or force your way past it. A branch with no upstream is not a failure — note it and continue. A stale tree makes every file below untrustworthy.

**Resolve the restart file and notes dir** — see [SKILL.md](SKILL.md), "Where the session files live". In per-host mode with no host slug, stop and ask; do not read another host's handoff as if it were yours.

Read the restart file and follow it. It is the single fixed entry point for resuming work, and it is kept current — written fresh or reset to a stub at every wrap — so trust its contents.

- **Live handoff** (the file describes where we are): follow it. It orients you — state of play, how to run the project, key files — and names the session note(s) worth reading. Read those.
- **Idle stub** (the file says there is no live handoff): nothing is in flight. Read the most recent note in the notes dir for recent context, then await the user's direction. Do not invent next steps.

References flow `restart → notes`, never the reverse: restart points at the notes that matter; notes never point forward.

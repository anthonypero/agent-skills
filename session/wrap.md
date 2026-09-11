# Wrap / End Session

**Resolve the restart file and notes dir** first — see [SKILL.md](SKILL.md), "Where the session files live". In per-host mode you write only your own host's subtree; the top-level `.agents/restart.md` router stub is never yours to overwrite.

Then run these in order, so the note and restart ride in the same commit as the work:

1. **Write the session note** — follow [notes.md](notes.md). Lean, historical.
2. **Make the restart file true again:**
   - A live thread carries into the next session → write a fresh handoff: state of play, how to run the project, key files, and which note(s) the next session should read.
   - Clean stopping point, nothing in flight → **reset it to the idle stub** (never leave stale content):

     > No live handoff. Read the most recent note in `<the notes dir>`, then await direction.

   The bar is "is the restart file still true?", not "did I feel like updating it?"

3. **Commit and push.** If a `repo-master` skill or agent exists, use it for the git work — it owns the commit conventions and identity checks. Otherwise commit and push directly. Stage everything together — code, the new note, and the restart file — in a single commit. **Pull before you push**: `git pull --rebase`. In per-host mode each host touches only its own subtree plus append-only shared files, so the rebase should be clean; if it conflicts anyway, stop and report it — do not resolve blindly.

After a wrap, the restart file is never stale: it holds either a fresh handoff or the stub.

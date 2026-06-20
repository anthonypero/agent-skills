# Wrap / End Session

Run these in order, so the note and restart ride in the same commit as the work:

1. **Write the session note** — follow [notes.md](notes.md). Lean, historical.
2. **Make `${PROJECT_DIR}/.agents/restart.md` true again:**
   - A live thread carries into the next session → write a fresh handoff: state of play, how to
     run the project, key files, and which note(s) the next session should read.
   - Clean stopping point, nothing in flight → **reset it to the idle stub** (never leave stale
     content):

     > No live handoff. Read the most recent note in `.agents/notes/`, then await direction.

   The bar is "is `restart.md` still true?", not "did I feel like updating it?"
3. **Commit and push.** If a `repo-master` skill or agent exists, use it for the git work — it
   owns the commit conventions and identity checks. Otherwise commit and push directly. Stage
   everything together — code, the new note, and `restart.md` — in a single commit.

After a wrap, `restart.md` is never stale: it holds either a fresh handoff or the stub.

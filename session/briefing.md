# Briefing

A start-of-day look across everything that can need the user today: calendar, inbox, tickets, reviews, and the state of each thread of work. It gathers facts from live sources, reports what needs action, and then rewrites the restart file's agenda to match. Run it at the start of a day, usually right after `start`.

## Sources come from config, not from this file

This file owns the procedure. Which sources to check, and how to reach each one, belongs to the project and the machine, so it lives in two config tiers, each in a `session/` subfolder named for this skill:

1. `<project>/.config/session/briefing.md` holds the project tier. It lists this project's sources: its ticket systems, repos, boards, and the accounts or browsers each one needs.
2. `~/.config/session/briefing.md` holds the user tier. It lists sources that apply to every project on this machine, such as a personal calendar. `$XDG_CONFIG_HOME` is honored.

Commit the project tier with the project, so every machine and collaborator briefs from the same sources. Never commit the user tier; it describes this machine and its owner.

Read both when they exist. The project tier adds to the user tier, and where both describe the same source, the project tier wins. If neither exists, tell the user there are no configured sources, offer to write the project tier with them, and stop.

Each source entry should say what to check, the command or tool that reaches it, and any quirk that has produced a wrong answer before.

## Procedure

1. **Read the handoff first.** Read the restart file, the latest session note's Deferred section, and, in a metaproject, every `.agents/subprojects/*/restart.md`. These tell you what is in flight. They are not the facts about it.
2. **Gather from every configured source.** Run independent checks in parallel. A source that fails (expired auth, a closed browser session) is reported as unchecked, with the reason. Never skip it silently.
3. **Verify before you state.** A handoff claim ("waiting on review", "ticket open") is true only when a live source confirms it today. Where a summary field and a detailed history disagree, trust the history. Anything a source shows that no handoff knows about is news, so call it out.
4. **Report**, grouped by urgency rather than by source:
   - **Today:** meetings with their times, deadlines, and anything that expires or goes stale today.
   - **Needs a reply or action:** unanswered messages that matter, new assignments, reviews waiting on the user.
   - **Waiting on others:** what is blocked, and on whom.
   - **Thread status:** one line per active subproject or thread, verified.
   - **Upkeep:** auth that expires soon, leftover watchers, chores owed.

   Keep each item to one line with its identifier (ticket number, PR number, time). Draft nothing and send nothing during the briefing. Replies are follow-up work the user picks.

5. **Refresh the agenda.** Rewrite the restart file's agenda from what the briefing found, and date it. The briefing is how the agenda stays true between wraps.

## Rules

- **Read only.** A briefing never replies, comments, takes, closes, or RSVPs. It reports, and the user decides.
- **Close what you open.** Any browser session the briefing uses is closed before hand-back.
- **Keep the config current.** The config tiers are the briefing's memory, so update them as part of every briefing, before hand-back:
  - **New source.** When something that needs briefing turned up somewhere no entry covers (a new ticket queue, a recurring email type, a board, a teammate's channel), add an entry for it.
  - **Quirk.** When a check misreports or a command fails in a way that could recur, record the quirk and the working command on that source's entry.
  - **Dead source.** When a source no longer yields anything worth briefing, remove its entry or narrow it.
  - **Which tier.** A source tied to this project goes in the project tier. One that applies to every project on this machine goes in the user tier; create `~/.config/session/briefing.md` for it if needed.
  - **Tell the user.** Name each config change in one line of the report.

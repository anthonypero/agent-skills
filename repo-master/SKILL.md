---
name: repo-master
description: "Use when the user asks to commit, push, pull, merge, branch, tag, stash, rebase, or perform any git or GitHub operation including creating pull requests, checking status, viewing logs, or managing remotes."
agent: repo-master
model: sonnet
---

# Git Operations

## Install (one-time, per machine)

This skill delegates to a `repo-master` **agent** so git work never runs on the parent session's model. Claude Code only discovers agents in `~/.claude/agents/` (user-wide) or `<project>/.claude/agents/` (per project), never inside a skill folder, so the bundled definition at `agents/repo-master.md` must be symlinked into place. This is the step that gets missed on a fresh machine: the skill symlink under `~/.claude/skills/` is not enough on its own.

```sh
mkdir -p ~/.claude/agents
ln -sfn "$(realpath ~/.claude/skills/repo-master)/agents/repo-master.md" ~/.claude/agents/repo-master.md
```

`realpath` matters because `~/.claude/skills/repo-master` is itself a symlink into the skills repo; linking through the real path keeps the agent tracking that repo. Agents register at session start, so the new agent appears in the Agent tool's available types **only in the next session**. It runs on the `model:` declared in `agents/repo-master.md` (Sonnet by default); edit that line to change tiers.

## On every invocation

1. **Check for the `repo-master` agent** in the Agent tool's available types.
2. **If it exists**, delegate the whole git task to it and stop doing git work yourself.
3. **If it does not exist**, run the install block above, tell the user the agent will be available from the next session, then complete the current task directly using the reference files below. Do not skip the install, and do not silently fall back without saying so.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Agent not in the available types | Not installed, or installed this session | `ls -la ~/.claude/agents/repo-master.md`; if missing, run the install block. If present, start a new session. |
| Symlink exists but points at a dead path | Skills repo moved, or link was made through `~/.claude/skills/...` instead of the real path | Re-run the install block; `realpath` resolves the real target. |
| Agent runs but ignores the conventions | Its `skills:` frontmatter did not load, or the agent file is a stale copy rather than a symlink | Confirm `~/.claude/agents/repo-master.md` is a symlink (`ls -la`), not a copied file, and that `agents/repo-master.md` still lists `repo-master` under `skills:`. |
| Push refused or wrong identity | `gh` is logged in as a different account than the repo owner | Follow [authentication.md](authentication.md); `gh auth status` shows the active account. |
| Agent is on the wrong model tier | `model:` in `agents/repo-master.md` | Edit that line; takes effect next session. |

## Routing

Consult the reference file that matches the task:

| When you need to... | Read |
| --- | --- |
| Verify identity or repo ownership | [authentication.md](authentication.md) |
| Run a git/gh command (clone, push, remote…) | [operations.md](operations.md) |
| Write a commit message | [conventions.md](conventions.md) |

**Before any operation that writes to a remote** (push, PR create, repo create), complete the identity verification in [authentication.md](authentication.md) first.

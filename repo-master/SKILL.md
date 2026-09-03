---
name: repo-master
description: "Use when the user asks to commit, push, pull, merge, branch, tag, stash, rebase, or perform any git or GitHub operation including creating pull requests, checking status, viewing logs, or managing remotes."
agent: repo-master
model: sonnet
---
# Git Operations

## Install (one-time, per machine)

This skill delegates to a `repo-master` **agent** so git work never runs on the parent session's model. Claude Code only discovers agents in `~/.claude/agents/` (user-wide) or `<project>/.claude/agents/` (per project), never inside a skill folder, so the bundled definition must be installed there. Symlink it so it tracks this repo:

```sh
mkdir -p ~/.claude/agents
ln -sfn "$(realpath <path-to-this-skill>)/agents/repo-master.md" ~/.claude/agents/repo-master.md
```

`realpath` matters when the skill directory is itself a symlink. Verify with a new session: the `repo-master` agent should appear in the Agent tool's available types. The agent runs on the `model:` declared in `agents/repo-master.md` (Sonnet by default); edit that line to change tiers.


**PRIOR TO TAKING ANY OTHER ACTION, IMMEDIATELY search for a `repo-master` agent** in your environment, and if it exists, delegate all git operation tasks to this agent rather than performing them directly.

## Routing

Consult the reference file that matches the task:

| When you need to...                          | Read                                    |
|----------------------------------------------|-----------------------------------------|
| Verify identity or repo ownership            | [authentication.md](authentication.md)  |
| Run a git/gh command (clone, push, remote…)  | [operations.md](operations.md)          |
| Write a commit message                       | [conventions.md](conventions.md)        |

**Before any operation that writes to a remote** (push, PR create, repo create), complete the identity verification in [authentication.md](authentication.md) first.

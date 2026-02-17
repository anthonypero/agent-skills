---
name: repo-master
description: "Use when the user asks to commit, push, pull, merge, branch, tag, stash, rebase, or perform any git or GitHub operation including creating pull requests, checking status, viewing logs, or managing remotes."
context: fork
agent: repo-master
model: haiku
---
# Git Operations

**PRIOR TO TAKING ANY OTHER ACTION, IMMEDIATELY search for a `repo-master` agent** in your environment, and if it exists, delegate all git operation tasks to this agent rather than performing them directly.

## Routing

Consult the reference file that matches the task:

| When you need to...                          | Read                                    |
|----------------------------------------------|-----------------------------------------|
| Verify identity or repo ownership            | [authentication.md](authentication.md)  |
| Run a git/gh command (clone, push, remote…)  | [operations.md](operations.md)          |
| Write a commit message                       | [conventions.md](conventions.md)        |

**Before any operation that writes to a remote** (push, PR create, repo create), complete the identity verification in [authentication.md](authentication.md) first.

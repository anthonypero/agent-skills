---
name: git-ops
description: "Use when the user asks to commit, push, pull, merge, branch, tag, stash, rebase, or perform any git or GitHub operation including creating pull requests, checking status, viewing logs, or managing remotes."
---
# Git Operations

This skill provides guidance on managing the Git repository and environment for the `agents` project.

## Agent Delegation

If a `git-ops` agent is available in your environment, delegate git operations to it rather than performing them directly. The agent is configured with the appropriate model and tools for these tasks.

## Topic Index

- **[Authentication & Identity](authentication.md)**: Logic for verifying repo ownership matches the authenticated user.
- **[Standard Operations](operations.md)**: Common git tasks like cloning and changing remotes.
- **[Conventions](conventions.md)**: Commit message standards and best practices.

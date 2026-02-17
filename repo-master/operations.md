# Standard Operations

Prefer the `gh` CLI for all GitHub operations. Fall back to raw `git` only when `gh` does not support the operation.

## Creating a Repository

New repositories **must be private by default**. Only create a public repo if the user explicitly requests it.

```bash
gh repo create <name> --private
```

## Cloning a Repository

```bash
gh repo clone <owner>/<repo>
```

## Managing Remotes

Verify the current remote:

```bash
git remote get-url origin
```

Update if it points to the wrong fork or uses the wrong protocol:

```bash
git remote set-url origin git@github.com:<owner>/<repo>.git
```

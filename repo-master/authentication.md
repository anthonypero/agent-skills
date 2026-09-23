# Authentication & Identity

Resolve which account to act as **before any operation that reaches GitHub**: a push, a PR, a repo create, or a `gh` read of a private repo. A machine may have several accounts logged into `gh`, and the active one is whichever was last switched to. Never run `gh auth switch` to fix a mismatch. It changes global state for every other session on the machine.

## Verification Steps

1. **Get the remote URL**: `git remote get-url origin`
   - If this fails (no remote / not a git repo), skip verification.

2. **Parse the repo owner**:
   - SSH: `git@github.com:Owner/Repo.git` → `Owner`
   - HTTPS: `https://github.com/Owner/Repo.git` → `Owner`

3. **Get the active user**: `gh api user --jq .login`

4. **Compare**:
   - **Match**: Proceed as normal.
   - **Mismatch, owner's token available**: Run `gh auth token --user <Owner>`. If it prints a token, the owner is logged in on this machine but inactive. Prefix every network command in this task with `GH_TOKEN=$(gh auth token --user <Owner>)`. That covers `git push`, `git pull`, `git fetch`, `git clone` and every `gh` call. Confirm with `GH_TOKEN=$(gh auth token --user <Owner>) gh api user --jq .login`, which must print `<Owner>`.
   - **Mismatch, no token for the owner**: Stop. Tell the user they are authenticated as `AuthUser`, the repo belongs to `RepoOwner`, and that account is not logged in on this machine. Ask them to run `gh auth login` for it, or to switch accounts themselves. Do not proceed until resolved.

A repo owned by an organization has an org name as its owner, not a user, so `gh auth token --user <Org>` finds nothing. In that case, a mismatch only means the active user is not the owner. Proceed as the active user, and treat a `Repository not found` or permission error as the mismatch.

## Why `GH_TOKEN` works for git

The global git config routes `github.com` credentials through `gh auth git-credential`, and that helper honors `GH_TOKEN`. Check with `git config --get-all credential.https://github.com.helper`. If the helper is something else, such as `osxkeychain`, `GH_TOKEN` covers only `gh` calls. In that case, treat a git mismatch as the no-token case.

## Check auth status

```bash
gh auth status
```

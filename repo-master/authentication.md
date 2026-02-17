# Authentication & Identity

Verify the authenticated user matches the repository owner **before any operation that writes to a remote**.

## Verification Steps

1. **Get the remote URL**: `git remote get-url origin`
   - If this fails (no remote / not a git repo), skip verification.

2. **Parse the repo owner**:
   - SSH: `git@github.com:Owner/Repo.git` → `Owner`
   - HTTPS: `https://github.com/Owner/Repo.git` → `Owner`

3. **Get the authenticated user**: `gh api user --jq .login`

4. **Compare**:
   - **Match**: Proceed.
   - **Mismatch**: Stop. Inform the user they are authenticated as `AuthUser` but the repo belongs to `RepoOwner`. Ask them to run `gh auth login` with the correct account. Do not proceed until resolved.

## Check auth status

```bash
gh auth status
```

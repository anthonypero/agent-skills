# Authentication & Identity

Before performing any GitHub operations, you **must** verify that the authenticated user matches the repository owner.

## Verification Logic

1.  **Check for Remote**: Run `git remote get-url origin`.
    -   If command fails (no remote/not a git repo): **Skip verification**. Proceed with caution.
    -   If success: Proceed to step 2.

2.  **Determine Repo Owner**:
    -   Parse the output of `git remote get-url origin`.
    -   SSH format: `git@github.com:User/Repo.git` -> Owner is `User`.
    -   HTTPS format: `https://github.com/User/Repo.git` -> Owner is `User`.

3.  **Determine Authenticated User**:
    -   Run `gh api user --jq .login`.
    -   Store the output as `AuthUser`.

4.  **Compare**:
    -   **Match** (`RepoOwner == AuthUser`): Proceed with the operation.
    -   **Mismatch**: Stop immediately.
        -   **Action**: Inform the user they are authenticated as `${AuthUser}` but the repo belongs to `${RepoOwner}`.
        -   **Prompt**: Ask the user to authenticate with the correct account (e.g., `gh auth login`).
        -   **Wait**: Do not proceed until the user creates a new session or explicitly overrides the check.

> [!WARNING]
> This logic enforces strictly working on forks/repos owned by the authenticated user. It prevents accidental pushes to upstream repositories or organizations where the username does not match the namespace.

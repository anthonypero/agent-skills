# Standard Operations

## Cloning a Repository

Since `gh` authentication is now the source of truth, prefer using `gh repo clone` if authenticated.

```bash
gh repo clone owner/repo
```

If you must use `git clone` manually, ensure you are cloning the fork that matches your intended identity.

## Changing Remote URL

If you need to fix a remote to point to your fork:

```bash
git remote set-url origin git@github.com:YourUsername/repo.git
```

## Checking Connection

```bash
gh auth status
```

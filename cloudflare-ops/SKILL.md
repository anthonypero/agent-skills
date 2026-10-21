---
name: cloudflare-ops
description: "Anthony's Cloudflare accounts, credentials, deploys and hard-won gotchas. Use whenever a task deploys to or changes anything on Cloudflare — Workers, static assets, `_headers`, wrangler, custom domains, DNS on a Cloudflare zone (anthonypero.me), R2, Tunnel, Access — or whenever `wrangler`, `cf`, or Cloudflare is mentioned. AUGMENTATION: this skill EXTENDS the official Cloudflare plugin skills (`cloudflare:wrangler`, `cloudflare:cf-cli`, `cloudflare:cloudflare`, `cloudflare:workers-best-practices`) rather than replacing them. Whenever one of those would trigger, load it AND this skill together. This one supplies the account-specific facts the official skills can't know."
---

# Cloudflare: Anthony's setup

This skill rides on top of the official Cloudflare plugin. If you got here without the matching official skill (`cloudflare:wrangler` for deploys, `cloudflare:cloudflare` for choosing a product), load it now for general guidance, then apply what follows.

**Inventory is canonical in `/Users/studio/Projects/handyman/ops/CLOUDFLARE.md`.** It lists the accounts, LastPass item names, zones, Workers, tunnel and Access app. Read it before touching an existing resource. Update it, in the handyman repo, whenever you create, rename or delete a Cloudflare resource.

## Credentials

Never run `wrangler login`. Pull the token pair from LastPass (`lastpass` skill) into the environment, in the same shell command as wrangler:

```bash
eval "$(~/.claude/skills/lastpass/scripts/lp env global CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID)" && npx wrangler deploy
```

Never echo or write the token. song-library's R2 uses a **different account**: see the ops doc.

## Deploying

- Deploying to a live site is outward-facing. Do it when the project's owner asked for it.
- **Test first without touching production.** `npx wrangler versions upload` prints a preview URL (`https://<id>-<worker>.<subdomain>.workers.dev`) and leaves live traffic alone. Only `wrangler deploy` (or `versions deploy`) goes live.
- After a deploy, wait ~10 s and verify with `curl -sI "https://host/?cb=$RANDOM"`. For a header change, check every path class: the root, a subfolder, a file.

## Gotchas (each one cost a real incident)

- **`_headers` can't target the root.** A `/` rule, or a `! Header` detach on `/`, is never applied to the root response. So a blanket `/*` rule can't exempt the homepage. To noindex everything but `/`, write one rule per top-level folder, and add a rule whenever a new top-level folder appears. (anthonypero.me was noindex until 2026-10-21 because of this.)
- **One splat per `_headers` rule.** A rule like `/*/*` is silently ignored, and nothing reports an error.
- **Preview URLs force `X-Robots-Tag: noindex`** on every response. To test robots rules on a preview, use the same patterns with a dummy header such as `X-Test`.
- `_headers` doesn't apply to responses produced by Worker code (only to static assets served directly).

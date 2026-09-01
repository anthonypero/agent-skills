# Pager infrastructure

Shared plumbing facts for all pager channels, plus how to stand up a channel on a new machine. The active machine's channel settings live in `~/.config/pager/channel.md` — NOT here.

## Shared infrastructure facts

All channels ride the same plumbing: Zoho hosts `anthonypero.com` (catch-all delivers to `info@`, but each channel alias must be registered as a true **email alias** in the Zoho Admin Console — required or Zoho SMTP refuses to relay sends as that address with a 553); Zoho forwards to `tonygpero@gmail.com`; `gws` on each machine is authed to that Gmail account (OAuth project `844167811990`, Gmail API enabled).

- `gws gmail send` really sends (no drafts-only limitation) but always as the account's **default send-as** (`info@anthonypero.com`) — no `--from` flag. **Always send outbound pager mail with `scripts/send_as.py --from <channel alias>`** so threads carry the agent identity. It supports `--thread-id` for threading; `gws gmail read` does NOT expose the RFC822 `Message-ID`, so pass `--thread-id` alone and keep the same subject with a `Re:` prefix.
- The Zoho path must stay **forwarding**, not Gmail POP import (POP polls on Google's schedule, up to an hour). Verified delivery is fast (~2 min round trip including human typing).
- UNC Charlotte prepends `[EXTERNAL]` to inbound subjects and injects a caution banner into bodies — harmless, but don't match on exact subjects.
- `gws auth login` must exclude Keep: `--services gmail,calendar,drive,docs,sheets,slides,tasks,chat,forms,contacts` (Keep scopes are Workspace-only and 400 the whole flow).
- Mark-processed = remove UNREAD: `gws gmail label <message-id> --remove UNREAD`. The poller deliberately leaves messages unread; mark read only after acting.
- The channel label lives in the user's personal Gmail, which their phone also reads — the Gmail filter should include **Skip the Inbox** so a phone glance can't consume the unread marker the poller depends on.
- If a label name misbehaves in `label:` queries, resolve the label ID once (`gws gmail labels`) and query by ID instead.

## Known channels

| Channel | Machine | Alias | Status |
| --- | --- | --- | --- |
| `clt` | Work laptop (`~/Projects/clt`) | `claude-clt@anthonypero.com` | Working — acceptance-tested 2026-08-26 |
| `personal` | FUMC laptop (`~/Projects/fumc`) | `agent@anthonypero.com` | Working — acceptance-tested 2026-09-01. One channel for everything non-work (FUMC and personal are co-mingled by design); the CLT laptop is the only separate channel |

## Standing up a channel on a new machine

1. Zoho Admin Console → Users → the mailbox → Mailbox Settings → Email Alias → add the channel alias.
2. Gmail: filter `to:<alias>` → apply a label (convention: the alias itself) + **Skip the Inbox**.
3. Gmail: add the alias under Send mail as, relayed via Zoho SMTP (keeps SPF/DKIM aligned). Zoho rejects the relay until step 1 is done.
4. Machine: `gws auth login` with the Keep-excluding `--services` list above; verify with `gws auth status`.
5. Machine: copy the skill's `assets/channel-template.md` to `~/.config/pager/channel.md` and fill in every TODO (label ID comes from `gws gmail labels`).
6. Acceptance test: `send_as.py` to the user's address (check From survives), have the user reply, confirm the poller digest catches it on the label.

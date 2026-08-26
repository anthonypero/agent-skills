# Pager channel — this machine (<machine name / context>)

Machine-local config read by the `pager` skill. The skill itself (and shared infrastructure facts) live in the `agent-skills` repo; this file holds only what is specific to this machine's channel. Copy this template to `~/.config/pager/channel.md` and fill in every value — the skill refuses to enter away mode while any TODO remains.

| Setting | Value |
| --- | --- |
| Channel name | TODO (short context id, e.g. `clt`, `personal`) |
| Agent alias (mail TO the agent) | TODO (e.g. `agent@example.com` — must be a true Zoho Email Alias, see infrastructure.md) |
| Gmail label the poller watches | TODO (convention: the alias itself; include the ID from `gws gmail labels`) |
| Agent send-as identity | TODO (verified Gmail send-as; sends require the skill's `send_as.py` — gws cannot set From) |
| User's address (mail TO the user) | TODO (where pages are sent; `/pager on <address>` can override per session) |
| Authorized senders (from-guard) | TODO (comma-separated; include every From identity the user's mail systems actually stamp) |
| Subject tag | TODO (e.g. `[clt-agent]` — goes in every outbound subject for phone filtering) |

Acceptance-tested: TODO (date, once the full round trip has passed — see infrastructure.md step 6).

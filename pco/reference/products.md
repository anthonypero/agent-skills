# Planning Center products — API map

Every product lives behind one host (`https://api.planningcenteronline.com`) and one JSON:API
dialect; only the path prefix and schema change. Official docs: https://developer.planning.center/docs/

| Product | Path prefix | Typical use | Field notes |
| --- | --- | --- | --- |
| Services | `/services/v2` | songs, arrangements, keys, attachments, plans, teams | [services.md](services.md) |
| People | `/people/v2` | people, households, lists, workflows, field data | *(none yet)* |
| Check-Ins | `/check-ins/v2` | events, check-ins, locations | *(none yet)* |
| Giving | `/giving/v2` | donations, funds, batches, pledges | *(none yet)* |
| Groups | `/groups/v2` | groups, memberships, events, tags | *(none yet)* |
| Calendar | `/calendar/v2` | events, resources, reservations | *(none yet)* |
| Registrations | `/registrations/v2` | signups, attendees | *(none yet)* |
| Publishing | `/publishing/v2` | church center content | *(none yet)* |
| Webhooks | `/webhooks/v2` | subscriptions | *(none yet)* |

Confirm the current prefix and resource names in the official docs before writing — this table is
a map, not the schema.

## Universal conventions

- **Auth**: HTTP Basic with a Personal Access Token (`app_id:secret`) — the client handles it.
- **Discovery**: `pco get /<product>/v2` returns the product's top-level resource links; each
  resource is listed under `links`. Cheapest way to orient in an unfamiliar product.
- **Pagination**: `?per_page=100` (max), follow `links.next` — `pco get --all` / `client.get_all()`.
- **Sideloading**: `?include=<rel>`; filtering `?where[<attr>]=<value>`; sorting `?order=<attr>`.
- **Rate limit**: ~100 requests / 20 s per token; 429 is retried with `Retry-After`.
- **Writes**: JSON:API envelope `{"data": {"type": "<Type>", "attributes": {...}}}`.
- **Token scope**: a PAT sees exactly what its owner sees in that church's account — permission
  errors (403) are usually a role problem in PCO, not an API problem.

## Adding a product's field notes

When a real project accumulates lessons for a product, create `reference/<product>.md` (pattern:
`services.md`) and, if reusable helpers emerge, `lib/pco_<product>.py`. Link it from the table
above.

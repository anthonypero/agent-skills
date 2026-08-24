# Planning Center Services — plans, templates, times

Field notes from FUMC Gastonia (2026-08). Code: `lib/pco_plans.py`; CLI: `pco services ...`.

## Data model

```
ServiceType                 name, frequency ("Weekly", "Daily", None)
  ├─ PlanTemplate           name, item_count, team_count — a saved plan used as a starting point
  └─ Plan                   title, series_title, public, sort_date (derived), items_count
       ├─ PlanTime          starts_at/ends_at (UTC ISO), time_type: service|rehearsal|other, name
       ├─ Item              sequence, item_type: header|item|song, title
       └─ TeamMember        scheduled/needed positions
```

## Lessons

- **A plan has no date attribute.** `sort_date` is derived from its PlanTimes. To create a
  dated plan: `POST /service_types/<st>/plans` (attributes may be empty), then
  `POST .../plans/<id>/plan_times` for each service/rehearsal/other time.
- **Templates are applied to an existing plan**, not passed at creation:
  `POST /service_types/<st>/plans/<id>/import_template` with
  `{"data":{"type":"PlanImportTemplate","attributes":{"plan_id":"<template id>","copy_items":true,"copy_people":true,"copy_notes":true}}}`.
  `copy_people` copies the template's team positions (needed/scheduled).
- **Copy times from the previous plan, shifted in the org's local zone.** The API speaks UTC;
  shifting in UTC drifts an hour across a DST change. Org zone: `GET /services/v2` →
  `data.attributes.time_zone`. `shifted_times()` keeps weekday offsets (a Thursday rehearsal
  before a Sunday service stays Thursday) and local clock times.
- **Listing plans by date**: `?filter=after&after=<ISO>&before=<ISO>&order=sort_date` on
  `/service_types/<st>/plans`. The un-scoped `/services/v2/plans` rejects filters ("You must
  pass a plan id").
- **Template `item_count` undercounts** what an import produces (Communion template says 21,
  imports 27) — compare imported plans to a known-good sibling plan, not to the attribute.
- Identify which template a church really uses by diffing a recent plan's item titles against
  each template's `/plan_templates/<id>/items` — names like "NEW"/"Summer" are not reliable.

## FUMC specifics (profile `fumc`)

- Modern worship = service type `191364` "01. Modern". Regular template `Modern` (60925570),
  communion `Modern - Communion` (69455127). Communion is the first Sunday of the month unless
  the worship spreadsheet's "Response" column says otherwise.
- Other weekly types: `1279119` 09:00 Relaxed Traditional, `704802` 11:00 Traditional.

# Calendar events as an editorial layer

**Status:** Accepted, 2026-06-24.

## Context

For v1 we need to surface *"what's happening in Congress this week"* to
users. The Chilean Congress publishes its agenda across multiple surfaces
(camara.cl daily Tabla, senado.cl agenda, interpelación citaciones, urgency
deadlines), but none of these are wired into the platform yet, and ADR-0016
explicitly defers Tabla / Sesión-meeting ingestion to a future scraper.

The platform already has two date-bearing entities that *sound* like they
could carry this information:

- **`LegislativeSession`** (ADR-0016) — a single Sesión meeting in the Sala
  or a Comisión. Intended to be exhaustive: every meeting Congress holds.
  Currently not being written (the ingestor is the deferred work).
- **`BillEvent`** — the granular past-tense tramitación log per bill,
  derived from upstream `tramitaciones`.

Neither matches the v1 product need:

- `LegislativeSession` is the wrong scope (~30+ meetings/week once
  ingested), the wrong tense (any meeting, not just notable ones), and
  not editorially controllable.
- `BillEvent` is past-tense and per-bill; it can't represent forward-looking
  moments that span bills (a presidential mensaje, an interpelación) or
  that have no bill at all (a constitutional deadline, a public hearing).

Two paths were possible: stretch one of the existing models to cover the
"what to watch" use case, or introduce a new entity. The product framing —
*"a curator picks the ~5–20 noteworthy moments per week"* — does not match
either existing model's semantics.

## Decisions

### 1. A separate `CalendarEvent` entity for the editorial layer

`CalendarEvent` is a curated, forward-looking record of moments worth
highlighting. It lives alongside `LegislativeSession` and `BillEvent`, not
inside either:

| Entity | Tense | Scope | Source |
|---|---|---|---|
| `LegislativeSession` | present/future | exhaustive (every Sesión) | upstream scraper (deferred, ADR-0016) |
| `BillEvent` | past | granular, per-bill | derived from upstream `tramitaciones` |
| `CalendarEvent` | forward-looking | curated (5–20/week) | manual now, scraper-augmented later |

A future Tabla/Sesión scraper that ships per ADR-0016 will populate
`LegislativeSession` exhaustively. Future agenda scrapers (e.g. a camara.cl
daily-agenda parser) will populate `CalendarEvent` with **filtered**
highlights. The two pipelines are independent.

### 2. v1 is manual entry through the admin panel; scraper-ready by design

For v1 there is no calendar ingestor. Curators enter events through a
`sqladmin` view. The model carries the dedup primitives a future scraper
needs from day one, so the upgrade path is a new task file plus an
ingestor — not a schema change:

- `source: CalendarEventSource` enum (v1: `{manual}`). New values added per
  scraper (`camara_agenda`, `senado_agenda`, …) mirroring how
  `INGESTOR_BILLS_SOURCE` already grows.
- `external_ref: str | None`. Unique index on `(source, external_ref)` —
  Postgres allows multiple nulls, so manual rows are unaffected. The
  scraper writes a stable upstream key and re-runs idempotently through
  the same `upsert_calendar_event` write-service function the admin form
  uses (or doesn't — see §6 below).

### 3. `CalendarEventKind` enum, not free-text

Per `CONTEXT.md`'s enum discipline ("All categorical string fields use
formal Python enums"). The kinds are:

`sesion, comision, interpelacion, mensaje, plazo, otro`

`otro` is the escape valve for editorial moments that don't fit the named
buckets; if it dominates after a few weeks of curation, that's the signal
to add a new enum value rather than reach for free-text. Kind drives UI
rendering and gives a future scraper a structural slot to target.

### 4. Linkages: nullable single FKs to `Bill`, `Legislator`, `Committee`

A `sesion`-kind event with a marquee bill links one `Bill`; an
`interpelacion` links one `Legislator`; a `comision` hearing links one
`Committee`. All FKs are `nullable`, all use `ondelete=SET NULL`.
Per-kind required-FK rules are deliberately **not** enforced in the
schema — they live in the service / form layer where the workflow is
("an interpelacion *should* have a legislator, but a curator setting up
an unconfirmed slot may not know yet").

No `LegislativeSession` FK in v1: that table isn't being written. It will
be added the day the Sesión scraper lands (ADR-0016 followup).

A `CalendarEvent` covering several bills on a Sesión Tabla currently has
to be split into multiple rows or list the rest in `description`. This is
acceptable at v1 volumes (~5–20 events/week) and avoids a join table that
would sit empty for months. Upgrading `bill_id` to a many-to-many
`calendar_event_bills` join is a single migration when the Tabla scraper
arrives and starts producing multi-bill rows.

### 5. Persists forever; cancellation is curator messaging in the title

No `CalendarEventStatus` enum. The row persists past `starts_at` (mobile
clients delta-sync the calendar through `SyncableMixin`, so vanishing
rows would break the sync protocol). Cancellation is communicated by
the curator editing the title (`[CANCELADO] …`, `[POSTERGADO] …`), not
by a stored state flag. A status enum would need a producer (a scraper
emitting "postponed" signals); until then it would be a column with no
write path.

Soft-delete via `SyncableMixin.deleted_at` is reserved for rows that
should disappear entirely (e.g. an event created in error). Public reads
filter `deleted_at IS NULL`.

### 6. The admin form does **not** route through `upsert_calendar_event`

The original plan called for the sqladmin create/edit hooks to delegate
to `upsert_calendar_event`, on the principle that the write service is
the single mutation entrypoint. On review, the deviation was justified:

- For manual rows (`external_ref=None`), `upsert_calendar_event` performs
  no dedup — it falls through to a plain insert. Routing through the
  write service buys nothing functionally for the manual path.
- Overriding sqladmin's async `insert_model` / `update_model` to use a
  service-layer function would be the first such override in the
  codebase. Other admin views (`BillAdmin`, `LegislatorAdmin`, etc.)
  write directly via the sqladmin session.
- `CLAUDE.md`'s "all DB mutations through write service" rule is
  scoped to the ingest pipeline — admin-panel writes are the documented
  exception ("Used to update reference data or fix records when data
  collectors can't capture them").

`upsert_calendar_event` still exists for the scraper path: it owns the
`(source, external_ref)` dedup logic, datetime tz coercion, and per-row
sync stamping. Both surfaces end up at the same `calendar_events` rows
in the same table.

## Alternatives considered

**Reuse `LegislativeSession`.** Push manual entries into the same table
that an eventual Sesión scraper will populate. Rejected because the two
have opposite scopes — exhaustive vs. curated — and the manual entries
would conflict with the scraper output the moment ADR-0016's followup
lands. The curator would be entering rows the scraper doesn't know
about, and vice versa.

**Reuse `BillEvent`.** Rejected on tense alone: `BillEvent` is the
past-tense activity log derived from upstream `tramitaciones`. A
forward-looking entry would be a write that no upstream producer made,
which breaks the model's invariant.

**Tabla as a child of `LegislativeSession`.** Per ADR-0016, this is the
right home for the *exhaustive* agenda once the Sesión scraper exists.
But Tabla items are bound to a specific Sesión row, which doesn't exist
for v1. Modeling Tabla now would require shimming Sesión, which is
outside scope.

**A polymorphic `target_type` + `target_id` column.** Rejected because
the project doesn't use polymorphism anywhere else, and admin form
support for polymorphic FKs is awkward in sqladmin. Nullable per-type
FKs are honest about the cardinality.

**An `is_published` draft toggle.** Rejected for v1. Adds one column to
the schema, a filter to every public query, and a state to test, all to
solve "I want to stage events but not publish yet" — which a curator can
solve by writing in a notes doc and pasting in when ready. Reversible
later as a single column add if real staging needs emerge.

**A `CalendarEventStatus {scheduled, happened, cancelled, postponed}`
enum.** Rejected: `scheduled` vs. `happened` is purely a function of
`starts_at` vs. `now()` (storing it would require a cron to flip the
flag); `cancelled` is soft-delete; `postponed` is `UPDATE starts_at`. A
status enum becomes useful only when an upstream signal needs to be
preserved, which no current source emits.

## Out of scope

- **A calendar scraper.** This ADR establishes the table shape and the
  write entrypoint; the scraper is a separate piece of work.
- **`LegislativeSession` FK on `CalendarEvent`.** Add when Sesión
  ingestion lands (ADR-0016 followup).
- **Many-to-many `calendar_event_bills`.** Add when the Tabla scraper
  starts producing multi-bill rows.
- **Dashboard widget.** `/v1/dashboard` does not embed calendar events
  in v1; clients can call `/v1/calendar?hasta=…` directly. Revisit when
  the web/mobile dashboard wants a top-N preview.
- **Urgency-derived plazos.** `BillUrgency` lets us *compute* plazo
  deadlines, but auto-materializing them as `CalendarEvent` rows would
  create double-source risk against any plazo a curator also enters
  manually. Defer until there's a UX need.

## Consequences

- New table `calendar_events`; schema regenerated via `just recreate-db`
  (pre-release flow, no Alembic migration to author).
- New enums `CalendarEventKind` and `CalendarEventSource` join the
  `CONTEXT.md` "Enums" list.
- `CONTEXT.md` carries a new *Calendar event* glossary entry naming the
  three-way distinction against `LegislativeSession` and `BillEvent`.
- `upsert_calendar_event` ships unused by the admin form for now; the
  test that pins down its `(source, external_ref)` dedup behavior is the
  scraper-readiness gate without writing the scraper.
- The public endpoint `GET /v1/calendar` is live with a today→+14-day
  default window. Time-of-day precision is tz-aware UTC, displayed in
  `America/Santiago` at the client edge (no server-side tz conversion).

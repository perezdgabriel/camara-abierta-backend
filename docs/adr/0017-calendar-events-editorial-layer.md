# Calendar events as an editorial layer

**Status:** Accepted, 2026-06-24. Amended 2026-06-24 — added `votacion` kind (see §3 below). Amended 2026-06-24 — Tabla Semanal CLI ingestor + two new kinds (see §7 below). Amended 2026-07-04 — S3-triggered ingestion replaces the manual DB-tunnel step (see §8 below). Amended 2026-07-04 — orphan boletínes are proactively retrieved and self-heal (see §9 below). Amended 2026-07-05 — §9's self-heal was silently defeated in serverless mode; fixed by re-resolving `bill_id` at write time (see §10 below).

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

`sesion, votacion, comision, interpelacion, mensaje, plazo, otro`

`otro` is the escape valve for editorial moments that don't fit the named
buckets; if it dominates after a few weeks of curation, that's the signal
to add a new enum value rather than reach for free-text. Kind drives UI
rendering and gives a future scraper a structural slot to target.

**`votacion` (added 2026-06-24).** Originally lumped under `sesion`. The
frontend swap revealed the lump was load-bearing: a *Sesión* is the
meeting block ("Sala Diputados — 13:00"), a *Votación* is a discrete
announced vote ("Vota Boletín 100-06 — 14:30"). The dashboard widget is
literally named *Próximas votaciones* on this distinction, and clients
will agendize votaciones as their own row even when they sit inside a
Sesión. This is exactly the "add a new enum value rather than reach for
free-text" signal anticipated above, applied before `otro` proliferated.

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

### 7. Tabla Semanal CLI as the first non-manual `source`

`CalendarEventSource.TABLA_SEMANAL` and `app/ingestors/parsers/tabla_semanal.py`
ship as the first non-manual writer. The CLI subcommand
`python -m app.cli ingestors tabla-semanal --pdf <path> [--dry-run]` reads
the Cámara de Diputados weekly agenda PDF, classifies each row, and writes
through `upsert_calendar_event` — both the admin form *exception* (§6) and
this CLI converge on the same row shape.

**New kinds — `acusacion_constitucional` and `informe_cei`.** The Tabla
carries two recurring row types that did not fit the existing kinds:

- Constitutional accusations against current or former ministers (Art. 42
  LOC Congreso). Forced into `interpelacion` would be semantically wrong —
  *interpelación* is questioning a sitting minister, not impeachment of an
  ex-minister. Forced into `otro` would lose the dashboard-filter signal
  for what is one of the year's highest-salience events.
- CEI report readings in Sala (Comisión Especial Investigadora informes —
  "CEI 70", "CEI 69 y 71"). These are not committee *hearings*
  (`comision` already covers that) but *reports presented to the Sala* —
  a distinct beat in the legislative week.

Both follow the §3 discipline: "if `otro` dominates after a few weeks of
curation, that's the signal to add a new enum value." The Tabla Semanal
parse made the dominance visible before the table accumulated.

**Item-level rows are `kind=votacion`.** Per the agenda's intent: every
tabled bill is announced *to be voted* this week, even when discussion
slips. Curators / future scrapers can flip an event to `otro` post-hoc
when the chamber actually defers. Forward-looking by design (§3).

**`external_ref` shape.** Per-bill rows: `tabla-semanal:{boletin}:{YYYY-MM-DD}`.
SESION header rows: `tabla-semanal:sesion:{YYYY-MM-DD}`. Acusación:
`tabla-semanal:acusacion-{slug}:{YYYY-MM-DD}`. CEI: `tabla-semanal:cei-{N}:{YYYY-MM-DD}`
(or slug fallback). Re-publication of the same week's PDF upserts in
place. A bill that slips from this week's Tabla to next week's produces a
new row at the new date — history is preserved automatically; cancellation
of the slipped week is curator-driven per §5.

**Refundidos: first bolet̄ín wins.** A row citing "Boletines Nos X, Y y Z,
refundidos" uses X as the `bill_id` and the `external_ref` key. Y and Z
are mentioned as a sentence in `description`. Avoids three near-duplicate
rows in the calendar at the cost of the bill pages for Y and Z losing the
event link — accepted since the refundidos relationship already lives in
the bill ingestion path.

**Unknown bolet̄ínes are orphans, not skips.** When the Tabla cites a
bolet̄ín not yet ingested into `bills`, the calendar event is still
created with `bill_id=None` and a `WARNING` log (same pattern as
`_reconcile_authorships`). Re-running the CLI after the bill ingestor
catches up resolves the link in place via the same `external_ref`. Two
ingestion paths stay decoupled.

**Local file only for v1.** The CLI takes `--pdf <path>`, not `--url`.
URL-fetching is deferred until the upstream URL pattern at camara.cl is
known stable enough for a scheduled scrape. Manual download → CLI run is
the v1 workflow.

### 8. S3-triggered ingestion replaces the manual DB-tunnel step

Running the CLI against production required an SSM tunnel through the
NAT-bastion box (`just rds-tunnel`) plus manually pulling DB credentials
from Secrets Manager — fragile, unaudited, and outside the
`revalidate()`/CloudWatch coverage every other ingestor gets from the
`jobs` Lambda (ADR-0022).

**What changed, what didn't.** The human-downloads-a-PDF step from §7 is
unchanged — this does **not** add URL-fetching; that remains deferred.
Only the delivery mechanism changed: the PDF is now uploaded to an S3
bucket (`tabla-semanal/` prefix, `.pdf` suffix) instead of sitting on a
laptop for the CLI to read. An `OBJECT_CREATED` event notification
invokes the existing `job_fn` Lambda, which downloads the object and calls
`run_ingest_tabla_semanal` in-process — the same Lambda that already
resolves `DB_SECRET_ARN` and fires the post-ingest `revalidate()` ping.
The CLI `--pdf` path is untouched and remains the recommended way to
dry-run a PDF locally before uploading.

**Idempotency carries over unchanged.** The `external_ref` dedup from §7
(re-publishing the same week's PDF upserts in place) makes both Lambda
async-invoke retries and accidental re-uploads safe by construction — no
new safeguard was needed for the new trigger.

**No DLQ / on-failure destination.** At roughly one upload per week,
Lambda's default async-invoke retry (two attempts) is enough. A failed
run is visible in CloudWatch Logs; the fix is to re-upload the same PDF,
which is safe per the idempotency guarantee above. A dedicated DLQ +
alarm (mirroring `llm_dlq`) was judged disproportionate at this volume —
revisit if the upload cadence increases.

**Revalidate tag.** Originally reused only `"dashboard"` (already expired
by other ingestors), on the grounds that a `"calendar"` tag had no
established frontend contract yet. That contract now exists —
`listCalendarEvents()` (`camara-abierta-web/src/lib/api/calendar.ts`) tags
its fetch `["calendar"]`, and the frontend's revalidate webhook already
allowlists `"calendar"` (`KNOWN_TAGS` in
`src/app/api/revalidate/route.ts`) — so `_TABLA_SEMANAL_REVAL_TAGS` in
`app/lambdas/jobs.py` now sends both `["dashboard", "calendar"]` (2026-07-04
correction). Until this fix, calendar reads only refreshed via their 60s
ISR TTL, never on-demand after an ingest.

### 9. Orphan boletínes are proactively retrieved and self-heal

§7's "unknown boletínes are orphans, not skips" still holds — the calendar
event still gets created immediately with `bill_id=None` — but the
resolution path is no longer "wait for a human to notice the WARNING and
re-run the CLI." The chamber-votes pipeline (ADR-0013) already solved the
identical "vote references a bill we don't have yet" problem, so this ports
that pattern rather than inventing a new one:

- When `run_ingest_tabla_semanal` finds an orphan boletín, it calls the same
  `_trigger_targeted_bill_ingest` helper the chamber-votes orphan path uses,
  which dispatches a single-bulletin `ingest_bills` job. Boletínes cited more
  than once in the same PDF (e.g. a `sesion` header and its own `votacion`
  row) only trigger one dispatch per run.
- `CalendarEvent.bulletin_number` persists the boletín even when `bill_id`
  is null — mirroring `VotingSession.bill_bulletin_number` exactly, down to
  the partial index shape (`ix_calendar_events_pending_bulletin`, `WHERE
  bill_id IS NULL AND bulletin_number IS NOT NULL`). It's set unconditionally
  whenever a boletín is known, resolved or not, matching how
  `upsert_voting_session` always sets its counterpart column.
- `upsert_bill` gained `_reconcile_orphan_calendar_events`, run alongside
  the existing `_reconcile_orphan_voting_sessions` at both of its call
  sites: once the bill lands, any `CalendarEvent` row with a matching
  `bulletin_number` and `bill_id IS NULL` gets linked automatically. No
  re-run of the tabla-semanal CLI is required.
- `--dry-run` never dispatches — matches every other `if not dry_run:` guard
  in this ingestor.
- Not backfilled: `CalendarEvent` rows created as orphans before this column
  existed keep `bulletin_number = NULL` and will not self-heal; only newly
  parsed orphans benefit. A manual re-run of the CLI against the original
  PDF still resolves those, per §7.

### 10. §9's self-heal only worked in `celery` mode — fixed by write-time re-resolution

§9 shipped assuming `_trigger_targeted_bill_ingest` *enqueues* the bill ingest
to run later, so `upsert_bill`'s `_reconcile_orphan_calendar_events` would fire
after the orphan `CalendarEvent` row was already committed. That assumption holds
under `celery` dispatch but is **false in serverless (deployed) mode**, where
`dispatch` runs task bodies **inline** (`task.apply().get()`, ADR-0022). The
result was a silent bug in production: orphan bills got retrieved, but the
calendar events stayed `bill_id = NULL` forever.

The inline execution inverts the ordering the reconcile depended on. In
`run_ingest_tabla_semanal`'s orphan branch, `_trigger_targeted_bill_ingest`
runs the *entire* bill ingest synchronously — including
`_reconcile_orphan_calendar_events` — **before** `upsert_calendar_event` writes
the orphan row. Two independent failures compound:

1. **Ordering:** the reconcile `UPDATE ... WHERE bill_id IS NULL AND
   bulletin_number = …` runs against a row that does not exist yet, matching
   zero rows.
2. **Cross-session isolation:** even reordered, the inline `ingest_bills` runs
   in its own `task_session`; the outer tabla-semanal session is still open and
   uncommitted, so under READ COMMITTED the reconcile cannot see the pending
   calendar row anyway.

And unlike `upsert_voting_session` — which re-resolves `bill_id` from the
bulletin at write time (ADR-0013) and therefore self-healed all along — the
calendar path trusted the caller's `bill_ids` map, computed *once before the
loop* and never refreshed for the bill just ingested inline mid-loop. That
asymmetry is exactly why Senate/Chamber votes worked but calendar events didn't.

**Fix: `upsert_calendar_event` re-resolves `bill_id` from `bulletin_number` at
write time** when the caller didn't supply one, mirroring
`upsert_voting_session`. Because the inline ingest has already *committed* the
bill by the time the event is written, a fresh `SELECT Bill.id WHERE
bulletin_number = …` finds it — no dependence on task ordering or cross-session
visibility. `_reconcile_orphan_calendar_events` stays as the backstop for the
genuinely-async arrival (a bill that lands on a *later* run, or `celery` mode).

Already-orphaned production rows are backfilled by the idempotent data migration
`05d02404c207_backfill_orphan_calendar_event_bill_id` (`bill_id IS NULL AND
bulletin_number = bills.bulletin_number`).

**Durable lesson — resolve FKs at write time, never from a pre-loop map.**
Because `dispatch` is inline in serverless, any pipeline that (a) inline-triggers
a dependency ingest mid-loop *and* (b) writes the dependent row from a map built
before that loop will re-introduce this exact silent orphan. A write-time
re-resolve is the robust pattern; the pre-loop map was the fragile one. As of
this amendment, the three relink flows — voting sessions → bill, calendar events
→ bill, and votes → legislator term — all resolve their FK at write time. (Votes
were never exposed: their term dependency arrives via a *separately scheduled*
`ingest_legislators`, never inline-triggered, so their reconcile always fires
against committed rows.)

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
- (§9) `calendar_events.bulletin_number` + `ix_calendar_events_pending_bulletin`
  ship via a real additive Alembic migration
  (`a03b709c50e7_add_calendar_event_bulletin_number`, `down_revision =
  e03d4c13e902`) — the project is past pre-release, so this is the first
  schema change shipped as an incremental migration rather than a
  `recreate_db`-regenerated single initial-schema file.

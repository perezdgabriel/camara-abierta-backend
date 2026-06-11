# Chamber-vote bulk ingest via OpenData `retornarVotacionesXAnno`

Chamber votes were captured only as a side-effect of bill ingest: the
embedded `<VotacionProyectoLey>` block inside `retornarProyectoLey` was
re-dispatched per bulletin by `sync_bill` (`app/tasks/bills.py:24-44`).
This couples vote coverage to bill discovery — **any vote on a bulletin
not yet ingested is invisible**, regardless of cadence. Observed in the
wild: the latest vote on `retornarVotacionesXAnno?prmAnno=2026` (Id 89113,
2026-06-10) is for boletín 15936-18, which was not in the DB at the time
restsil had not yet surfaced it.

Structurally identical to the Senate problem [ADR-0009](0009-restsil-as-bills-and-senate-vote-source.md)
solved by introducing `run_ingest_senate_votes`. We do the same for the
Chamber. This ADR partially supersedes ADR-0009's Chamber paragraph
("Chamber votes still flow via OpenData enrichment on the bills ingest
path").

## Chamber-vote discovery

A new `run_ingest_chamber_votes` task walks
`WSLegislativo.asmx/retornarVotacionesXAnno?prmAnno=YYYY` (root
`<VotacionesColeccion>`, desc by `<Id>`/`<Fecha>`) and dispatches
`sync_voting_session` per row.

- **Watermark:** highest `<Id>` seen, stored in
  `IngestorState(entity_type="chamber_votes").last_cursor`. IDs are
  globally monotonic across years in the sample we inspected (~89k in
  mid-2026), so a single integer suffices — no per-year cursor.
- **Steady state:** `prmAnno=current_year` every tick, stop at watermark.
- **Cold start (no cursor):** iterate years
  `[settings.ingestor_bills_start_year .. current_year]` newest-first,
  no watermark cutoff, then set the watermark to the global max `<Id>`
  observed. After cold start, past years are not re-swept by default —
  vote `Fecha` is what `prmAnno` keys on, so new activity on a 2018 bill
  hits the current-year feed.
- **Targeted recovery:** `--year YYYY` re-walks a single year without
  touching the watermark; `--bulletin X-Y` runs the enrichment+detail
  path for one bulletin without touching the watermark.
- **Safety cap:** `--max-pages N` (and a settings default) bounds a
  single tick.
- **Schedule:** `crontab(hour="5,9,13,17,21", minute=30)` — bills at
  `:00`, senate-votes at `:15`, chamber-votes at `:30`.

## Three-endpoint enrichment

`retornarVotacionesXAnno` carries only a light summary
(`Id`, `Descripcion`, `Fecha`, totals, `Quorum`, `Resultado`, `Tipo`).
It does not include `TipoVotacionProyectoLey`, `Articulo`,
`TramiteConstitucional`, or `TramiteReglamentario`, and it has no
per-deputy detail. Rich data is assembled from three calls:

| Purpose | Endpoint | Returns |
|---|---|---|
| Discovery | `retornarVotacionesXAnno?prmAnno=YYYY` | Light summary, desc by `<Id>` |
| Per-bulletin enrichment | `retornarVotacionesXProyectoLey?prmNumeroBoletin=X` | Rich `<VotacionProyectoLey>` summaries |
| Per-vote detail | `retornarVotacionDetalle?prmVotacionId=Z` | Per-deputy `Voto/Diputado` |

Per tick, new votes are grouped by bulletin; the enrichment call is made
once per distinct bulletin (typically 1–5 in steady state). Per-deputy
detail fan-out reuses `opendata_camara_async.fetch_voting_details_parallel`.
A new `fetch_chamber_vote_summaries_parallel(bulletins)` helper applies
the same first-page-then-fan-out pattern to per-bulletin enrichment for
cold-start backfill, bounded by
`settings.ingestor_opendata_async_concurrency` (new, default 10).

The dedup key stays `camara:vot:{Id}` from `<Id>`; the existing parser
shape (`VoteParser.parse_chamber_vote`) is preserved.

## Orphan-bulletin handling

When a vote arrives for a bulletin not yet in our `bills` table, two
things happen at once:

1. `sync_bill.delay(bulletin)` is enqueued so bill ingest catches up
   out-of-band.
2. The vote is saved immediately with `bill_id = NULL`.

To make the eventual relink **deterministic** rather than dependent on a
later embedded re-dispatch, `VotingSession` gains a
`bill_bulletin_number: str | None` column (indexed; partial WHERE
`bill_id IS NULL`). `upsert_voting_session` always writes the upstream
bulletin string into this column. `upsert_bill` ends with:

```python
db.execute(
    update(VotingSession)
    .where(
        VotingSession.bill_id.is_(None),
        VotingSession.bill_bulletin_number == bill.bulletin_number,
    )
    .values(bill_id=bill.id)
)
```

Senate votes get this fix for free — they already pass `bill_bulletin`
through `upsert_voting_session` and are subject to the same dark-hole.
Pre-release means `recreate_db.py` handles the schema change; no
Alembic migration.

## Source-flag failover

`INGESTOR_CHAMBER_VOTES_SOURCE: Literal["bulk", "bill_detail"] = "bulk"`,
mirroring `INGESTOR_SENATE_VOTES_SOURCE` (ADR-0009).

- **`bulk`** (default): `run_ingest_chamber_votes` drives; the embedded
  loop at `app/tasks/bills.py:24-44` is gated off.
- **`bill_detail`**: the dedicated task no-ops; the embedded loop is
  active. Restores pre-change behaviour exactly. Used if OpenData's bulk
  endpoint regresses.

A single canonical path means lineage is unambiguous: if `bulk`, a
chamber `VotingSession` came from `retornarVotacionesXAnno` and its
two enrichments. The duplicate-dispatch cost of running both is small
(idempotent upsert), but explicit-source semantics match the operator
playbook the Senate side already follows.

## Considered Options

- **Run both paths in parallel (no gate).** Cheap redundancy, but
  duplicate `sync_voting_session.delay()` calls per overlapping vote and
  ambiguous data lineage. Rejected for consistency with ADR-0009.
- **Save orphan votes light, let the bill-ingest embedded re-dispatch
  fill rich fields later.** Looks tempting but `upsert_voting_session`
  unconditionally clobbers all fields on `on_conflict_do_update`,
  creating a tick-boundary race where a light bulk save can downgrade a
  previously-rich row. Rejected; fetching `retornarVotacionesXProyectoLey`
  per bulletin is cheap (~1–5 calls per tick in steady state).
- **Store the bulletin only in transit (not on `VotingSession`); rely on
  the embedded re-dispatch to do the relink.** Rejected: the embedded
  `<VotacionProyectoLey>` block is sometimes stale, the same lesson
  ADR-0008's Senate correction surfaced. A persisted bulletin column is
  the only deterministic way to recover.
- **Pause watermark while orphans exist (JSON cursor with `pending_below`
  set).** Rejected: more complex state for the same outcome, and a single
  perma-orphan (an upstream bulletin that never lands in restsil) would
  stall the cursor.
- **Always sweep all past years on every tick.** Rejected: past-year
  drift is rare and the cold-start backfill plus per-CLI `--year`
  override is enough.

## Consequences

- **Chamber-vote freshness improves and discovery is decoupled from bill
  ingest.** Votes on brand-new bulletins land in our DB on the next
  chamber-votes tick instead of waiting for restsil to surface the bill.
- **One additional HTTP call per distinct bulletin per tick** for the
  per-bulletin enrichment fetch. Bounded; ~1–5 in steady state. Cold-start
  backfill is bounded by `ingestor_bills_start_year`.
- **Schema change**: `VotingSession.bill_bulletin_number` added. Handled
  by `recreate_db.py` in pre-release; one indexed UPDATE appended to
  `upsert_bill`. Senate-side orphan votes get the same backfill behaviour
  as a side-benefit.
- **Joint-bulletin votes still link to the first parsed bulletin only.**
  Existing limitation, preserved. Proper many-to-many vote↔bill modeling
  is out of scope.
- **Non-bill chamber votes (Descripcion without `Boletín N° X-Y`) are
  skipped.** They never appear in the DB. Matches the current bill-driven
  posture; Proyectos de Acuerdo and internal procedural votes are not
  part of the transparency surface.
- **New env vars:** `INGESTOR_CHAMBER_VOTES_SOURCE` and
  `INGESTOR_OPENDATA_ASYNC_CONCURRENCY` (the latter shared with any
  future OpenData parallel path).
- **The bill-detail embedded loop stays compilable and tested but does
  not receive fresh work in the default configuration.** If a full
  release passes without invoking the failover, consider thinning it
  out in a follow-up ADR.

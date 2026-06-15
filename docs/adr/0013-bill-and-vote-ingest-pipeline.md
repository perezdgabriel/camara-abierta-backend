# Bill and vote ingest pipeline

Consolidates three predecessor ADRs into the current bills + votes design.
Supersedes [ADR-0008](0008-bills-discovery-via-opendata-year-scan.md),
[ADR-0009](0009-restsil-as-bills-and-senate-vote-source.md), and
[ADR-0010](0010-chamber-vote-bulk-via-opendata.md). Their content is
preserved in-place for history; this ADR is the single source of truth for
how the pipeline operates today.

## Context

Bill discovery and vote capture were originally entangled: the `<votacion>`
nodes embedded in `tramitacion.php?boletin=X` were the only Senate-vote
source, and chamber votes were captured only as a side-effect of bill
ingest (`sync_bill` re-dispatching `<VotacionProyectoLey>`). Two failures
forced the redesign:

1. The embedded `<votacion>` block in `tramitacion.php` was inconsistently
   populated, so the frontend was missing complete Senate-vote sessions.
2. Any chamber vote on a bulletin not yet in the bills table was
   invisible until restsil surfaced the bill — a bulletin-discovery race.

The fix came in stages (ADR-0009 for bills + Senate votes, ADR-0010 for
chamber votes), each partially superseding the previous. This ADR
collapses them.

## Decisions

### Bill discovery — restsil paged feed

`run_ingest_bills` walks
`restsil.senado.cl/v3/buscarProyectosDeLey?order=desc` (the
apikey-authenticated SPA backend of `portallegislativo.senado.cl`).
Server-side filters: `fecha_desde` / `fecha_hasta` (entry-year window),
`estado`, `camara`, `iniciativa`, `boletin`.

- **Every tick (5×/day):** scan `fecha_desde=fecha_hasta=current_year`,
  all statuses — picks up newly filed bills and anything the summary
  surfaces about already-ingested ones.
- **Daily (gated):** scan past years
  (`fecha_desde=start_year`, `fecha_hasta=current_year-1`), **`estado=T`
  only** — ~7,000 rows globally vs ~18,000 unfiltered. Terminal-status
  bills don't get new activity that matters to the existing upsert
  logic. The gate cursor is `IngestorState(entity_type="bills").last_cursor`
  (ISO date of last sweep).
- **Cold start (no cursor):** past-years scan keeps all statuses for a
  one-time full backfill, then the cursor flips the steady-state policy
  on.

`PROYFECHAINGRESO` is entry-date, not modified-since. Coverage of late
activity on already-introduced bills is preserved by re-fetching every
in-window bulletin's full detail every tick — the "no modified-since
upstream" policy from ADR-0008 carries forward unchanged.

### Bill detail — wspublico `tramitacion.php`

Restsil only exposes bill *summaries*. Full detail (tramitaciones, etapas,
informes, oficios, comparados, materias) comes from
`tramitacion.php?boletin=X` via `fetch_bills_parallel`. OpenData
enrichment for sponsoring ministries and chamber-vote IDs is unchanged.

### Senate-vote ingest — dedicated task

`run_ingest_senate_votes` walks
`restsil.senado.cl/v3/buscarVotaciones?order=desc&sort=HORA&limit=100`.
Each row is a **complete** vote (counts + per-legislator
`SI`/`NO`/`ABSTENCION`/`PAREO` lists carrying `PARLID`, names, slugs); no
per-bulletin fan-out needed.

- **Watermark:** highest `ID_VOTACION` seen, stored in
  `IngestorState(entity_type="senate_votes").last_cursor`. Walks
  stop at the first row at or below the watermark. IDs are
  upstream-issued and monotonic — immune to HORA/FECHA drift and
  multiple-vote-per-minute ties.
- **Cold start:** walk until exhausted (~87 pages at limit=100).
- **Schedule:** same 5×/day cadence as bills, offset by 15 minutes.
- **Safety cap:** `ingestor_restsil_max_pages_per_tick` bounds a single
  tick — a corrupt/wiped watermark cannot drain the upstream in one go.
- **Targeted recovery:** `--bulletin X-Y` uses the server-side filter and
  does *not* advance the watermark.

The vote `bcn_id` shape is **`senado:vot:{ID_VOTACION}`** — the upstream
ID is unique and stable, replacing the earlier
`senado:vot:{bulletin}:{session_ref}` shape that had latent collisions
when a single session held multiple votes on the same bulletin.

`voting_type` is heuristically inferred from `TEMA` substrings ("discusión
en general" / "particular" / "única") because `buscarVotaciones` omits
`TIPOVOTACION`. `stage_label` and `bill_stage_id` are left `None` (both
nullable, FE handles absence). Slight quality drop versus the labeled
wspublico fields; acceptable.

Senate-vote legislator matching is bridge-id-only via
`senado:{PARLID}` (per the legislator pipeline — see
[ADR-0012](0012-legislator-ingest-pipeline.md)). Name matching is dropped.

### Chamber-vote ingest — dedicated task with three-endpoint enrichment

`run_ingest_chamber_votes` walks
`WSLegislativo.asmx/retornarVotacionesXAnno?prmAnno=YYYY` (root
`<VotacionesColeccion>`, desc by `<Id>`/`<Fecha>`).

- **Watermark:** highest `<Id>` seen, stored in
  `IngestorState(entity_type="chamber_votes").last_cursor`. IDs are
  globally monotonic across years — single integer suffices.
- **Steady state:** `prmAnno=current_year` every tick, stop at watermark.
- **Cold start:** iterate
  `[settings.ingestor_bills_start_year .. current_year]` newest-first,
  no watermark cutoff, then set watermark to global max `<Id>` observed.
  Past years are not re-swept by default — vote `Fecha` is what
  `prmAnno` keys on, so new activity on an old bill hits the
  current-year feed.
- **Targeted recovery:** `--year YYYY` re-walks a year without touching
  the watermark; `--bulletin X-Y` runs enrichment+detail for one
  bulletin.
- **Schedule:** `crontab(hour="5,9,13,17,21", minute=30)` — bills at
  `:00`, senate-votes at `:15`, chamber-votes at `:30`.

The discovery summary is light (`Id`, `Descripcion`, `Fecha`, totals,
`Quorum`, `Resultado`, `Tipo`); rich fields are assembled from three
calls per tick:

| Purpose | Endpoint | Returns |
|---|---|---|
| Discovery | `retornarVotacionesXAnno?prmAnno=YYYY` | Light summary, desc by `<Id>` |
| Per-bulletin enrichment | `retornarVotacionesXProyectoLey?prmNumeroBoletin=X` | Rich `<VotacionProyectoLey>` (`TipoVotacionProyectoLey`, `Articulo`, trámites) |
| Per-vote detail | `retornarVotacionDetalle?prmVotacionId=Z` | Per-deputy `Voto/Diputado` |

Per tick, new votes are grouped by bulletin; the enrichment call is made
once per distinct bulletin (typically 1–5 in steady state). Per-deputy
detail fan-out via `opendata_camara_async.fetch_voting_details_parallel`.
A `fetch_chamber_vote_summaries_parallel(bulletins)` helper applies the
same first-page-then-fan-out pattern to the per-bulletin enrichment for
cold-start backfill, bounded by `settings.ingestor_opendata_async_concurrency`.

Chamber-vote `bcn_id` is **`camara:vot:{Id}`**. Per-legislator matching is
bridge-id-only via `camara:{deputy_id}`.

### Orphan-bulletin handling

A vote can land for a bulletin not yet in our `bills` table. In that case
the writer:

1. Enqueues `sync_bill.delay(bulletin)` so bill ingest catches up
   out-of-band.
2. Saves the vote immediately with `bill_id=NULL` and the upstream
   bulletin string in `VotingSession.bill_bulletin_number` (indexed,
   partial WHERE `bill_id IS NULL`).

`upsert_bill` ends with a deterministic relink:

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

The relink is independent of any embedded `<VotacionProyectoLey>` block —
not coincidence, choice: the embedded list is inconsistently populated
and cannot be the linker of last resort. Senate-side orphan votes inherit
the same behaviour.

Non-bill chamber votes (Descripcion without a parseable `Boletín N° X-Y`)
are skipped entirely — Proyectos de Acuerdo and internal procedural votes
are not part of the transparency surface.

### Source-flag failover

Restsil is the SPA backend, not a documented public API, and could be
locked down without notice. Legacy paths stay in tree:

- `INGESTOR_BILLS_SOURCE`: `"restsil"` (default) | `"opendata"` (year-scan
  failover via `get_mensajes_x_anno` / `get_mociones_x_anno`).
- `INGESTOR_SENATE_VOTES_SOURCE`: `"restsil"` (default) | `"wspublico"`
  (no-op in the dedicated task — legacy votes ride on bill ingest's
  embedded `<votacion>` parse + `votaciones.php` fan-out).
- `INGESTOR_CHAMBER_VOTES_SOURCE`: `"bulk"` (default) | `"bill_detail"`
  (no-op in the dedicated task — legacy votes ride on bill ingest's
  embedded `<VotacionProyectoLey>` loop).

Failover is operator action only (env var or `--source` CLI flag). No
automatic source switching — a flip silently changes dedup-key shape
mid-stream, which is a poor failure mode for transient outages already
absorbed by watermarks + tenacity retries.

A failover Senate-vote run on `wspublico` produces
`senado:vot:{bulletin}:{session_ref}` keys that coexist with the new
`senado:vot:{ID_VOTACION}` rows. The duplication is accepted for the
recovery window; restsil resumes from its watermark cleanly when it
returns.

## Considered options

- **Trust entry-date desc-sort as modified-since signal.** Rejected
  (ADR-0008 + ADR-0009): a bill from 2020 with new activity yesterday
  would never appear near the top, regressing coverage of late activity
  on already-introduced bills.
- **Rely on the embedded `<votacion>` in `tramitacion.php` for Senate
  votes.** Initially accepted in ADR-0008, then rejected: the block is
  inconsistently populated and the frontend showed too few sessions.
  `votaciones.php` was the source for Senate votes until restsil's
  `buscarVotaciones` replaced it.
- **Keep the voting-ingestion task generic, just fix its discovery.**
  Rejected (ADR-0008): would re-discover and re-fetch an entire year of
  bulletins daily while bill ingest already visits the same set 5×/day.
- **Auto-failover on a single upstream failure.** Rejected (ADR-0009 +
  ADR-0010): silent dedup-key shape change mid-stream is worse than
  letting watermarks + retries absorb transients. Operator action is
  clearer.
- **Delete the legacy code paths.** Rejected (ADR-0009): restsil is
  unofficial. Cheap insurance to keep failover compilable and tested.
- **Run both bulk and embedded chamber-vote paths in parallel
  (no gate).** Rejected (ADR-0010): duplicate `sync_voting_session`
  calls + ambiguous lineage.
- **Save orphan votes light and rely on embedded re-dispatch to fill
  rich fields later.** Rejected (ADR-0010): `upsert_voting_session`
  unconditionally clobbers all fields on conflict, creating a
  tick-boundary race where a light bulk save downgrades a previously-rich
  row.
- **Store the bulletin only in transit (not on `VotingSession`).**
  Rejected (ADR-0010): the embedded block is sometimes stale (same
  lesson as the Senate-side `tramitacion.php` correction); a persisted
  bulletin column is the only deterministic way to relink.

## Consequences

- **Vote freshness improves on both sides.** Senate + chamber votes flow
  on their own cadence, decoupled from bill discovery. Votes on
  brand-new bulletins land on the next vote tick instead of waiting for
  the bill to be discovered.
- **One additional HTTP call per distinct chamber-vote bulletin per
  tick** (the per-bulletin enrichment fetch). Bounded; ~1–5 in steady
  state.
- **`voting_type` quality drops slightly** for restsil-sourced Senate
  votes (heuristic from TEMA vs labeled TIPOVOTACION). `stage_label` and
  `bill_stage_id` null on restsil-sourced votes.
- **Vote dedup-key shapes:** `senado:vot:{ID_VOTACION}` (restsil),
  `camara:vot:{Id}` (OpenData bulk). Failover modes can produce coexisting
  legacy keys; accepted for the recovery window.
- **Joint-bulletin votes still link to the first parsed bulletin only.**
  Existing limitation, preserved. Proper many-to-many vote↔bill modeling
  is out of scope.
- **Legacy code paths stay compilable and tested but do not receive fresh
  work in the default configuration.** If a full release passes without
  invoking any failover, consider thinning them in a follow-up ADR.
- **Required env vars:** `INGESTOR_RESTSIL_API_KEY` (production —
  raises `ConfigurationError` if missing while restsil is selected),
  `INGESTOR_OPENDATA_ASYNC_CONCURRENCY` (default 10).

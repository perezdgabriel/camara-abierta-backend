# Restsil portallegislativo backend is the bills + Senate-vote source

Two endpoints on `restsil.senado.cl/v3/` — the backend of
`portallegislativo.senado.cl`, reached with `Authorization: Apikey {...}` —
supersede the bill-discovery and Senate-vote-capture paths from
[ADR-0008](0008-bills-discovery-via-opendata-year-scan.md):

- `proyectos/buscarProyectosDeLey?order=desc` — paginated bill summary list
  (~18,085 rows today), offset/limit pagination, desc-by-`PROYFECHAINGRESO`.
  Server-side filters: `fecha_desde` / `fecha_hasta` (entry-year window),
  `estado` (T En tramitación, V Archivado, I Inadmisible, N Inconstitucional,
  L Publicado, R/E Rechazado), `camara` (S/D), `iniciativa` (30 Mensaje, 31
  Moción), `boletin`.
- `votaciones/buscarVotaciones?order=desc&sort=HORA` — paginated Senate-vote
  list (~8,640 rows today). Each row is a **complete** vote: counts +
  per-legislator `SI` / `NO` / `ABSTENCION` / `PAREO` lists carrying `PARLID`,
  `UUID`, `SLUG`, and names. No per-bulletin fan-out needed. Filter by
  `boletin`.

These endpoints are *unofficial* — they are the SPA backend, not a documented
public API, and could be secured or rate-limited at any time. The legacy
collectors stay in tree as a failover path; source selection is a settings
flag, see "Failover" below.

## Bills discovery

`run_ingest_bills` branches on `settings.ingestor_bills_source` (`"restsil"`
by default, `"opendata"` for failover). In restsil mode:

- **Every tick (5×/day):** desc-paged scan of `fecha_desde=fecha_hasta=Y`
  for the current year, all statuses — picks up newly filed bills and
  anything the upstream summary surfaces about already-ingested ones.
- **Daily (gated):** desc-paged scan of past years
  `fecha_desde=start_year, fecha_hasta=current_year-1`, **`estado=T`** only
  — ~7,000 rows globally vs ~18,000 unfiltered. Terminal-status bills are
  not refreshed (they don't get new activity that matters to the existing
  upsert logic). The gate is `IngestorState(entity_type="bills").last_cursor`,
  storing the ISO date of the last past-years sweep.
- **Cold start (no cursor):** past-years scan keeps **all statuses** for a
  one-time full backfill, then the cursor flips the steady-state policy
  on for subsequent runs.

For every discovered bulletin the bill detail still comes from
`tramitacion.php?boletin=X` on wspublico via `fetch_bills_parallel` —
restsil only exposes a summary. OpenData enrichment for sponsoring ministries
and chamber-vote IDs is unchanged.

`PROYFECHAINGRESO` is *entry date*, not a modified-since signal. The
"re-fetch full detail for every bulletin in the scan window" semantics from
ADR-0008 are preserved; we have not solved the upstream "modified since"
problem, only made the discovery feed cheaper and more uniform (both origin
chambers in one paged call).

## Senate votes

A dedicated `run_ingest_senate_votes` task replaces the per-bulletin Senate
vote capture from ADR-0008. It walks
`buscarVotaciones?order=desc&sort=HORA&limit=100` and dispatches
`sync_voting_session` per row, stopping at the first row whose `ID_VOTACION`
is at or below the stored watermark.

- Watermark: highest `ID_VOTACION` seen, stored in
  `IngestorState(entity_type="senate_votes").last_cursor`. IDs are
  upstream-issued and monotonic — immune to HORA / FECHA_VOTACION drift
  and to multiple-votes-per-minute ties.
- Cold start (no cursor): walk until exhausted (~87 pages at limit=100).
- Safety cap: `ingestor_restsil_max_pages_per_tick` (default 50) bounds a
  single tick — a corrupt or wiped watermark cannot drain the upstream in
  one go.
- Targeted recovery (`--bulletin`) uses the endpoint's server-side
  `boletin=` filter and does **not** advance the global watermark.
- Schedule: same 5×/day cadence as bills, offset by 15 minutes.

### Paged fetches run in parallel

Each restsil paged call takes ~4s P50; the cold-start vote backfill (~87
pages) and the daily past-years bill sweep (~73 pages of `estado=T`) are
HTTP-idle-bound. `RestsilSenadoClient.iter_votes_desc` /
`iter_bills_desc` use a first-page-sequential-then-fan-out shape:

1. Fetch page 1 sequentially. This gives `total` (so we can plan the
   remaining offsets) and triggers the watermark cutoff cheaply in the
   common case (≤ 1 new page per tick).
2. If page 1 was the last page, the watermark fired, or `total <=
   page_size`: return without any async hop.
3. Otherwise compute the remaining offsets
   `[page_size, 2*page_size, ...]` (bounded by `max_pages - 1`) and call
   `restsil_senado_async.afetch_votes_pages` / `afetch_bills_pages` —
   parallel `httpx.AsyncClient` requests behind an `asyncio.Semaphore`
   sized by `settings.ingestor_restsil_async_concurrency` (default 10).
4. Iterate the returned envelopes in offset order, applying the watermark
   cutoff to the votes path. Cold-start backfill drops from ~350s to ~40s.

The over-fetch bound in the parallel branch is `concurrency * page_size`
rows past the watermark (worst case: a cutoff that fires on row 1 of the
first parallel page). For 5×/day cadence with low upstream write rate this
is negligible.

The vote `bcn_id` shape switches from
`senado:vot:{bulletin}:{session_ref}` (latent collisions when a session has
multiple votes on the same bulletin) to **`senado:vot:{ID_VOTACION}`**. The
upstream id is unique and stable. Pre-release means cutover is a
`recreate_db.py` + reseed step; no online migration script.

### Legislator matching upgrade

Senators are already stored with `Legislator.bcn_id = f"senado:{PARLID}"`
(per ADR-0005 BCN cross-references senado with `idSenado == PARLID`). The
restsil per-legislator detail carries `PARLID` directly, so each individual
vote sets `legislator_external_id = f"senado:{PARLID}"` and resolves through
the existing `_resolve_vote_legislator` bcn_id branch — the brittle
name-match path used by the legacy parser is bypassed entirely. Name fields
are still set on the payload as a fallback for the (extremely rare) case
where a senator is not yet upserted by the time their vote arrives.

### Vote fields the new endpoint *does not* carry

`TIPOVOTACION` and `ETAPA` are absent from `buscarVotaciones`. Consequences:

- `voting_type` is **inferred** from `TEMA` substrings ("discusión en
  general" / "particular" / "única") via the same heuristic
  `VoteParser._parse_chamber_voting_type` already applies to chamber votes.
  Defaults to `VotingType.OTHER` when no hint is present. This is a slight
  quality drop versus the labeled wspublico field; acceptable.
- `stage_label` and `bill_stage_id` are left `None`. Both fields are already
  nullable in `VotingSession`; FE handles absence.

## Failover

Because restsil is unofficial:

- **Settings flags** (`ingestor_bills_source`, `ingestor_senate_votes_source`)
  default to `"restsil"`. Operators can flip either to `"opendata"` /
  `"wspublico"` without restart (env var) to activate the legacy collectors.
- **CLI overrides** (`--source` on `ingestors bills` and
  `ingestors senate-votes`) pin the choice for a single run.
- **No automatic source switching.** Transient restsil failures are absorbed
  by the watermark + tenacity retries; flipping sources is an explicit
  operator action.
- The legacy code paths remain in tree:
  `OpenDataCamaraClient.get_mensajes_x_anno` / `get_mociones_x_anno`,
  `senado_async.fetch_votes_parallel`, the embedded `<votacion>` parse in
  `SenadoClient._parse_bill_xml`, and `VoteParser.parse_senate_vote` (the
  name-match `bcn_id` resolver).
- A failover run on the wspublico Senate-vote path will produce
  `senado:vot:{bulletin}:{session_ref}` keys that **coexist** with new
  `senado:vot:{ID_VOTACION}` rows. We accept the duplication for the
  recovery window; restsil resumes from its watermark cleanly when it
  returns.

## Considered Options

- **Trust `PROYFECHAINGRESO` desc-by-date as a modified-since signal and
  only fetch detail for new bulletins.** Rejected for the same reason
  ADR-0008 rejected an entry-date filter on OpenData: a bill from 2020 with
  new activity yesterday would never appear near the top, regressing
  coverage of late activity on already-introduced bills.
- **Delete the legacy code paths.** Rejected — restsil is the SPA backend,
  not a public API, and could be locked down without warning. Keeping
  failover in tree is cheap insurance.
- **Auto-failover on a single restsil failure.** Rejected as premature: the
  watermark + tenacity retries already absorb transient failures, and an
  auto-flip would silently swap dedup-key shapes mid-stream. Operator action
  is clearer.

## Consequences

- **Senate-vote freshness improves.** Vote capture decoupled from bill
  discovery — votes flow whenever the dedicated task runs (5×/day), not
  only when their bill is re-fetched. The upstream cost per tick is ~1
  paged call when nothing is new.
- **No per-bulletin `votaciones.php` fan-out** in steady state. The bills
  ingest only re-fetches `tramitacion.php` per discovered bulletin —
  Senate-vote work moves entirely to the dedicated task.
- **Vote `bcn_id` shape change** is a destructive rename. The pre-release
  posture (`recreate_db.py`) makes this a non-event; existing rows are
  re-created with the new key on first ingest.
- **`voting_type` quality drops slightly** for restsil-sourced votes
  (heuristic from TEMA vs labeled TIPOVOTACION). `stage_label` and
  `bill_stage_id` are null on restsil-sourced votes.
- **The legacy code paths grow stale.** They stay compilable and tested but
  do not receive parser improvements unless restsil is replaced. If we go a
  full release without invoking the failover, consider thinning them out
  in a follow-up ADR.
- **New env var** `INGESTOR_RESTSIL_API_KEY` is required for production. The
  client raises `ConfigurationError` at construction if the key is missing
  and either source flag points at restsil.

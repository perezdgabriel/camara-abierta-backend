# Bills discovery is a bounded OpenData year-scan; vote capture rides on bill ingest

**Status:** Superseded by [ADR-0013](0013-bill-and-vote-ingest-pipeline.md) on 2026-06-15 (consolidated bills + votes ADR). Previously superseded by ADR-0009 — bill discovery moved to the `restsil.senado.cl/v3/buscarProyectosDeLey` paged feed and Senate-vote capture moved to a dedicated `run_ingest_senate_votes` task driven by `buscarVotaciones`. The OpenData year-scan and per-bulletin `votaciones.php` paths remain in tree as a failover; the policy below (re-fetch full detail for every in-window bulletin; no modified-since signal upstream) is preserved in the restsil discovery branch. Content preserved below for history.

`SenadoClient.get_bills_by_date(since)` called the Senado wspublico
`tramitacion.php?fecha=DD/MM/YYYY` endpoint to discover which bulletins changed
recently. The `fecha` parameter is not a supported query and the call always
errored; it had never been exercised against the live API (every test mocked it).
Because `run_ingest_bills` stamps `last_sync_date = today` at the end of each run,
after the first run it always entered the incremental branch, always hit the broken
call, and the broad `except` swallowed the error — so **bills silently stopped
updating after run #1**, and the Senate-vote path of the voting task was dead for
the same reason.

We **remove `get_bills_by_date`** and make bulletin discovery a **bounded re-scan of
OpenData's per-year bill lists** (`get_mensajes_x_anno` + `get_mociones_x_anno`).
`run_ingest_bills` computes a `start_year` — the year of `--since` or the last sync
date, falling back to `settings.ingestor_bills_start_year` on a cold start — and
re-fetches every bill's full Senado detail for `range(start_year, current_year + 1)`.
In steady state this is just the current year; after downtime it widens to catch up;
a first run backfills the full history. The `--since` flag is kept but **coarsened to
year granularity**.

We also **retire the dedicated voting-ingestion task**
(`run_ingest_voting_sessions` / `ingest_voting_sessions`). There is no longer a
separate scheduled collector for votes: `run_ingest_bills` captures **both** vote
sources for every bill it re-fetches. Senate votes come from the dedicated
`votaciones.php` endpoint — fetched in parallel via `fetch_votes_parallel` alongside
the bill-detail fetch and assigned to the bill's `_votaciones` (→ `parse_senate_vote`).
Chamber votes come from OpenData enrichment (`_camara_votaciones` →
`parse_chamber_vote`). `sync_bill` fans both out to `sync_voting_session` with the same
`senado:vot:{bulletin}:{session}` / `camara:vot:{id}` dedup keys, so a separate task
would only duplicate work.

> **Correction (follow-up).** This ADR originally claimed the `<votacion>` nodes
> embedded in `tramitacion.php` (the bill detail) were a complete Senate-vote source
> and deleted `get_votes_by_bulletin` accordingly — based on a single sampled bill
> (15776). That was wrong: `tramitacion.php` embeds votes **inconsistently** (e.g.
> boletín 15767 has 0 embedded votaciones but 1 complete vote with 39 per-legislator
> entries on `votaciones.php`). The frontend showed too few Senate sessions as a
> result. `votaciones.php` is a strict **superset** and is now the source of Senate
> votes, fetched within bill ingest as described above. The embedded `<votacion>` is
> kept only as a fallback when the `votaciones.php` fetch fails.

## Considered Options

- **Fix the date query / find a real "modified since" feed.** Rejected: no reliable
  wspublico date endpoint exists, and OpenData exposes only `entry_date`
  (`FechaIngreso`), no last-modified field. There is nothing dependable to query for
  "bills that changed since X".
- **Filter the scanned year by `entry_date >= since` (new bills only).** Rejected:
  it would miss new activity (stages, votes, urgency, status) on already-introduced
  bills — a real regression for a transparency platform. We re-fetch *every* bill in
  the window instead.
- **Keep the voting-ingestion task, just fix its discovery.** Rejected: it would
  re-discover and re-fetch an entire year of bulletins daily while bills ingest
  already visits the same set 5×/day. Folding the `votaciones.php` fetch into bill
  ingest captures the same votes without a second scheduled collector.
- **Rely on the embedded `<votacion>` in `tramitacion.php` for Senate votes.**
  Initially accepted, then **rejected** — see the correction above. The embedded list
  is frequently empty, so Senate votes are sourced from `votaciones.php` instead.

## Consequences

- **Late activity on bills older than the scan window is not picked up.** A bill
  introduced years ago that gets new activity now is only refreshed if the window
  reaches its entry year (e.g. after extended downtime, or a manual
  `--since <old-year>`). Acceptable; rare. A true as-of-date "modified" feed would be
  needed to close this, and none exists upstream.
- **Vote freshness improves.** Senate + Chamber votes now refresh whenever bills
  ingest runs (5×/day for in-window bills) instead of the single daily 03:15 voting
  run, which is removed from the beat schedule.
- **`voting-sessions` ingest is gone** — the CLI subcommand, the Celery task, the
  beat entry, `_resolve_since_date`, and the `"voting"` `IngestorState` entity. The
  public `/voting-sessions` API and all signal computation
  (`voting_signals`, `legislator_voting_stats`, `compute_voting_session_signals`)
  are untouched — they consume already-persisted `VotingSession` rows, which still
  arrive via `sync_bill → sync_voting_session`.
- **`--since` is year-granular now**, not a precise date filter; the CLI help
  reflects this. The reported `since` in the dispatch result still echoes the ISO
  date that seeded the year.

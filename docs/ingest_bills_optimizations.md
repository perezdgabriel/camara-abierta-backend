# Bill Ingestion Optimizations

Future performance improvements for the `ingest_bills` pipeline.

---

## 1. Parallel Senado fetches with asyncio

**Status:** ✅ Implemented

**Problem:** `get_bill_by_bulletin` calls are sequential with a 1-second delay between each.
With ~200 bulletins, this takes ~200 seconds minimum — most of it idle waiting on HTTP.

**Solution:** Use `httpx.AsyncClient` + `asyncio.Semaphore` (concurrency=10) to fetch multiple
bulletins in parallel. The Semido API can handle moderate parallelism.

**Implementation:**
- `app/ingestors/clients/senado_async.py`: `fetch_bills_parallel()` function
- `SenadoClient._parse_bill_xml()`: extracted parsing shared between sync/async paths
- `run_ingest_bills`: replaced sequential loop with `asyncio.run(fetch_bills_parallel(...))`

**Expected speedup:** ~10x (200s → ~20s for 200 bulletins)

---

## 2. Cache Camara year lists

**Status:** Not implemented

**Problem:** `get_mensajes_x_anno(year)` and `get_mociones_x_anno(year)` return static lists
for a given year. The task runs every 5 hours, so the same data is fetched 5x/day from the
Camara API — unnecessary external calls.

**Solution:** Cache results with a Redis key like `camara:mensajes:{year}` and
`camara:mociones:{year}` with a 1-hour TTL. On cache hit, skip the HTTP call.

**Implementation sketch:**
```python
# In run_ingest_bills, before calling Camara endpoints:
cache_key = f"camara:mensajes:{year}"
cached = redis_client.get(cache_key)
if cached:
    proyectos = json.loads(cached)
else:
    proyectos = opendata.get_mensajes_x_anno(year)
    redis_client.setex(cache_key, 3600, json.dumps(proyectos))
```

**Expected impact:** Reduces Camara API calls by ~80% (5x/day → 1x/day effective).
Small but meaningful for API courtesy.

---

## 3. Skip dispatching sync_bill for terminal bills

**Status:** Not implemented

**Problem:** Even though `upsert_bill` now skips reconciliation for terminal bills
(published/enacted/rejected/archived/withdrawn), we still dispatch a full `sync_bill`
Celery task. That task opens a DB session, does the main-row upsert, enqueues ES indexing,
and enqueues vote processing — all wasted work for a bill that won't change.

**Solution:** In `run_ingest_bills`, before dispatching, query the DB for bulletins
already in terminal status. Skip dispatching for those. Since we open a `task_session()`
for `_mark_synced` anyway, we can batch-query all existing terminal bulletins upfront:

```python
with task_session() as db:
    terminal_boletins = set(
        db.execute(
            select(Bill.bulletin_number).where(Bill.status.in_(TERMINAL_STATUSES))
        ).scalars().all()
    )

# In the dispatch loop:
if bulletin_number in terminal_boletins:
    skipped += 1
    continue
```

**Expected impact:** Eliminates ~80% of Celery task dispatches once most bills reach
terminal state. Saves DB sessions, ES re-indexing, and vote re-processing overhead.

---

## 4. Stagger past-year re-scans

**Status:** Not implemented

**Problem:** The task iterates `start_year..current_year` on every 5-hour tick.
Bills from past years don't get new bulletins added — their lists are static.
Re-fetching them every 5 hours is wasted work.

**Solution:** Track `last_full_year_scan` in `IngestorState`. On each run:
- Always fetch the **current year** (new bills can appear any day)
- Only do a full re-scan of past years once per day (or once per week)

**Implementation sketch:**
```python
if bulletin:
    bulletins = [bulletin]
else:
    current_year = date.today().year
    start_year = settings.ingestor_bills_start_year

    # Always scan current year
    years_to_scan = [current_year]

    # Scan past years only if >1 day since last full scan
    with task_session() as db:
        state = _get_state(db, "bills")
        last_full = state.last_full_year_sync_date if state else None
    if last_full is None or (date.today() - last_full).days >= 1:
        years_to_scan = list(range(start_year, current_year))
```

**Expected impact:** For a task that runs 5x/day across 4 years of history, this
reduces Camara + Senado API calls by ~75% on most runs.

---

## Summary

| # | Optimization | Speedup | Complexity | Implemented |
|---|---|---|---|---|
| 1 | Parallel Senado fetches | ~10x | Low | ✅ |
| 2 | Cache Camara year lists | ~5x fewer external calls | Low | ❌ |
| 3 | Skip dispatching terminal bills | Eliminates ~80% tasks | Low | ❌ |
| 4 | Stagger past-year re-scans | ~75% fewer API calls | Low | ❌ |

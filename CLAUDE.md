# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run API server
just dev              # or: uv run uvicorn app.main:app --reload

# Quality checks (run all after every change)
just format           # uv run ruff format
just check            # uv run ruff check
just typecheck        # uv run ty check
just test             # uv run pytest

# Run a single test file
uv run pytest tests/test_parsers.py

# Integration tests (require PostgreSQL)
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/camara_abierta_test \
  uv run pytest -m integration --integration
# or: just test-integration

# Celery worker / beat
just worker
celery -A app.core.celery_beat beat --loglevel=info

# CLI data collectors
python -m app.cli list
python -m app.cli ingestors bills --since 2026-05-01 --dry-run
python -m app.cli ingestors legislators
python -m app.cli scrapers diario-oficial --target-date 2026-05-04 --dry-run

# Database reset (pre-release only — drops all, regenerates schema)
just recreate-db      # or: python scripts/recreate_db.py --yes

# Seed reference data
just seed             # runs: reference, legislature, legislators
```

## Architecture

### Data pipeline

All external data follows a strict pipeline:

1. **Client** (`ingestors/clients/`) — fetches raw data from Congress APIs via httpx
2. **Parser** (`ingestors/parsers/`) — normalizes raw dicts into structured dicts
3. **Task** (`tasks/`) — Celery task that dispatches parsed data downstream
4. **Write service** (`services/write.py`) — the only place DB mutations happen

**Never write raw INSERT/UPDATE in tasks or API handlers.** All DB mutations go through the `upsert_*` functions in `services/write.py`.

### Celery tasks

All tasks must extend `DatabaseTask` from `app/tasks/base.py`. It provides auto-retry with exponential backoff (`retry_backoff=True`, max 3 retries). Use `task_session()` from `app.core.session` for DB access within tasks.

### Model mixins

`SyncableMixin` (from `app/models/base.py`) adds `id` (BigInteger PK), `created_at`, `updated_at`, `deleted_at`, and `sync_version` to main entities. `sync_version` is a global PostgreSQL sequence used for client-side delta sync.

### Three independent subdomains

No cross-domain FK references exist between them:
1. **Legislative** — `models/proyecto.py`, `models/legislature.py`, `models/votacion.py`
2. **Diario Oficial** — `models/diario_oficial.py` (`OfficialGazetteNorm`)
3. **CGR Reglamentos** — `models/diario_oficial.py` (`Regulation`, `RegulationStage`)

### Unit vs integration tests

The default test suite (`uv run pytest`) uses SQLite in-memory (`sqlite+pysqlite:///:memory:`) — no PostgreSQL needed. Integration tests (`-m integration --integration`) require `TEST_DATABASE_URL` pointing to a real PostgreSQL database whose name ends with `_test`. Integration fixtures roll back each test via savepoints.

## Key conventions

**No Alembic migrations in pre-release.** Modify SQLAlchemy models and run `recreate_db.py` to regenerate the schema. The single `*_initial_schema.py` migration is regenerated each time.

**Enums for all categorical fields.** See `app/models/enums.py`. Never use raw strings for `status`, `origin`, vote type, chamber type, etc.

**Language split:** Model/table names and field names use English. User-facing API response content uses Spanish. Legacy Spanish model names (`NormaGeneral`, `Reglamento`) are candidates for renaming.

**Imports:** Always absolute from `app.` root.

**Data sources per entity:**

| Entity | Source |
|--------|--------|
| Bills (list / discovery) | restsil (`buscarProyectosDeLey`) — apikey-authed paged feed (ADR-0013) |
| Bills (detail) | wspublico `tramitacion.php?boletin=X` |
| Legislators (active roster) | BCN REST `ObtenerParlamentariosActivos` — both chambers in one call (ADR-0012) |
| Deputies (gender + party history) | OpenData Camara (`get_diputados_periodo_actual`) overlay (ADR-0012) |
| Deputies (photo + profile URL) | camara.cl scraper (`scrapers/camara_diputados.py`) — enrichment-only (ADR-0012) |
| Senators (gender, phone, photo) | senado.cl web JSON catalog (`SenadoWebClient.get_full_catalog`) overlay (ADR-0012) |
| BCN biographic enrichment | Out-of-band: `python -m app.cli ingestors bcn-sparql-enrichment` — profession, twitter, ParliamentaryAppointment history (ADR-0012) |
| Senate votes | Dedicated `run_ingest_senate_votes` task via restsil `buscarVotaciones` (ADR-0013) |
| Chamber votes | Dedicated `run_ingest_chamber_votes` task via OpenData `retornarVotacionesXAnno` + per-bulletin + per-deputy enrichment (ADR-0013) |

## Domain language

- **Bill lifecycle:** `Bill.status` is the upstream source of truth. `BillStage` is the detailed legislative history; `is_current` marks the active stage. `BillEvent` is the granular activity log (from `tramitaciones`). `last_activity_date` = latest `BillEvent.event_date`, falls back to `Bill.entry_date`.
- **Dispensed vote:** An excused vote recorded explicitly — distinct from an absence.
- **Sponsoring ministries:** Bill-scoped upstream labels, not a shared catalog.
- **Data collector:** The concept covering both `scrapers/` (Playwright browser-driven) and `ingestors/` (httpx API clients). The split is an implementation detail.
- **Sync:** Client-side delta sync protocol using `sync_version`. `ClientSyncState` tracks per-device progress; `ChangeLog` records mutations.
- **Topics:** Hierarchical bill tags. Pre-defined reference data, but new ones can appear from upstream APIs.

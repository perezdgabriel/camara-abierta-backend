# Agents Guide — Camara Abierta Backend

## Project Overview

Legislative transparency platform for Chile. Tracks bills, legislators, voting sessions, and committees from the Chilean Congress. Built with FastAPI, SQLAlchemy 2, Celery, PostgreSQL, and Elasticsearch.

**Current version:** v0.1 (pre-release). See `docs/v0.1-plan.md` for the active development plan.

## Quick Reference

### Running locally

```bash
# Install dependencies
uv sync

# API server
uvicorn app.main:app --reload

# Celery worker
celery -A app.core.celery_app worker -Q default,llm -c 4 --loglevel=info

# Celery beat scheduler
celery -A app.core.celery_beat beat --loglevel=info
```

### CLI commands

```bash
# List available data collector jobs
python -m app.cli list

# Run data collectors
python -m app.cli ingestors bills --since 2026-05-01 --dry-run
python -m app.cli ingestors legislators
python -m app.cli ingestors voting-sessions --since 2026-05-03
python -m app.cli scrapers diario-oficial --target-date 2026-05-04 --dry-run
```

### Quality checks (always run after changes)

```bash
uv run ruff format
uv run ruff check
uv run mypy app/
uv run pytest
```

### Database reset (pre-release only)

```bash
python scripts/recreate_db.py
```

**Do not create Alembic migrations.** The project is pre-release. Use `recreate_db.py` to regenerate the schema after model changes.

## Architecture

### Directory structure

```
app/
├── api/v1/           # FastAPI route handlers
├── core/             # Config, database, celery app, session helpers
├── ingestors/        # API clients and data parsers
│   ├── clients/      # HTTP clients for Congress APIs
│   └── parsers/      # Normalize raw API data into dicts
├── models/           # SQLAlchemy ORM models
├── schemas/          # Pydantic request/response schemas
├── scrapers/         # Browser-driven data collectors (Playwright)
├── search/           # Elasticsearch indexing and queries
├── services/         # Business logic and DB mutations
└── tasks/            # Celery tasks
```

### Key patterns

**All DB mutations go through `app/services/write.py`.** Never write raw INSERT/UPDATE in tasks or API handlers. Use the `upsert_*` functions.

**Celery tasks use `DatabaseTask` base class.** It provides auto-retry with exponential backoff. Use `task_session()` for DB access within tasks.

**Data collectors follow a pipeline:**
1. Client (`ingestors/clients/`) fetches raw data from external API
2. Parser (`ingestors/parsers/`) normalizes into a dict
3. Task (`tasks/`) dispatches the parsed dict
4. Write service (`services/write.py`) upserts into DB

**Enum fields use Python enums.** See `app/models/enums.py` for the canonical vocabulary. All categorical string fields (status, origin, vote, etc.) must use these enums.

**SyncableMixin** adds `id`, `created_at`, `updated_at`, `deleted_at`, `sync_version` to main entities. The `sync_version` column is used for client-side delta sync.

### Data sources for v0.1

| Entity | Source API | Client method |
|--------|-----------|---------------|
| Bills (list) | OpenData Camara | `get_mensajes_x_anno()`, `get_mociones_x_anno()` |
| Bills (detail) | Senado | `get_bill_by_bulletin()` |
| Legislators | Senado + OpenData Camara | `get_senadores_vigentes()`, `get_diputados_periodo_actual()` |
| Voting sessions | Senado | `get_votes_by_bulletin()` |

### Three subdomains (independent)

1. **Legislative** — Bills, Legislators, Voting, Committees
2. **Diario Oficial** — Official gazette norms
3. **CGR Reglamentos** — Regulatory decrees

No cross-domain references between them.

## Code Conventions

- **Language:** Python 3.14
- **Models:** English names, SQLAlchemy 2 mapped_column style
- **API responses:** Spanish user-facing content, English field names
- **Formatting:** `ruff check` (run after every change)
- **Type checking:** `mypy app/` (run after every change)
- **Testing:** `pytest` (run after every change)
- **Line length:** Default ruff (88 chars)
- **Imports:** Absolute from `app.` root

## Agent Rules

1. **Always run `ruff check` and `mypy` after making changes.** Fix any errors before considering the task done.
2. **Do not create Alembic migrations.** Use `python scripts/recreate_db.py` to regenerate the schema.
3. **Follow the v0.1 plan** in `docs/v0.1-plan.md` for prioritization.
4. **All DB mutations go through `app/services/write.py`.** Do not write raw SQL or direct ORM inserts in tasks/handlers.
5. **Use enums for categorical fields.** Do not use raw strings for status, origin, vote type, etc.
6. **New Celery tasks** must extend `DatabaseTask` and use `task_session()`.
7. **Do not commit `.env` files.** Use `.env.local` for local overrides.

dev:
    uv run uvicorn app.main:app --reload

check: 
    uv run ruff check

format:
    uv run ruff format

typecheck:
    uv run mypy app/

test:
    uv run pytest

test-integration:
    uv run pytest -m integration --integration

recreate-db:
    uv run python scripts/recreate_db.py -y

worker:
    uv run celery -A app.core.celery_app worker -Q default -c 4 --loglevel=info

reference: 
    uv run python -m app.cli ingestors reference-data

geography:
    uv run python -m app.cli geography

legislature: 
    uv run python -m app.cli ingestors legislature

legislators:
    uv run python -m app.cli ingestors legislators

bills:
    uv run python -m app.cli ingestors bills

seed: geography reference legislature legislators
    echo "Database has been seeded with initial data"
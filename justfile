dev:
    uv run uvicorn app.main:app --reload

check: 
    uv run ruff check

format:
    uv run ruff format

typecheck:
    uv run ty check

test:
    uv run pytest

test-integration:
    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/camara_abierta_test uv run pytest -m integration --integration

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

seed-blocs:
    uv run python scripts/seed_blocs.py

seed: geography legislature legislators seed-blocs
    echo "Database has been seeded with initial data"
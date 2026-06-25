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
    uv run celery -A app.core.celery_app worker -Q default --loglevel=info

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

senate-votes:
    uv run python -m app.cli ingestors senate-votes

chamber-votes:
    uv run python -m app.cli ingestors chamber-votes

stats:
    uv run python -m app.cli legislator-stats refresh

seed-blocs:
    uv run python scripts/seed_blocs.py

seed: geography legislature legislators seed-blocs
    echo "Database has been seeded with initial data"

# Full bootstrap from an empty DB: drops + recreates the schema, seeds all
# reference data, then runs cold-start backfills for bills and both chambers'
# votes. Destructive — wipes the DB pointed to by DATABASE_URL.
coldstart: recreate-db seed reference bills senate-votes chamber-votes
    echo "Cold start complete: schema regenerated and all data backfilled"
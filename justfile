dev:
    uv run uvicorn app.main:app --reload

test:
    uv run pytest

flush:
    uv run python scripts/recreate_db.py -y

worker:
    uv run celery -A app.core.celery_app worker -Q default -c 4 --loglevel=info

reference: 
    uv run python -m app.cli ingestors reference-data

legislature: 
    uv run python -m app.cli ingestors legislature

legislators:
    uv run python -m app.cli ingestors legislators

bills:
    uv run python -m app.cli ingestors bills

seed: reference legislature legislators bills
    echo "Database has been seeded with initial data"
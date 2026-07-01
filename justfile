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

seed-topics:
    uv run python scripts/seed_topics.py

seed: geography legislature legislators seed-blocs seed-topics
    echo "Database has been seeded with initial data"

# Full bootstrap from an empty DB: drops + recreates the schema, seeds all
# reference data, then runs cold-start backfills for bills and both chambers'
# votes. Destructive — wipes the DB pointed to by DATABASE_URL.
coldstart: recreate-db seed bills senate-votes chamber-votes
    echo "Cold start complete: schema regenerated and all data backfilled"

# ── AWS RDS bootstrap (one-time coldstart load; see ADR-0022) ─────────────
# Run `coldstart` locally, then move that data into the private RDS via an SSM
# tunnel. Schema on RDS is owned by Alembic (migrate Lambda), so we load DATA
# ONLY — never `recreate_db` against a deployed DB.

# 1. Dump local data only (schema comes from Alembic on RDS, not this dump).
coldstart-dump db_url=env_var_or_default("DATABASE_URL", "postgresql://postgres@localhost:5432/camara_abierta"):
    pg_dump "{{db_url}}" --data-only --disable-triggers --no-owner --no-privileges -Fc -f coldstart.dump

# 2. Open an SSM port-forward to the private RDS through the fck-nat bastion.
#    fck-nat enables SSM automatically and runs as an ASG (min=max=1), so we
#    resolve the live instance by its `role=nat-bastion` tag (set in the CDK
#    NetworkStack) rather than hardcoding an ID. Leave this running in its own
#    terminal; RDS is then reachable at localhost:55432.
rds-tunnel rds_endpoint:
    #!/usr/bin/env bash
    set -euo pipefail
    NAT_ID=$(aws ec2 describe-instances \
      --filters "Name=tag:role,Values=nat-bastion" "Name=instance-state-name,Values=running" \
      --query 'Reservations[].Instances[].InstanceId' --output text)
    if [ -z "$NAT_ID" ]; then echo "No running nat-bastion instance found" >&2; exit 1; fi
    echo "NAT bastion $NAT_ID -> forwarding {{rds_endpoint}}:5432 to localhost:55432"
    aws ssm start-session --target "$NAT_ID" \
      --document-name AWS-StartPortForwardingSessionToRemoteHost \
      --parameters "{\"host\":[\"{{rds_endpoint}}\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"55432\"]}"

# 3. Restore into the Alembic-created RDS schema through the step-2 tunnel, then
#    verify the shared sync sequence carried over (must exceed max(sync_version)).
#    rds_url e.g. postgresql://<master>:<pw>@localhost:55432/camara_abierta
restore-rds rds_url:
    pg_restore --data-only --disable-triggers --no-owner -d "{{rds_url}}" coldstart.dump
    psql "{{rds_url}}" -c "SELECT last_value FROM global_sync_version_seq;"
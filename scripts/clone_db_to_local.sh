#!/usr/bin/env bash
# Clone the remote Railway PostgreSQL database to a local database.
# Usage: ./scripts/clone_db_to_local.sh [local_db_name]
#
# Requirements: pg_dump and psql must be in PATH (Postgres.app is fine).

set -euo pipefail

# ── Remote (Railway) ────────────────────────────────────────────────────────
REMOTE_HOST="caboose.proxy.rlwy.net"
REMOTE_PORT="32865"
REMOTE_USER="postgres"
REMOTE_DB="railway"
REMOTE_PASSWORD="vWnPytFSAyoSEfXfzOBTPpUfkloxwwvl"

# ── Local ───────────────────────────────────────────────────────────────────
LOCAL_DB="${1:-diariooficial_dev}"
LOCAL_USER="${LOCAL_USER:-$(whoami)}"
LOCAL_HOST="${LOCAL_HOST:-localhost}"
LOCAL_PORT="${LOCAL_PORT:-5432}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Remote : postgres://$REMOTE_USER@$REMOTE_HOST:$REMOTE_PORT/$REMOTE_DB"
echo "  Local  : postgres://$LOCAL_USER@$LOCAL_HOST:$LOCAL_PORT/$LOCAL_DB"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 1. Drop & recreate local database ───────────────────────────────────────
echo ""
echo "▶ Dropping (if exists) and recreating local database '$LOCAL_DB'…"
psql -h "$LOCAL_HOST" -p "$LOCAL_PORT" -U "$LOCAL_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS $LOCAL_DB;" \
    -c "CREATE DATABASE $LOCAL_DB;"

# ── 2. Dump remote and restore locally in one pipe ──────────────────────────
echo ""
echo "▶ Dumping remote DB via Docker (postgres:18-alpine) and restoring locally…"
echo "  (Docker will pull postgres:18-alpine on first run — may take a moment)"
docker run --rm \
    -e PGPASSWORD="$REMOTE_PASSWORD" \
    postgres:18-alpine \
    pg_dump \
        --host="$REMOTE_HOST" \
        --port="$REMOTE_PORT" \
        --username="$REMOTE_USER" \
        --dbname="$REMOTE_DB" \
        --no-owner \
        --no-acl \
        --format=plain \
| psql \
    --host="$LOCAL_HOST" \
    --port="$LOCAL_PORT" \
    --username="$LOCAL_USER" \
    --dbname="$LOCAL_DB" \
    --quiet

# ── 3. Print local connection string ────────────────────────────────────────
echo ""
echo "✅ Done. Local DATABASE_URL:"
echo "   postgresql://$LOCAL_USER@$LOCAL_HOST:$LOCAL_PORT/$LOCAL_DB"
echo ""
echo "   To use it for migrations:"
echo "   DATABASE_URL=postgresql://$LOCAL_USER@$LOCAL_HOST:$LOCAL_PORT/$LOCAL_DB alembic upgrade head"

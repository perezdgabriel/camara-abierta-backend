"""create unaccent extension

Search filters compile ``func.unaccent(column)`` (see ``app/core/search.py``)
for accent-insensitive matching. On SQLite this resolves to a registered Python
UDF; on PostgreSQL it needs the ``unaccent`` extension. Without it, every search
query errored on deployed PostgreSQL while passing locally on SQLite.

``unaccent`` is a trusted extension since PG 13, so the app's RDS role can
create it. Idempotent and PostgreSQL-only.

Revision ID: f1a2b3c4d5e6
Revises: 05d02404c207
Create Date: 2026-07-09
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "05d02404c207"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP EXTENSION IF EXISTS unaccent")

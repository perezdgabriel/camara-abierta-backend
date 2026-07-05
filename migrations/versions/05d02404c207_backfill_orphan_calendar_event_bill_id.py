"""backfill orphan calendar_event bill_id

Relinks CalendarEvent rows that were left with ``bill_id IS NULL`` even though
their ``bulletin_number`` now matches an existing bill. These accumulated
because, in serverless mode, the Tabla Semanal ingestor's targeted orphan-bill
ingest runs *inline* — so ``upsert_bill``'s reconcile step fired before the
orphan event row existed, and the event's own write never re-resolved
``bill_id`` from the bulletin. The code fix (``upsert_calendar_event`` now
re-resolves at write time) prevents new orphans; this backfills the old ones.

Idempotent: only touches rows where ``bill_id IS NULL``. See ADR-0017 §9.

Revision ID: 05d02404c207
Revises: a03b709c50e7
Create Date: 2026-07-05
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "05d02404c207"
down_revision = "a03b709c50e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE calendar_events AS c
        SET bill_id = b.id
        FROM bills AS b
        WHERE c.bill_id IS NULL
          AND c.bulletin_number IS NOT NULL
          AND c.bulletin_number = b.bulletin_number
        """
    )


def downgrade() -> None:
    # Data backfill — nothing to undo. The link is indistinguishable from one
    # written by the normal ingest path.
    pass

import datetime
import logging
from typing import Any

from app.core.celery_app import app
from app.core.session import task_session
from app.models.enums import ChamberType
from app.services.write import (
    count_orphan_votes_older_than,
    enrich_legislator_profile,
    upsert_legislator,
    upsert_term_appointment,
)
from app.tasks.base import DatabaseTask

logger = logging.getLogger(__name__)

ORPHAN_VOTE_SLA_DAYS = 7


@app.task(name="app.tasks.legislators.sync_legislator", bind=True, base=DatabaseTask)
def sync_legislator(self, data: dict) -> dict:
    """Upsert one normalized legislator seed (person + terms). See ADR-0015."""
    with task_session() as db:
        legislator = upsert_legislator(db, data)
        return {"legislator_id": legislator.id, "status": "ok"}


@app.task(
    name="app.tasks.legislators.sync_legislator_bcn_enrichment",
    bind=True,
    base=DatabaseTask,
)
def sync_legislator_bcn_enrichment(self, key: str, fields: dict) -> dict:
    """Apply BCN-sourced biographic enrichment to an existing legislator.

    ``key`` is either a ``bcn_uri`` (cross-chamber identity, preferred) or a
    chamber bridge ID (``camara:{Id}`` / ``senado:{PARLID}``). No-op if the
    legislator cannot be resolved — typically means an upstream identity ingest
    has not run yet. See ADR-0015.
    """
    with task_session() as db:
        if key.startswith("camara:") or key.startswith("senado:"):
            legislator = enrich_legislator_profile(
                db, chamber_external_id=key, fields=fields
            )
        else:
            legislator = enrich_legislator_profile(db, bcn_uri=key, fields=fields)
        if legislator is None:
            return {"status": "unmatched", "key": key}
        return {"legislator_id": legislator.id, "status": "ok"}


@app.task(
    name="app.tasks.legislators.sync_parliamentary_appointment",
    bind=True,
    base=DatabaseTask,
)
def sync_parliamentary_appointment(self, bcn_uri: str, data: dict) -> dict:
    """Upsert a BCN ``PositionPeriod`` onto the matching :class:`LegislatorTerm`.

    Matches the legislator by ``bcn_uri`` (BCN's person URI is the
    cross-chamber identity); within their terms, stamps
    ``bcn_appointment_uri`` onto an existing chamber+start row when one
    exists, otherwise opens a new term. See ADR-0015.
    """
    with task_session() as db:
        chamber_value = data["chamber_type"]
        chamber_type = (
            ChamberType(chamber_value)
            if isinstance(chamber_value, str)
            else chamber_value
        )
        term = upsert_term_appointment(
            db,
            bcn_uri=bcn_uri,
            bcn_appointment_uri=data["bcn_appointment_uri"],
            chamber_type=chamber_type,
            start_date=_parse_date(data["start_date"]),
            end_date=_parse_date(data["end_date"]),
        )
        if term is None:
            return {"status": "unmatched", "bcn_uri": bcn_uri}
        return {"term_id": term.id, "status": "ok"}


@app.task(
    name="app.tasks.legislators.alert_orphan_votes",
    bind=True,
    base=DatabaseTask,
)
def alert_orphan_votes(self, sla_days: int = ORPHAN_VOTE_SLA_DAYS) -> dict[str, Any]:
    """Surface the count of orphan votes older than ``sla_days``.

    A vote is "orphan" when it carries a chamber bridge ID but no
    :class:`LegislatorTerm` matches the vote date. Counts older than the SLA
    indicate an unresolved identity that won't be filled in by future ingest
    cycles — operator follow-up required. Returns the count so the beat
    monitor can alert. See ADR-0015.
    """
    with task_session() as db:
        count = count_orphan_votes_older_than(db, sla_days)
    if count > 0:
        logger.warning(
            "orphan-vote SLA breach: %d votes have legislator_id IS NULL "
            "and created_at older than %d days; investigate the chamber "
            "bridge IDs they reference",
            count,
            sla_days,
        )
    return {"orphan_count": count, "sla_days": sla_days}


def _parse_date(value: object) -> datetime.date:
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))

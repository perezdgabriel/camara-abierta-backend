import datetime

from app.core.celery_app import app
from app.core.session import task_session
from app.models.enums import ChamberType
from app.services.write import (
    enrich_legislator_profile,
    upsert_legislator,
    upsert_parliamentary_appointment,
)
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.legislators.sync_legislator", bind=True, base=DatabaseTask)
def sync_legislator(self, data: dict) -> dict:
    with task_session() as db:
        legislator = upsert_legislator(db, data)
        return {"legislator_id": legislator.id, "status": "ok"}


@app.task(
    name="app.tasks.legislators.sync_legislator_bcn_enrichment",
    bind=True,
    base=DatabaseTask,
)
def sync_legislator_bcn_enrichment(self, bcn_id: str, fields: dict) -> dict:
    """Apply BCN-sourced biographic enrichment to an existing legislator.

    No-op if the legislator does not exist yet — the BCN enrichment runs after
    the chamber-source ingest in the same pipeline run, so a missing record
    means an upstream failure, not a normal case.
    """
    with task_session() as db:
        legislator = enrich_legislator_profile(db, bcn_id, fields)
        if legislator is None:
            return {"status": "unmatched", "bcn_id": bcn_id}
        return {"legislator_id": legislator.id, "status": "ok"}


@app.task(
    name="app.tasks.legislators.sync_parliamentary_appointment",
    bind=True,
    base=DatabaseTask,
)
def sync_parliamentary_appointment(self, bcn_id: str, data: dict) -> dict:
    """Upsert a single BCN parliamentary appointment for a legislator.

    Looks up the legislator by ``bcn_id``; if missing, returns ``unmatched``
    (the legislator should have been created by an earlier ``sync_legislator``
    dispatch in the same run).
    """
    from sqlalchemy import select

    from app.models import Legislator

    with task_session() as db:
        legislator = db.execute(
            select(Legislator).where(Legislator.bcn_id == bcn_id)
        ).scalar_one_or_none()
        if legislator is None:
            return {"status": "unmatched", "bcn_id": bcn_id}
        appointment = upsert_parliamentary_appointment(
            db,
            legislator_id=legislator.id,
            bcn_appointment_uri=data["bcn_appointment_uri"],
            chamber_type=ChamberType(data["chamber_type"])
            if isinstance(data["chamber_type"], str)
            else data["chamber_type"],
            start_date=_parse_date(data["start_date"]),
            end_date=_parse_date(data["end_date"]),
        )
        return {"appointment_id": appointment.id, "status": "ok"}


def _parse_date(value: object) -> datetime.date:
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))

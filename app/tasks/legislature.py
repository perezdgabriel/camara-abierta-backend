from app.core.celery_app import app
from app.core.session import task_session
from app.services.write import (
    upsert_legislature,
    upsert_meeting_session,
    upsert_period,
)
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.legislature.sync_period", bind=True, base=DatabaseTask)
def sync_period(self, data: dict) -> dict:
    with task_session() as db:
        period = upsert_period(db, data)
        return {"period_id": period.id, "status": "ok"}


@app.task(name="app.tasks.legislature.sync_legislature", bind=True, base=DatabaseTask)
def sync_legislature(self, data: dict) -> dict:
    with task_session() as db:
        legislature = upsert_legislature(db, data)
        return {"legislature_id": legislature.id, "status": "ok"}


@app.task(
    name="app.tasks.legislature.sync_meeting_session", bind=True, base=DatabaseTask
)
def sync_meeting_session(self, data: dict) -> dict:
    with task_session() as db:
        session = upsert_meeting_session(db, data)
        return {"session_id": session.id, "status": "ok"}

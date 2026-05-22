from app.core.celery_app import app
from app.core.session import task_session
from app.services.write import upsert_period, upsert_session
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.legislature.sync_period", bind=True, base=DatabaseTask)
def sync_period(self, data: dict) -> dict:
    with task_session() as db:
        period = upsert_period(db, data)
        return {"period_id": period.id, "status": "ok"}


@app.task(name="app.tasks.legislature.sync_session", bind=True, base=DatabaseTask)
def sync_session(self, data: dict) -> dict:
    with task_session() as db:
        session = upsert_session(db, data)
        return {"session_id": session.id, "status": "ok"}

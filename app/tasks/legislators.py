from app.core.celery_app import app
from app.core.session import task_session
from app.services.write import upsert_legislator
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.legislators.sync_legislator", bind=True, base=DatabaseTask)
def sync_legislator(self, data: dict) -> dict:
    with task_session() as db:
        legislator = upsert_legislator(db, data)
        return {"legislator_id": legislator.id, "status": "ok"}

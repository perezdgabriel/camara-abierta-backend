from app.core.celery_app import app
from app.core.session import task_session
from app.services.write import upsert_committee
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.committees.sync_committee", bind=True, base=DatabaseTask)
def sync_committee(self, data: dict) -> dict:
	with task_session() as db:
		committee = upsert_committee(db, data)
		return {"committee_id": committee.id, "status": "ok"}
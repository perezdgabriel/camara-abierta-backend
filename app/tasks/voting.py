from app.core.celery_app import app
from app.core.session import task_session
from app.services.write import upsert_voting_session
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.voting.sync_voting_session", bind=True, base=DatabaseTask)
def sync_voting_session(self, data: dict, bill_bulletin: str | None = None) -> dict:
	with task_session() as db:
		voting_session = upsert_voting_session(db, data, bill_bulletin=bill_bulletin)
		return {"voting_session_id": voting_session.id, "status": "ok"}
from app.core.celery_app import app
from app.core.session import task_session
from app.services.write import upsert_topic
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.reference.sync_topic", bind=True, base=DatabaseTask)
def sync_topic(self, data: dict) -> dict:
    with task_session() as db:
        topic = upsert_topic(db, data)
        return {"topic_id": topic.id, "status": "ok"}

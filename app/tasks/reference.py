from app.core.celery_app import app
from app.core.session import task_session
from app.services.write import upsert_district, upsert_region
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.reference.sync_region", bind=True, base=DatabaseTask)
def sync_region(self, data: dict) -> dict:
	with task_session() as db:
		region = upsert_region(db, data)
		return {"region_id": region.id, "status": "ok"}


@app.task(name="app.tasks.reference.sync_district", bind=True, base=DatabaseTask)
def sync_district(self, data: dict) -> dict:
	with task_session() as db:
		district = upsert_district(db, data)
		return {"district_id": district.id, "status": "ok"}
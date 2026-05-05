from app.core.celery_app import app
from app.core.session import task_session
from app.ingestors.parsers.votes import VoteParser
from app.search.bills import delete_bill as es_delete_bill
from app.search.bills import ensure_index, index_bill as es_index_bill
from app.services.pdf import extract_text_from_url
from app.services.proyectos import get_bill
from app.services.write import upsert_bill
from app.tasks.base import DatabaseTask
from app.tasks.voting import sync_voting_session


@app.task(name="app.tasks.bills.sync_bill", bind=True, base=DatabaseTask)
def sync_bill(self, data: dict) -> dict:
	with task_session() as db:
		bill = upsert_bill(db, data)
		bill_id = bill.id

	index_bill.delay(bill_id)

	for raw_vote in data.get("_votaciones", []):
		sync_voting_session.delay(
			VoteParser.parse_senate_vote(raw_vote, bulletin=data["bulletin_number"]),
			data["bulletin_number"],
		)
	return {"bill_id": bill_id, "status": "ok"}


@app.task(name="app.tasks.bills.index_bill", bind=True, base=DatabaseTask)
def index_bill(self, bill_id: int) -> dict:
	with task_session() as db:
		bill = get_bill(db, bill_id)
		if bill is None:
			return {"bill_id": bill_id, "status": "missing"}
		if bill.full_text is None and bill.full_text_url:
			bill.full_text = extract_text_from_url(bill.full_text_url)

	with task_session() as db:
		bill = get_bill(db, bill_id)
		if bill is None:
			return {"bill_id": bill_id, "status": "missing"}
		ensure_index()
		es_index_bill(bill)
	return {"bill_id": bill_id, "status": "indexed"}


@app.task(name="app.tasks.bills.delete_bill_from_index", bind=True, base=DatabaseTask)
def delete_bill_from_index(self, bill_id: int) -> dict:
	es_delete_bill(bill_id)
	return {"bill_id": bill_id, "status": "deleted"}
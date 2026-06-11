from app.core.celery_app import app
from app.core.config import settings
from app.core.session import task_session
from app.ingestors.parsers.votes import VoteParser
from app.search.bills import delete_bill as es_delete_bill
from app.search.bills import ensure_index
from app.search.bills import index_bill as es_index_bill
from app.services.llm import can_generate_bill_summary, generate_bill_summary
from app.services.notifications import send_alerta_proyecto
from app.services.pdf import extract_text_from_url
from app.services.proyectos import get_bill
from app.services.write import (
    update_bill_ai_summary,
    update_bill_full_text,
    upsert_bill,
)
from app.tasks.base import DatabaseTask
from app.tasks.voting import sync_voting_session


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


@app.task(name="app.tasks.bills.sync_bill", bind=True, base=DatabaseTask)
def sync_bill(self, data: dict) -> dict:
    with task_session() as db:
        bill, change_info = upsert_bill(db, data)
        bill_id = bill.id

    generate_bill_ai_summary.delay(bill_id)

    for raw_vote in data.get("_votaciones", []):
        sync_voting_session.delay(
            VoteParser.parse_senate_vote(raw_vote, bulletin=data["bulletin_number"]),
            data["bulletin_number"],
        )

    # ADR-0010: the dedicated chamber-votes task owns this dispatch in the
    # default ``bulk`` configuration. The embedded loop here is the failover
    # path, activated via ``INGESTOR_CHAMBER_VOTES_SOURCE=bill_detail``.
    if settings.ingestor_chamber_votes_source == "bill_detail":
        for raw_vote in data.get("_camara_votaciones", []):
            if not raw_vote.get("id"):
                continue
            sync_voting_session.delay(
                VoteParser.parse_chamber_vote(
                    raw_vote, bulletin=data["bulletin_number"]
                ),
                data["bulletin_number"],
            )

    bulletin_number = data["bulletin_number"]
    title = data.get("title") or ""

    if change_info["is_new"]:
        send_alerta_proyecto(
            bulletin_number=bulletin_number,
            title=title,
            change_type="new",
            extra={
                "entry_date": str(data.get("entry_date") or ""),
                "origin": _enum_value(
                    data.get("origin_type") or data.get("origin") or ""
                ),
            },
        )

    if change_info["status_changed"]:
        send_alerta_proyecto(
            bulletin_number=bulletin_number,
            title=title,
            change_type="status_changed",
            extra={
                "old_status": _enum_value(change_info.get("old_status") or ""),
                "new_status": _enum_value(change_info.get("new_status") or ""),
            },
        )

    if change_info["stage_changed"]:
        send_alerta_proyecto(
            bulletin_number=bulletin_number,
            title=title,
            change_type="stage_changed",
        )

    return {"bill_id": bill_id, "status": "ok"}


@app.task(name="app.tasks.bills.generate_bill_ai_summary", bind=True, base=DatabaseTask)
def generate_bill_ai_summary(self, bill_id: int) -> dict:
    if not can_generate_bill_summary():
        return {"bill_id": bill_id, "status": "llm_unavailable"}

    with task_session() as db:
        bill = get_bill(db, bill_id)
        if bill is None:
            return {"bill_id": bill_id, "status": "missing"}
        full_text = bill.full_text
        full_text_url = bill.full_text_url

    if not full_text and full_text_url:
        extracted_text = extract_text_from_url(full_text_url)
        if extracted_text:
            with task_session() as db:
                updated_bill = update_bill_full_text(db, bill_id, extracted_text)
                full_text = (
                    updated_bill.full_text
                    if updated_bill is not None
                    else extracted_text
                )

    if not full_text:
        return {"bill_id": bill_id, "status": "skipped"}

    summary = generate_bill_summary(full_text)
    with task_session() as db:
        updated_bill = update_bill_ai_summary(db, bill_id, summary)
        if updated_bill is None:
            return {"bill_id": bill_id, "status": "missing"}
    return {"bill_id": bill_id, "status": "summarized"}


@app.task(name="app.tasks.bills.index_bill", bind=True, base=DatabaseTask)
def index_bill(self, bill_id: int) -> dict:
    with task_session() as db:
        bill = get_bill(db, bill_id)
        if bill is None:
            return {"bill_id": bill_id, "status": "missing"}
        full_text_url = bill.full_text_url
        full_text = bill.full_text

    if full_text is None and full_text_url:
        extracted_text = extract_text_from_url(full_text_url)
        if extracted_text:
            with task_session() as db:
                update_bill_full_text(db, bill_id, extracted_text)

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

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.legislature import Legislator
from app.models.proyecto import Bill, BillAuthorship, BillEvent, BillStage, BillUrgency, bill_topics
from app.models.votacion import VotingSession

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def list_bills(
    db: Session,
    *,
    status: str | None,
    bill_type: str | None,
    origin: str | None,
    topic_id: int | None,
    date_from: date | None,
    date_to: date | None,
    law_number: str | None,
    offset: int,
    limit: int,
) -> tuple[int, list[Bill]]:
    query = (
        db.query(Bill)
        .options(
            joinedload(Bill.origin_chamber),
            joinedload(Bill.current_chamber),
            joinedload(Bill.current_committee),
            selectinload(Bill.topics),
            selectinload(Bill.urgencies).joinedload(BillUrgency.chamber),
            selectinload(Bill.stages).options(
                joinedload(BillStage.chamber),
                joinedload(BillStage.committee),
            ),
        )
    )
    count_query = db.query(func.count(Bill.id.distinct()))

    if status:
        query = query.filter(Bill.status == status)
        count_query = count_query.filter(Bill.status == status)
    if bill_type:
        query = query.filter(Bill.bill_type == bill_type)
        count_query = count_query.filter(Bill.bill_type == bill_type)
    if origin:
        query = query.filter(Bill.origin == origin)
        count_query = count_query.filter(Bill.origin == origin)
    if topic_id:
        query = query.join(bill_topics, Bill.id == bill_topics.c.bill_id).filter(
            bill_topics.c.topic_id == topic_id
        )
        count_query = count_query.join(bill_topics, Bill.id == bill_topics.c.bill_id).filter(
            bill_topics.c.topic_id == topic_id
        )
    if date_from:
        query = query.filter(Bill.entry_date >= date_from)
        count_query = count_query.filter(Bill.entry_date >= date_from)
    if date_to:
        query = query.filter(Bill.entry_date <= date_to)
        count_query = count_query.filter(Bill.entry_date <= date_to)
    if law_number:
        query = query.filter(Bill.law_number == law_number)
        count_query = count_query.filter(Bill.law_number == law_number)

    total = count_query.scalar() or 0
    rows = (
        query
        .order_by(Bill.entry_date.desc(), Bill.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, rows


def _full_options():
    """SQLAlchemy load options for a fully populated Bill (detail view)."""
    return [
        joinedload(Bill.origin_chamber),
        joinedload(Bill.current_chamber),
        joinedload(Bill.current_committee),
        selectinload(Bill.topics),
        selectinload(Bill.authorships).options(
            joinedload(BillAuthorship.legislator).options(
                joinedload(Legislator.party)
            )
        ),
        selectinload(Bill.stages).options(
            joinedload(BillStage.chamber),
            joinedload(BillStage.committee),
        ),
        selectinload(Bill.events).joinedload(BillEvent.chamber),
        selectinload(Bill.documents),
        selectinload(Bill.urgencies).joinedload(BillUrgency.chamber),
        selectinload(Bill.voting_sessions).joinedload(VotingSession.chamber),
    ]


def get_bill(db: Session, bill_id: int) -> Bill | None:
    return (
        db.query(Bill)
        .options(*_full_options())
        .filter(Bill.id == bill_id)
        .first()
    )


def get_bill_by_bulletin(db: Session, bulletin_number: str) -> Bill | None:
    bill = db.query(Bill).filter(Bill.bulletin_number == bulletin_number).first()
    if bill is None:
        return None
    return get_bill(db, bill.id)


def bill_to_summary_extra(bill: Bill) -> dict:
    """Compute denormalised convenience fields not present on the ORM object."""
    active_urgency = next((u for u in bill.urgencies if u.is_active), None)
    current_stage = next((s for s in bill.stages if s.is_current), None)
    return {
        "active_urgency_type": active_urgency.urgency_type if active_urgency else None,
        "current_stage_type": current_stage.stage_type if current_stage else None,
    }


def list_bills_by_ids(db: Session, bill_ids: list[int]) -> list[Bill]:
    """Fetch bills by a list of IDs, preserving the given order (used after ES search)."""
    if not bill_ids:
        return []
    rows = (
        db.query(Bill)
        .options(
            joinedload(Bill.origin_chamber),
            joinedload(Bill.current_chamber),
            joinedload(Bill.current_committee),
            selectinload(Bill.topics),
            selectinload(Bill.urgencies).joinedload(BillUrgency.chamber),
            selectinload(Bill.stages).options(
                joinedload(BillStage.chamber),
                joinedload(BillStage.committee),
            ),
        )
        .filter(Bill.id.in_(bill_ids))
        .all()
    )
    # Re-order to match ES ranking order
    order = {bid: i for i, bid in enumerate(bill_ids)}
    return sorted(rows, key=lambda b: order.get(b.id, len(bill_ids)))

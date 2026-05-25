from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.core import Topic
from app.models.enums import BillStatus, ChamberType
from app.models.legislature import Legislator, PoliticalParty
from app.models.proyecto import Bill, BillEvent, BillUrgency, bill_topics
from app.models.votacion import VotingSession
from app.services import proyectos as bills_svc

RECENT_EVENTS_LIMIT = 8
TOPIC_DISTRIBUTION_LIMIT = 8
FEATURED_BILLS_LIMIT = 3


def _stats(db: Session) -> dict[str, int]:
    bills_active = (
        db.query(func.count(Bill.id)).filter(Bill.status == BillStatus.PENDING).scalar()
        or 0
    )
    bills_with_urgency = (
        db.query(func.count(Bill.id))
        .filter(Bill.urgencies.any(BillUrgency.is_active.is_(True)))
        .scalar()
        or 0
    )
    week_ago = datetime.now() - timedelta(days=7)
    voted_this_week = (
        db.query(func.count(VotingSession.id))
        .filter(VotingSession.voting_date >= week_ago)
        .scalar()
        or 0
    )
    year_start = date(date.today().year, 1, 1)
    enacted_this_year = (
        db.query(func.count(Bill.id))
        .filter(
            Bill.status.in_([BillStatus.ENACTED, BillStatus.PUBLISHED]),
            Bill.publication_date >= year_start,
        )
        .scalar()
        or 0
    )
    return {
        "bills_active": bills_active,
        "bills_with_urgency": bills_with_urgency,
        "voted_this_week": voted_this_week,
        "enacted_this_year": enacted_this_year,
    }


def _recent_events(db: Session) -> list[BillEvent]:
    return (
        db.query(BillEvent)
        .options(joinedload(BillEvent.bill), joinedload(BillEvent.chamber))
        .order_by(BillEvent.event_date.desc(), BillEvent.id.desc())
        .limit(RECENT_EVENTS_LIMIT)
        .all()
    )


def _topic_distribution(db: Session) -> list[tuple[Topic, int]]:
    rows = (
        db.query(Topic, func.count(bill_topics.c.bill_id).label("count"))
        .join(bill_topics, Topic.id == bill_topics.c.topic_id)
        .group_by(Topic.id)
        .order_by(func.count(bill_topics.c.bill_id).desc())
        .limit(TOPIC_DISTRIBUTION_LIMIT)
        .all()
    )
    return [(topic, count) for topic, count in rows]


def _chamber_composition(db: Session) -> list[tuple[PoliticalParty, int]]:
    rows = (
        db.query(PoliticalParty, func.count(Legislator.id).label("count"))
        .join(Legislator, Legislator.party_id == PoliticalParty.id)
        .filter(
            Legislator.chamber_type == ChamberType.DEPUTIES,
            Legislator.is_active.is_(True),
        )
        .group_by(PoliticalParty.id)
        .order_by(func.count(Legislator.id).desc())
        .all()
    )
    return [(party, count) for party, count in rows]


def _featured_bills(db: Session) -> list[Bill]:
    _, bills = bills_svc.list_bills(
        db,
        status=BillStatus.PENDING,
        bill_type=None,
        origin=None,
        topic_id=None,
        date_from=None,
        date_to=None,
        law_number=None,
        sort=bills_svc.BillSort.RECENT_ACTIVITY,
        offset=0,
        limit=FEATURED_BILLS_LIMIT,
    )
    return bills


def get_dashboard(db: Session) -> dict:
    return {
        "stats": _stats(db),
        "recent_events": _recent_events(db),
        "topic_distribution": _topic_distribution(db),
        "chamber_composition": _chamber_composition(db),
        "featured_bills": _featured_bills(db),
    }

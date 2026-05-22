from datetime import date, datetime, time

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.enums import ChamberType
from app.models.legislature import Chamber, Legislator
from app.models.votacion import Vote, VotingSession

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _start_of_day(value: date) -> datetime:
    return datetime.combine(value, time.min)


def _end_of_day(value: date) -> datetime:
    return datetime.combine(value, time.max)


def list_voting_sessions(
    db: Session,
    *,
    date_from: date | None,
    date_to: date | None,
    chamber: ChamberType | None,
    bill_id: int | None,
    offset: int,
    limit: int,
) -> tuple[int, list[VotingSession]]:
    query = db.query(VotingSession).options(
        joinedload(VotingSession.chamber),
        joinedload(VotingSession.bill),
    )
    count_query = db.query(func.count(VotingSession.id))

    if date_from is not None:
        clause = VotingSession.voting_date >= _start_of_day(date_from)
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if date_to is not None:
        clause = VotingSession.voting_date <= _end_of_day(date_to)
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if chamber is not None:
        clause = VotingSession.chamber.has(Chamber.chamber_type == chamber)
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if bill_id is not None:
        query = query.filter(VotingSession.bill_id == bill_id)
        count_query = count_query.filter(VotingSession.bill_id == bill_id)

    total = count_query.scalar() or 0
    rows = (
        query.order_by(VotingSession.voting_date.desc(), VotingSession.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, rows


def get_voting_session(db: Session, voting_session_id: int) -> VotingSession | None:
    return (
        db.query(VotingSession)
        .options(
            joinedload(VotingSession.chamber),
            joinedload(VotingSession.bill),
            selectinload(VotingSession.votes).options(
                joinedload(Vote.legislator).joinedload(Legislator.party),
            ),
        )
        .filter(VotingSession.id == voting_session_id)
        .first()
    )

from datetime import date, datetime, time

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.enums import ChamberType, SignalType, VotingResult
from app.models.legislature import Chamber, Legislator, LegislatorTerm
from app.models.votacion import Vote, VotingSession, VotingSessionSignal
from app.schemas.voting import (
    LegislatorBrief,
    PartyBrief,
    VoteDetail,
)

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
    signal_type: SignalType | None = None,
    result: VotingResult | None = None,
    q: str | None = None,
    offset: int,
    limit: int,
) -> tuple[int, list[VotingSession]]:
    query = db.query(VotingSession).options(
        joinedload(VotingSession.chamber),
        joinedload(VotingSession.bill),
        selectinload(VotingSession.signals),
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
    if result is not None:
        query = query.filter(VotingSession.result == result)
        count_query = count_query.filter(VotingSession.result == result)
    if q:
        clause = VotingSession.subject.ilike(f"%{q}%")
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if signal_type is not None:
        # EXISTS subquery against the signal table — covers "any session that
        # fired this signal type." Index on signal_type makes this cheap.
        sig_clause = (
            db.query(VotingSessionSignal.id)
            .filter(
                VotingSessionSignal.voting_session_id == VotingSession.id,
                VotingSessionSignal.signal_type == signal_type,
            )
            .exists()
        )
        query = query.filter(sig_clause)
        count_query = count_query.filter(sig_clause)

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
            selectinload(VotingSession.signals),
            selectinload(VotingSession.votes).options(
                # Vote-time party reads walk ``Legislator.terms`` for the term
                # covering ``VotingSession.voting_date`` — eager-load
                # terms/party/chamber to avoid N+1. See CONTEXT.md
                # "Vote-time party" and ADR-0015.
                joinedload(Vote.legislator)
                .selectinload(Legislator.terms)
                .joinedload(LegislatorTerm.party),
                joinedload(Vote.legislator)
                .selectinload(Legislator.terms)
                .joinedload(LegislatorTerm.chamber),
            ),
        )
        .filter(VotingSession.id == voting_session_id)
        .first()
    )


def build_vote_details(session: VotingSession) -> list[VoteDetail]:
    """Vote rows with party/chamber_type resolved at ``voting_date``.

    Resolves through ``Legislator.party_on`` / ``chamber_type_on`` so that a
    historical session renders each legislator's party as it was on the date
    of the vote, not today's active term. Orphan votes (no resolved
    legislator) pass through with ``legislator=None``.
    """
    d = session.voting_date.date()
    rows: list[VoteDetail] = []
    for vote in session.votes:
        legislator_brief: LegislatorBrief | None = None
        if vote.legislator is not None:
            party = vote.legislator.party_on(d)
            legislator_brief = LegislatorBrief(
                id=vote.legislator.id,
                full_name=vote.legislator.full_name,
                chamber_type=vote.legislator.chamber_type_on(d),
                party=PartyBrief.model_validate(party) if party else None,
            )
        rows.append(
            VoteDetail(
                id=vote.id,
                vote=vote.vote,
                legislator=legislator_brief,
                legislator_external_id=vote.legislator_external_id,
            )
        )
    return rows

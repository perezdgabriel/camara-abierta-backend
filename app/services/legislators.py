from sqlalchemy import ColumnElement, case, func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.core import Circumscription, District, Region, Topic
from app.models.enums import ChamberType, VoteChoice
from app.models.legislature import (
    Committee,
    CommitteeMembership,
    Legislator,
    LegislatorTerm,
    PoliticalParty,
)
from app.models.proyecto import bill_topics
from app.models.votacion import Vote, VotingSession

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_RECORD_LIMIT = 60
TOPIC_AFFINITY_LIMIT = 8

# Sentinel value for `party` query param meaning "Legislator.party_id IS NULL".
# Independents are not a party (see CONTEXT.md "Independent legislator") so we
# can't filter them by abbreviation. A sentinel keeps the API contract simple
# without inventing an Independent party row.
PARTY_INDEPENDENT_SENTINEL = "__independent__"


def _count_choice(choice: VoteChoice):
    return func.coalesce(func.sum(case((Vote.vote == choice, 1), else_=0)), 0)


def get_legislator_voting_summary(db: Session, legislator_id: int) -> dict:
    row = (
        db.query(
            func.count(Vote.id).label("total"),
            _count_choice(VoteChoice.FOR).label("votes_for"),
            _count_choice(VoteChoice.AGAINST).label("votes_against"),
            _count_choice(VoteChoice.ABSTAIN).label("abstentions"),
            _count_choice(VoteChoice.ABSENT).label("absences"),
        )
        .filter(Vote.legislator_id == legislator_id)
        .one()
    )
    total = int(row.total or 0)
    absences = int(row.absences or 0)
    votes_for = int(row.votes_for or 0)
    votes_against = int(row.votes_against or 0)
    abstentions = int(row.abstentions or 0)
    attendance = round((total - absences) / total * 100, 1) if total else 0.0
    participation = (
        round((votes_for + votes_against + abstentions) / total * 100, 1)
        if total
        else 0.0
    )
    return {
        "total_sessions": total,
        "votes_for": votes_for,
        "votes_against": votes_against,
        "abstentions": abstentions,
        "absences": absences,
        "attendance_percentage": attendance,
        "participation_rate": participation,
    }


def get_legislator_voting_record(
    db: Session, legislator_id: int, limit: int = DEFAULT_RECORD_LIMIT
) -> list[dict]:
    rows = (
        db.query(Vote, VotingSession)
        .join(VotingSession, Vote.voting_session_id == VotingSession.id)
        .filter(Vote.legislator_id == legislator_id)
        .order_by(VotingSession.voting_date.desc(), Vote.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": vote.id,
            "voting_session_id": session.id,
            "vote": vote.vote,
            "date": session.voting_date.date(),
            "subject": session.subject,
            "result": session.result,
        }
        for vote, session in rows
    ]


def get_legislator_topic_affinity(db: Session, legislator_id: int) -> list[dict]:
    rows = (
        db.query(
            Topic,
            _count_choice(VoteChoice.FOR).label("for_"),
            _count_choice(VoteChoice.AGAINST).label("against"),
            _count_choice(VoteChoice.ABSTAIN).label("abstain"),
        )
        .select_from(Vote)
        .join(VotingSession, Vote.voting_session_id == VotingSession.id)
        .join(bill_topics, bill_topics.c.bill_id == VotingSession.bill_id)
        .join(Topic, Topic.id == bill_topics.c.topic_id)
        .filter(Vote.legislator_id == legislator_id)
        .group_by(Topic.id)
        .order_by(func.count(Vote.id).desc())
        .limit(TOPIC_AFFINITY_LIMIT)
        .all()
    )
    return [
        {
            "topic": topic,
            "for": int(for_ or 0),
            "against": int(against or 0),
            "abstain": int(abstain or 0),
        }
        for topic, for_, against, abstain in rows
    ]


def list_legislators(
    db: Session,
    *,
    q: str | None,
    party: str | None,
    district: int | None,
    circumscription: int | None,
    region: int | None,
    chamber_type: ChamberType | None,
    include_inactive: bool,
    offset: int,
    limit: int,
) -> tuple[int, list[Legislator]]:
    query = db.query(Legislator).options(
        joinedload(Legislator.party).selectinload(PoliticalParty.bloc_affiliations),
        joinedload(Legislator.district),
        joinedload(Legislator.circumscription),
        # voting_lean (on LegislatorSummary) reads this; eager-load to avoid N+1.
        selectinload(Legislator.voting_stats),
    )
    count_query = db.query(func.count(Legislator.id))

    filters: list[ColumnElement[bool]] = []
    if q:
        filters.append(Legislator.full_name.ilike(f"%{q}%"))
    if party == PARTY_INDEPENDENT_SENTINEL:
        filters.append(Legislator.party_id.is_(None))
    elif party:
        filters.append(
            Legislator.party.has(PoliticalParty.abbreviation == party),
        )
    if district is not None:
        filters.append(Legislator.district.has(District.number == district))
    if circumscription is not None:
        filters.append(
            Legislator.circumscription.has(Circumscription.number == circumscription),
        )
    if region is not None:
        if chamber_type == ChamberType.DEPUTIES:
            filters.append(Legislator.district.has(District.region_id == region))
        elif chamber_type == ChamberType.SENATE:
            filters.append(
                Legislator.circumscription.has(
                    Circumscription.regions.any(Region.id == region),
                ),
            )
        else:
            # No chamber selected: match a legislator whose district OR
            # circumscription is in the region. Senators' circumscriptions are
            # many-to-many with regions (see app/models/core.py).
            filters.append(
                Legislator.district.has(District.region_id == region)
                | Legislator.circumscription.has(
                    Circumscription.regions.any(Region.id == region),
                ),
            )
    if chamber_type is not None:
        filters.append(Legislator.chamber_type == chamber_type)
    if not include_inactive:
        filters.append(Legislator.is_active.is_(True))

    for clause in filters:
        query = query.filter(clause)
        count_query = count_query.filter(clause)

    total = count_query.scalar() or 0
    rows = (
        query.order_by(Legislator.last_name.asc(), Legislator.first_name.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, rows


def get_legislator(db: Session, legislator_id: int) -> Legislator | None:
    return (
        db.query(Legislator)
        .options(
            joinedload(Legislator.party).selectinload(PoliticalParty.bloc_affiliations),
            joinedload(Legislator.district),
            joinedload(Legislator.circumscription),
            selectinload(Legislator.terms).options(
                joinedload(LegislatorTerm.chamber),
                joinedload(LegislatorTerm.party),
            ),
            selectinload(Legislator.committee_memberships).options(
                joinedload(CommitteeMembership.committee).joinedload(Committee.chamber),
            ),
            joinedload(Legislator.voting_stats),
        )
        .filter(Legislator.id == legislator_id)
        .first()
    )

from datetime import date

from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.core import Circumscription, District, Region, Topic
from app.models.enums import BillOrigin, ChamberType, VoteChoice
from app.models.legislature import (
    Chamber,
    Committee,
    CommitteeMembership,
    Legislator,
    LegislativePeriod,
    LegislatorTerm,
    PoliticalParty,
)
from app.models.proyecto import (
    Bill,
    BillAuthorship,
    BillUrgency,
    bill_topics,
)
from app.models.votacion import Vote, VotingSession

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_RECORD_LIMIT = 60
TOPIC_AFFINITY_LIMIT = 8

# Sentinel value for `party` query param meaning "currently has no party".
# Independents are not a party (see CONTEXT.md "Independent legislator") so we
# can't filter them by abbreviation. A sentinel keeps the API contract simple
# without inventing an Independent party row.
PARTY_INDEPENDENT_SENTINEL = "__independent__"


def active_term_subquery(today: date | None = None):
    """Scalar subquery returning ids of legislators with an open term today.

    ``Legislator`` no longer carries a stored chamber/party/is_active — those
    are properties of the active :class:`LegislatorTerm`. List/filter queries
    use this subquery to express "currently serving" without the legacy
    column. See ADR-0015.
    """
    today = today or date.today()
    return (
        select(LegislatorTerm.legislator_id)
        .where(LegislatorTerm.start_date <= today)
        .where(or_(LegislatorTerm.end_date.is_(None), LegislatorTerm.end_date >= today))
    )


def _active_term_with_chamber_subquery(chamber: ChamberType, today: date | None = None):
    today = today or date.today()
    return (
        select(LegislatorTerm.legislator_id)
        .join(Chamber, Chamber.id == LegislatorTerm.chamber_id)
        .where(LegislatorTerm.start_date <= today)
        .where(or_(LegislatorTerm.end_date.is_(None), LegislatorTerm.end_date >= today))
        .where(Chamber.chamber_type == chamber)
    )


def _count_choice(choice: VoteChoice):
    return func.coalesce(func.sum(case((Vote.vote == choice, 1), else_=0)), 0)


def get_legislator_voting_summary(db: Session, legislator_id: int) -> dict:
    row = (
        db.query(
            func.count(Vote.id).label("total"),
            _count_choice(VoteChoice.FOR).label("votes_for"),
            _count_choice(VoteChoice.AGAINST).label("votes_against"),
            _count_choice(VoteChoice.ABSTAIN).label("abstentions"),
            _count_choice(VoteChoice.NO_VOTE).label("no_votes"),
        )
        .filter(Vote.legislator_id == legislator_id)
        .one()
    )
    total = int(row.total or 0)
    no_votes = int(row.no_votes or 0)
    votes_for = int(row.votes_for or 0)
    votes_against = int(row.votes_against or 0)
    abstentions = int(row.abstentions or 0)
    record_rate = round((total - no_votes) / total * 100, 1) if total else 0.0
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
        "no_votes": no_votes,
        "record_rate": record_rate,
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
    """List legislators with filters on the *currently active* term.

    Filters that target chamber/party/district/circumscription/is_active now
    resolve against the active :class:`LegislatorTerm` (those columns were
    removed from ``Legislator`` per ADR-0015). The query loads the term tree
    eagerly so the API can project ``current_*`` properties without N+1.
    """
    today = date.today()
    query = db.query(Legislator).options(
        selectinload(Legislator.terms).options(
            joinedload(LegislatorTerm.chamber),
            joinedload(LegislatorTerm.party).selectinload(
                PoliticalParty.bloc_affiliations
            ),
            joinedload(LegislatorTerm.district),
            joinedload(LegislatorTerm.circumscription),
        ),
        # voting_lean (on LegislatorSummary) reads this; eager-load to avoid N+1.
        selectinload(Legislator.voting_stats),
    )
    count_query = db.query(func.count(Legislator.id))

    filters: list[ColumnElement[bool]] = []
    if q:
        filters.append(Legislator.full_name.ilike(f"%{q}%"))

    # Party filter: needs to look at the *active* term's party.
    if party == PARTY_INDEPENDENT_SENTINEL:
        # Active term exists, but its party_id is NULL.
        active_with_party = active_term_subquery(today).where(
            LegislatorTerm.party_id.isnot(None)
        )
        filters.append(Legislator.id.in_(active_term_subquery(today)))
        filters.append(~Legislator.id.in_(active_with_party))
    elif party:
        party_filter = (
            active_term_subquery(today)
            .join(PoliticalParty, PoliticalParty.id == LegislatorTerm.party_id)
            .where(PoliticalParty.abbreviation == party)
        )
        filters.append(Legislator.id.in_(party_filter))

    if district is not None:
        district_filter = (
            active_term_subquery(today)
            .join(District, District.id == LegislatorTerm.district_id)
            .where(District.number == district)
        )
        filters.append(Legislator.id.in_(district_filter))

    if circumscription is not None:
        circ_filter = (
            active_term_subquery(today)
            .join(
                Circumscription, Circumscription.id == LegislatorTerm.circumscription_id
            )
            .where(Circumscription.number == circumscription)
        )
        filters.append(Legislator.id.in_(circ_filter))

    if region is not None:
        if chamber_type == ChamberType.DEPUTIES:
            region_filter = (
                active_term_subquery(today)
                .join(District, District.id == LegislatorTerm.district_id)
                .where(District.region_id == region)
            )
            filters.append(Legislator.id.in_(region_filter))
        elif chamber_type == ChamberType.SENATE:
            region_filter = (
                active_term_subquery(today)
                .join(
                    Circumscription,
                    Circumscription.id == LegislatorTerm.circumscription_id,
                )
                .where(Circumscription.regions.any(Region.id == region))
            )
            filters.append(Legislator.id.in_(region_filter))
        else:
            district_filter = (
                active_term_subquery(today)
                .join(District, District.id == LegislatorTerm.district_id)
                .where(District.region_id == region)
            )
            circ_filter = (
                active_term_subquery(today)
                .join(
                    Circumscription,
                    Circumscription.id == LegislatorTerm.circumscription_id,
                )
                .where(Circumscription.regions.any(Region.id == region))
            )
            filters.append(
                or_(
                    Legislator.id.in_(district_filter),
                    Legislator.id.in_(circ_filter),
                )
            )

    if chamber_type is not None:
        filters.append(
            Legislator.id.in_(_active_term_with_chamber_subquery(chamber_type, today))
        )
    if not include_inactive:
        filters.append(Legislator.id.in_(active_term_subquery(today)))

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
            selectinload(Legislator.terms).options(
                joinedload(LegislatorTerm.chamber),
                joinedload(LegislatorTerm.party).selectinload(
                    PoliticalParty.bloc_affiliations
                ),
                joinedload(LegislatorTerm.district),
                joinedload(LegislatorTerm.circumscription),
                joinedload(LegislatorTerm.period),
            ),
            selectinload(Legislator.committee_memberships).options(
                joinedload(CommitteeMembership.committee).joinedload(Committee.chamber),
            ),
            joinedload(Legislator.voting_stats),
        )
        .filter(Legislator.id == legislator_id)
        .first()
    )


def get_legislator_overlapping_periods(
    db: Session, legislator_id: int
) -> list[LegislativePeriod]:
    """Every ``LegislativePeriod`` whose date range overlaps any of the
    legislator's terms, sorted most-recent first.

    A senate stint that crosses a March-11 boundary is one term tagged to the
    older Período but actually overlaps two — both surface here so the UI can
    render the term row under each. Term dates are inclusive (ADR-0015);
    Período dates are half-open (ADR-0016), so the overlap predicate is
    ``term.start_date < period.end_date AND (term.end_date IS NULL OR
    term.end_date >= period.start_date)``.
    """
    return list(
        db.execute(
            select(LegislativePeriod)
            .where(
                select(LegislatorTerm.id)
                .where(LegislatorTerm.legislator_id == legislator_id)
                .where(LegislatorTerm.start_date < LegislativePeriod.end_date)
                .where(
                    or_(
                        LegislatorTerm.end_date.is_(None),
                        LegislatorTerm.end_date >= LegislativePeriod.start_date,
                    )
                )
                .exists()
            )
            .order_by(LegislativePeriod.start_date.desc())
        )
        .scalars()
        .all()
    )


DEFAULT_AUTHORED_BILLS_LIMIT = 10
MAX_AUTHORED_BILLS_LIMIT = 100


def get_legislator_authored_bills(
    db: Session, legislator_id: int, limit: int
) -> tuple[list[Bill], int]:
    """Return (items, total) for mociones authored by this legislator.

    Filters to ``BillOrigin.DEPUTIES`` because the UI surface is moción-only
    (mensajes don't have individual author rows in practice). Eager-load
    set mirrors :func:`app.services.proyectos.list_bills` so the resulting
    rows are ready for ``BillSummary`` rendering without N+1s.
    """
    base_join = (
        db.query(Bill)
        .join(BillAuthorship, BillAuthorship.bill_id == Bill.id)
        .filter(
            BillAuthorship.legislator_id == legislator_id,
            Bill.origin == BillOrigin.DEPUTIES,
        )
    )
    total = base_join.with_entities(func.count(Bill.id.distinct())).scalar() or 0
    items = (
        base_join.options(
            joinedload(Bill.origin_chamber),
            joinedload(Bill.current_chamber),
            joinedload(Bill.current_committee),
            selectinload(Bill.events),
            selectinload(Bill.topics),
            selectinload(Bill.urgencies).joinedload(BillUrgency.chamber),
            selectinload(Bill.stages),
            selectinload(Bill.voting_sessions),
        )
        .order_by(Bill.entry_date.desc(), Bill.id.desc())
        .limit(limit)
        .all()
    )
    return items, int(total)

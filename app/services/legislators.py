from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.core import Circumscription, District
from app.models.enums import ChamberType
from app.models.legislature import (
    Committee,
    CommitteeMembership,
    Legislator,
    LegislatorTerm,
    PoliticalParty,
)

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def list_legislators(
    db: Session,
    *,
    party: str | None,
    district: int | None,
    circumscription: int | None,
    chamber_type: ChamberType | None,
    offset: int,
    limit: int,
) -> tuple[int, list[Legislator]]:
    query = db.query(Legislator).options(
        joinedload(Legislator.party),
        joinedload(Legislator.district),
        joinedload(Legislator.circumscription),
    )
    count_query = db.query(func.count(Legislator.id))

    if party:
        clause = Legislator.party.has(PoliticalParty.name.ilike(f"%{party}%"))
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if district is not None:
        clause = Legislator.district.has(District.number == district)
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if circumscription is not None:
        clause = Legislator.circumscription.has(
            Circumscription.number == circumscription
        )
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if chamber_type is not None:
        query = query.filter(Legislator.chamber_type == chamber_type)
        count_query = count_query.filter(Legislator.chamber_type == chamber_type)

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
            joinedload(Legislator.party),
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

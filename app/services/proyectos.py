from datetime import date
from enum import StrEnum

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.core.search import unaccent_ilike
from app.models.enums import (
    BillOrigin,
    BillStatus,
    BillSummaryKind,
    BillType,
    ChamberType,
    StageType,
)
from app.models.legislature import Chamber, Legislator, LegislatorTerm
from app.models.proyecto import (
    Bill,
    BillAuthorship,
    BillDocument,
    BillEvent,
    BillStage,
    BillUrgency,
    bill_topics,
)
from app.models.votacion import VotingSession

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class BillSort(StrEnum):
    RECENT_ACTIVITY = "recent_activity"
    ENTRY_DATE = "entry_date"
    BULLETIN = "bulletin"


def _latest_bill_activity_subquery(db: Session):
    return (
        db.query(
            BillEvent.bill_id.label("bill_id"),
            func.max(BillEvent.event_date).label("last_activity_date"),
        )
        .group_by(BillEvent.bill_id)
        .subquery()
    )


def list_bills(
    db: Session,
    *,
    status: BillStatus | None,
    bill_type: BillType | None,
    origin: BillOrigin | None,
    topic_id: int | None,
    current_chamber: ChamberType | None = None,
    has_urgency: bool | None = None,
    search: str | None = None,
    date_from: date | None,
    date_to: date | None,
    law_number: str | None,
    sort: BillSort,
    offset: int,
    limit: int,
) -> tuple[int, list[Bill]]:
    query = db.query(Bill).options(
        joinedload(Bill.origin_chamber),
        joinedload(Bill.current_chamber),
        joinedload(Bill.current_committee),
        selectinload(Bill.events),
        selectinload(Bill.topics),
        selectinload(Bill.urgencies).joinedload(BillUrgency.chamber),
        selectinload(Bill.stages).options(
            joinedload(BillStage.chamber),
            joinedload(BillStage.committee),
        ),
        selectinload(Bill.voting_sessions),
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
        count_query = count_query.join(
            bill_topics, Bill.id == bill_topics.c.bill_id
        ).filter(bill_topics.c.topic_id == topic_id)
    if date_from:
        query = query.filter(Bill.entry_date >= date_from)
        count_query = count_query.filter(Bill.entry_date >= date_from)
    if date_to:
        query = query.filter(Bill.entry_date <= date_to)
        count_query = count_query.filter(Bill.entry_date <= date_to)
    if law_number:
        query = query.filter(Bill.law_number == law_number)
        count_query = count_query.filter(Bill.law_number == law_number)
    if current_chamber is not None:
        clause = Bill.current_chamber.has(Chamber.chamber_type == current_chamber)
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if has_urgency:
        clause = Bill.urgencies.any(BillUrgency.is_active.is_(True))
        query = query.filter(clause)
        count_query = count_query.filter(clause)
    if search:
        clause = unaccent_ilike(Bill.title, search) | unaccent_ilike(
            Bill.bulletin_number, search
        )
        query = query.filter(clause)
        count_query = count_query.filter(clause)

    order_by: tuple = (Bill.entry_date.desc(), Bill.id.desc())
    if sort == BillSort.RECENT_ACTIVITY:
        latest_activity = _latest_bill_activity_subquery(db)
        query = query.outerjoin(latest_activity, Bill.id == latest_activity.c.bill_id)
        order_by = (
            func.coalesce(latest_activity.c.last_activity_date, Bill.entry_date).desc(),
            Bill.id.desc(),
        )
    elif sort == BillSort.BULLETIN:
        order_by = (Bill.bulletin_number.asc(), Bill.id.desc())

    total = count_query.scalar() or 0
    rows = query.order_by(*order_by).offset(offset).limit(limit).all()
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
                # ``current_party`` reads from active term; eager-load terms
                # + their party + chamber to avoid N+1. See ADR-0015.
                selectinload(Legislator.terms).joinedload(LegislatorTerm.party),
                selectinload(Legislator.terms).joinedload(LegislatorTerm.chamber),
            )
        ),
        selectinload(Bill.stages).options(
            joinedload(BillStage.chamber),
            joinedload(BillStage.committee),
        ),
        selectinload(Bill.events).joinedload(BillEvent.chamber),
        selectinload(Bill.documents),
        selectinload(Bill.sponsoring_ministries),
        selectinload(Bill.urgencies).joinedload(BillUrgency.chamber),
        selectinload(Bill.voting_sessions).joinedload(VotingSession.chamber),
        selectinload(Bill.summaries),
    ]


def get_bill(db: Session, bill_id: int) -> Bill | None:
    return db.query(Bill).options(*_full_options()).filter(Bill.id == bill_id).first()


def get_bill_by_bulletin(db: Session, bulletin_number: str) -> Bill | None:
    bill = db.query(Bill).filter(Bill.bulletin_number == bulletin_number).first()
    if bill is None:
        return None
    return get_bill(db, bill.id)


def to_summary(bill: Bill):
    """Build a ``BillSummary`` from a fully-loaded ``Bill`` ORM instance.

    Combines ``Bill.__dict__`` with the denormalised fields from
    :func:`bill_to_summary_extra`. Importing the response schema here would
    create a route-layer dependency on this service module, so we keep the
    import local.
    """
    from app.schemas.proyectos import BillSummary

    extra = bill_to_summary_extra(bill)
    return BillSummary.model_validate({**bill.__dict__, **extra})


def build_author_briefs(bill: Bill) -> list[dict]:
    """Author briefs with party/chamber resolved at ``bill.entry_date``.

    Mirrors the as-of-voting-date resolution in
    :func:`app.services.voting.build_vote_details` (ADR-0015 "Vote-time
    party"): we show each author's term-time party and chamber, not today's
    active term. For a moción signed in 2018, this means the legislator
    appears with their 2018 party affiliation even if they've since switched
    parties or retired.
    """
    as_of = bill.entry_date
    briefs: list[dict] = []
    for authorship in bill.authorships:
        legislator = authorship.legislator
        briefs.append(
            {
                "id": authorship.id,
                "author_type": authorship.author_type,
                "legislator": {
                    "id": legislator.id,
                    "full_name": legislator.full_name,
                    "photo_thumbnail_url": legislator.photo_thumbnail_url,
                    "chamber_type": legislator.chamber_type_on(as_of),
                    "party": legislator.party_on(as_of),
                },
            }
        )
    return briefs


def bill_to_summary_extra(bill: Bill) -> dict:
    """Compute denormalised convenience fields not present on the ORM object."""
    active_urgency = next((u for u in bill.urgencies if u.is_active), None)
    current_stage = next((s for s in bill.stages if s.is_current), None)
    last_activity_date = max(
        (event.event_date for event in (bill.events or [])),
        default=bill.entry_date,
    )
    latest_voting = max(
        (bill.voting_sessions or []),
        key=lambda v: v.voting_date,
        default=None,
    )
    votes_summary = (
        {
            "for": latest_voting.votes_for,
            "against": latest_voting.votes_against,
            "abstain": latest_voting.abstentions,
        }
        if latest_voting is not None
        else None
    )
    return {
        "active_urgency_type": active_urgency.urgency_type if active_urgency else None,
        "current_stage_type": current_stage.stage_type if current_stage else None,
        "last_activity_date": last_activity_date,
        "votes_summary": votes_summary,
    }


def _stage_for_session(
    session: VotingSession, stages: list[BillStage]
) -> BillStage | None:
    voting_day = session.voting_date.date()
    candidates: list[BillStage] = []
    for stage in stages:
        if stage.chamber_id is not None and stage.chamber_id != session.chamber_id:
            continue
        if stage.start_date and stage.start_date > voting_day:
            continue
        if stage.end_date is not None and stage.end_date < voting_day:
            continue
        candidates.append(stage)
    if not candidates:
        return None
    candidates.sort(key=lambda s: s.start_date, reverse=True)
    return candidates[0]


def attribute_voting_sessions_to_stages(bill: Bill) -> None:
    """Compute each session's ``bill_stage_id`` from stage windows + chamber.

    Read-time attribution: the ingestor leaves the column null today, so we
    derive it here and push it onto the in-memory ORM instances without
    marking them dirty (``set_committed_value``). The DB row is unchanged.
    """
    stages = list(bill.stages or [])
    for session in bill.voting_sessions or []:
        match = _stage_for_session(session, stages)
        set_committed_value(session, "bill_stage_id", match.id if match else None)


def _stage_for_document(
    document: BillDocument, stages: list[BillStage]
) -> BillStage | None:
    if document.document_date is None:
        return None
    doc_day = document.document_date
    candidates: list[BillStage] = []
    for stage in stages:
        if stage.start_date and stage.start_date > doc_day:
            continue
        if stage.end_date is not None and stage.end_date < doc_day:
            continue
        candidates.append(stage)
    if not candidates:
        return None
    candidates.sort(key=lambda s: s.start_date, reverse=True)
    return candidates[0]


def attribute_documents_to_stages(bill: Bill) -> None:
    """Compute each document's ``bill_stage_id`` from stage windows.

    Mirrors :func:`attribute_voting_sessions_to_stages` but without the chamber
    filter — ``bill_documents`` has no chamber column, and an informe/oficio
    fired during a trámite is attributed by date window alone. Comparados (no
    ``document_date``) stay null and surface as orphans on the frontend.
    """
    stages = list(bill.stages or [])
    for document in bill.documents or []:
        match = _stage_for_document(document, stages)
        set_committed_value(document, "bill_stage_id", match.id if match else None)


_STAGE_PLAIN: dict[StageType, str] = {
    StageType.FIRST_CONSTITUTIONAL_TRAMITE: "primer trámite constitucional",
    StageType.SECOND_CONSTITUTIONAL_TRAMITE: "segundo trámite constitucional",
    StageType.THIRD_CONSTITUTIONAL_TRAMITE: "tercer trámite constitucional",
    StageType.MIXED_COMMISSION: "comisión mixta",
    StageType.CONSTITUTIONAL_TRIBUNAL: "tribunal constitucional",
    StageType.PROMULGATION: "promulgación",
    StageType.PUBLICATION: "publicación",
    StageType.OTHER: "trámite",
}

_STATUS_PLAIN: dict[BillStatus, str] = {
    BillStatus.PENDING: "En tramitación",
    BillStatus.APPROVED: "Aprobado",
    BillStatus.REJECTED: "Rechazado",
    BillStatus.ARCHIVED: "Archivado",
    BillStatus.WITHDRAWN: "Retirado",
    BillStatus.UNCONSTITUTIONAL: "Declarado inconstitucional",
    BillStatus.ENACTED: "Promulgado",
    BillStatus.PUBLISHED: "Publicado como ley",
}


def build_bill_ai_summary(bill: Bill, last_activity_date: date) -> dict:
    """Compose the layered ``BillAISummary`` payload for a bill detail response.

    Reads stored ``BillSummary`` rows for the proposal/amendments layers and
    derives the ``status_line`` deterministically from stage/committee. See ADR-0019.
    """
    proposal_row = next(
        (s for s in bill.summaries if s.kind == BillSummaryKind.PROPOSAL), None
    )
    amendments_row = next(
        (s for s in bill.summaries if s.kind == BillSummaryKind.AMENDMENTS), None
    )

    current_stage = next((s for s in bill.stages if s.is_current), None)
    current_stage_type = current_stage.stage_type if current_stage else None
    committee_name = (
        bill.current_committee.name if bill.current_committee is not None else None
    )

    parts: list[str] = [_STATUS_PLAIN.get(bill.status, bill.status.value)]
    if current_stage_type is not None:
        parts.append(f"en {_STAGE_PLAIN[current_stage_type]}")
    if committee_name:
        parts.append(f"Comisión de {committee_name}")
    parts.append(f"Última actividad: {last_activity_date.isoformat()}")
    plain_text = ". ".join(parts) + "."

    return {
        "proposal": proposal_row,
        "amendments": amendments_row,
        "status_line": {
            "plain_text": plain_text,
            "current_status": bill.status,
            "current_stage_type": current_stage_type,
            "current_committee_name": committee_name,
            "last_activity_date": last_activity_date,
        },
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
            selectinload(Bill.events),
            selectinload(Bill.topics),
            selectinload(Bill.urgencies).joinedload(BillUrgency.chamber),
            selectinload(Bill.stages).options(
                joinedload(BillStage.chamber),
                joinedload(BillStage.committee),
            ),
            selectinload(Bill.voting_sessions),
        )
        .filter(Bill.id.in_(bill_ids))
        .all()
    )
    # Re-order to match ES ranking order
    order = {bid: i for i, bid in enumerate(bill_ids)}
    return sorted(rows, key=lambda b: order.get(b.id, len(bill_ids)))

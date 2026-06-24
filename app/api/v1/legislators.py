from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import ChamberType
from app.schemas.legislators import (
    LegislatorAuthoredBillsResponse,
    LegislatorDetail,
    LegislatorsResponse,
    LegislatorSummary,
    LegislatorVotingResponse,
    LegislatorVotingSummary,
    TopicAffinityItem,
    VotingRecordItem,
)
from app.services import legislators as legislators_service
from app.services import proyectos as bills_service

router = APIRouter(tags=["Legislators"])


@router.get("", response_model=LegislatorsResponse)
def list_legislators(
    q: str | None = Query(
        None, description="Case-insensitive partial match on legislator full name"
    ),
    party: str | None = Query(
        None,
        description=(
            "Exact match on political party abbreviation. Use the sentinel "
            f"`{legislators_service.PARTY_INDEPENDENT_SENTINEL}` to filter "
            "independents (party_id IS NULL)."
        ),
    ),
    district: int | None = Query(None, ge=1, description="District number"),
    circumscription: int | None = Query(
        None, ge=1, description="Circumscription number"
    ),
    region: int | None = Query(
        None,
        ge=1,
        description=(
            "Region id. For Deputies matches District.region_id; for Senators "
            "matches Circumscription↔Region (many-to-many)."
        ),
    ),
    chamber_type: ChamberType | None = Query(None, description="Legislative chamber"),
    include_inactive: bool = Query(
        False,
        description=(
            "If false (default), only active legislators are returned. Set to "
            "true to include inactive (historical) legislators alongside active."
        ),
    ),
    offset: int = Query(legislators_service.DEFAULT_OFFSET, ge=0),
    limit: int = Query(
        legislators_service.DEFAULT_LIMIT, ge=1, le=legislators_service.MAX_LIMIT
    ),
    db: Session = Depends(get_db),
):
    total, rows = legislators_service.list_legislators(
        db=db,
        q=q,
        party=party,
        district=district,
        circumscription=circumscription,
        region=region,
        chamber_type=chamber_type,
        include_inactive=include_inactive,
        offset=offset,
        limit=limit,
    )
    return LegislatorsResponse(
        count=total,
        data=[LegislatorSummary.model_validate(row) for row in rows],
    )


@router.get("/{legislator_id}", response_model=LegislatorDetail)
def get_legislator(legislator_id: int, db: Session = Depends(get_db)):
    legislator = legislators_service.get_legislator(db=db, legislator_id=legislator_id)
    if legislator is None:
        raise HTTPException(status_code=404, detail="Legislator not found")
    return LegislatorDetail.model_validate(legislator)


@router.get("/{legislator_id}/voting", response_model=LegislatorVotingResponse)
def get_legislator_voting(
    legislator_id: int,
    limit: int = Query(legislators_service.DEFAULT_RECORD_LIMIT, ge=1, le=200),
    db: Session = Depends(get_db),
):
    legislator = legislators_service.get_legislator(db=db, legislator_id=legislator_id)
    if legislator is None:
        raise HTTPException(status_code=404, detail="Legislator not found")
    summary = legislators_service.get_legislator_voting_summary(db, legislator_id)
    record = legislators_service.get_legislator_voting_record(db, legislator_id, limit)
    topic_affinity = legislators_service.get_legislator_topic_affinity(
        db, legislator_id
    )
    return LegislatorVotingResponse(
        summary=LegislatorVotingSummary.model_validate(summary),
        record=[VotingRecordItem.model_validate(r) for r in record],
        topic_affinity=[TopicAffinityItem.model_validate(t) for t in topic_affinity],
    )


@router.get(
    "/{legislator_id}/authored-bills",
    response_model=LegislatorAuthoredBillsResponse,
)
def get_legislator_authored_bills(
    legislator_id: int,
    limit: int = Query(
        legislators_service.DEFAULT_AUTHORED_BILLS_LIMIT,
        ge=1,
        le=legislators_service.MAX_AUTHORED_BILLS_LIMIT,
    ),
    db: Session = Depends(get_db),
):
    legislator = legislators_service.get_legislator(db=db, legislator_id=legislator_id)
    if legislator is None:
        raise HTTPException(status_code=404, detail="Legislator not found")
    items, total = legislators_service.get_legislator_authored_bills(
        db, legislator_id, limit
    )
    return LegislatorAuthoredBillsResponse(
        items=[bills_service.to_summary(b) for b in items],
        total=total,
    )

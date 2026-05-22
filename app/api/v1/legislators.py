from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import ChamberType
from app.schemas.legislators import (
    LegislatorDetail,
    LegislatorsResponse,
    LegislatorSummary,
)
from app.services import legislators as legislators_service

router = APIRouter(tags=["Legislators"])


@router.get("", response_model=LegislatorsResponse)
def list_legislators(
    party: str | None = Query(
        None, description="Partial match on political party name"
    ),
    district: int | None = Query(None, ge=1, description="District number"),
    circumscription: int | None = Query(
        None, ge=1, description="Circumscription number"
    ),
    chamber_type: ChamberType | None = Query(None, description="Legislative chamber"),
    offset: int = Query(legislators_service.DEFAULT_OFFSET, ge=0),
    limit: int = Query(
        legislators_service.DEFAULT_LIMIT, ge=1, le=legislators_service.MAX_LIMIT
    ),
    db: Session = Depends(get_db),
):
    total, rows = legislators_service.list_legislators(
        db=db,
        party=party,
        district=district,
        circumscription=circumscription,
        chamber_type=chamber_type,
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

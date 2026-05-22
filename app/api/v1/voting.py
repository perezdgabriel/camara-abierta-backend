from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import ChamberType
from app.schemas.voting import (
    VotingSessionDetail,
    VotingSessionsResponse,
    VotingSessionSummary,
)
from app.services import voting as voting_service

router = APIRouter(tags=["Voting Sessions"])


@router.get("", response_model=VotingSessionsResponse)
def list_voting_sessions(
    date_from: date | None = Query(None, description="Voting date from (YYYY-MM-DD)"),
    date_to: date | None = Query(None, description="Voting date to (YYYY-MM-DD)"),
    chamber: ChamberType | None = Query(None, description="Legislative chamber"),
    bill_id: int | None = Query(None, ge=1, description="Filter by bill id"),
    offset: int = Query(voting_service.DEFAULT_OFFSET, ge=0),
    limit: int = Query(voting_service.DEFAULT_LIMIT, ge=1, le=voting_service.MAX_LIMIT),
    db: Session = Depends(get_db),
):
    total, rows = voting_service.list_voting_sessions(
        db=db,
        date_from=date_from,
        date_to=date_to,
        chamber=chamber,
        bill_id=bill_id,
        offset=offset,
        limit=limit,
    )
    return VotingSessionsResponse(
        count=total,
        data=[VotingSessionSummary.model_validate(row) for row in rows],
    )


@router.get("/{voting_session_id}", response_model=VotingSessionDetail)
def get_voting_session(voting_session_id: int, db: Session = Depends(get_db)):
    voting_session = voting_service.get_voting_session(
        db=db, voting_session_id=voting_session_id
    )
    if voting_session is None:
        raise HTTPException(status_code=404, detail="Voting session not found")
    return VotingSessionDetail.model_validate(voting_session)

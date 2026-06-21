from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import ChamberType, SignalType, VotingResult
from app.schemas.voting import (
    HighlightedResponse,
    VotingAggregates,
    VotingSessionDetail,
    VotingSessionsResponse,
    VotingSessionSummary,
)
from app.services import voting as voting_service
from app.services import voting_signals

router = APIRouter(tags=["Voting Sessions"])


# Specific routes must come before /{voting_session_id} so FastAPI doesn't
# try to parse "aggregates" or "highlighted" as a numeric id.


@router.get("/aggregates", response_model=VotingAggregates)
def get_voting_aggregates(
    window: str = Query("30d", description="Rolling window, e.g. '30d'"),
    db: Session = Depends(get_db),
):
    window_days = _parse_window(window)
    row = voting_signals.get_window_aggregate(db, window_days=window_days)
    if row is None:
        # No aggregate cached yet (e.g. fresh DB before the daily beat runs).
        # Compute on demand so the page always has something to render.
        row = voting_signals.refresh_window_aggregate(db, window_days=window_days)
    payload = dict(row.payload)
    return VotingAggregates(
        window_days=row.window_days,
        computed_at=row.updated_at,
        **payload,
    )


@router.get("/highlighted", response_model=HighlightedResponse)
def get_highlighted(
    window: str = Query("30d", description="Rolling window, e.g. '30d'"),
    db: Session = Depends(get_db),
):
    window_days = _parse_window(window)
    result = voting_signals.select_highlighted(db, window_days=window_days)
    return HighlightedResponse.model_validate(result)


@router.get("", response_model=VotingSessionsResponse)
def list_voting_sessions(
    date_from: date | None = Query(None, description="Voting date from (YYYY-MM-DD)"),
    date_to: date | None = Query(None, description="Voting date to (YYYY-MM-DD)"),
    chamber: ChamberType | None = Query(None, description="Legislative chamber"),
    bill_id: int | None = Query(None, ge=1, description="Filter by bill id"),
    signal_type: SignalType | None = Query(
        None, description="Restrict to sessions that fired this signal"
    ),
    result: VotingResult | None = Query(None, description="Filter by outcome"),
    q: str | None = Query(
        None, min_length=1, description="Free-text search on session subject"
    ),
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
        signal_type=signal_type,
        result=result,
        q=q,
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
    detail = VotingSessionDetail.model_validate(voting_session)
    detail.votes = voting_service.build_vote_details(voting_session)
    return detail


def _parse_window(window: str) -> int:
    """Parse strings like '30d' into the integer day count.

    Kept tolerant: bare integers (``"30"``) also work. Anything else is a 400.
    """
    raw = window.strip().lower()
    if raw.endswith("d"):
        raw = raw[:-1]
    if not raw.isdigit():
        raise HTTPException(
            status_code=400, detail=f"Invalid window: {window!r}; expected e.g. '30d'"
        )
    n = int(raw)
    if n <= 0 or n > 365:
        raise HTTPException(
            status_code=400, detail="window must be between 1d and 365d"
        )
    return n

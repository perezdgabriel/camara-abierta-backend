from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.diario_oficial import Norma, NormasResponse
from app.services import diario_oficial as diario_oficial_service

router = APIRouter(tags=["Diario Oficial"])


@router.get("/normas", response_model=NormasResponse)
def list_normas(
    date_from: date | None = Query(
        None, description="Filter from this date (inclusive, YYYY-MM-DD)"
    ),
    date_to: date | None = Query(
        None, description="Filter up to this date (inclusive, YYYY-MM-DD)"
    ),
    ministry: str | None = Query(
        None, description="Filter by ministry (case-insensitive partial match)"
    ),
    branch: str | None = Query(
        None, description="Filter by branch (e.g. PODER EJECUTIVO)"
    ),
    search: str | None = Query(None, description="Full-text search on the norm title"),
    offset: int = Query(diario_oficial_service.DEFAULT_OFFSET, ge=0),
    limit: int = Query(
        diario_oficial_service.DEFAULT_LIMIT, ge=1, le=diario_oficial_service.MAX_LIMIT
    ),
    db: Session = Depends(get_db),
):
    total, rows = diario_oficial_service.list_normas(
        db=db,
        date_from=date_from,
        date_to=date_to,
        ministry=ministry,
        branch=branch,
        search=search,
        offset=offset,
        limit=limit,
    )
    return NormasResponse(count=total, data=[Norma.model_validate(row) for row in rows])


@router.get("/normas/por-importancia", response_model=list[Norma])
def list_destacadas_por_importancia(
    min_score: int = Query(
        1, ge=1, le=10, description="Minimum importancia_ciudadana score"
    ),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    rows = diario_oficial_service.list_destacadas_por_importancia(
        db=db, min_score=min_score, limit=limit
    )
    return [Norma.model_validate(row) for row in rows]


@router.get("/normas/{cve}", response_model=Norma)
def get_norma_by_cve(cve: str, db: Session = Depends(get_db)):
    row = diario_oficial_service.get_norma_by_cve(db=db, cve=cve)
    if row is None:
        raise HTTPException(status_code=404, detail="Norma not found")
    return Norma.model_validate(row)


@router.get("/dates/available", response_model=list[str])
def list_available_dates(
    limit: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    return diario_oficial_service.list_available_dates(db=db, limit=limit)


@router.get("/stats/by-ministry", response_model=list[dict])
def stats_by_ministry(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
):
    return diario_oficial_service.stats_by_ministry(
        db=db, date_from=date_from, date_to=date_to
    )

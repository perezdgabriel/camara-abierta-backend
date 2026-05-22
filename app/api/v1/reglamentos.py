from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.reglamentos import (
    Reglamento,
    ReglamentoDetail,
    ReglamentosResponse,
    ReglamentoStats,
    ReglamentoTimeline,
)
from app.services import reglamentos as reglamentos_service

router = APIRouter(tags=["Reglamentos"])


@router.get("", response_model=ReglamentosResponse)
def list_reglamentos(
    categoria: str | None = Query(
        None, description="en_tramite, tramitados, retirados"
    ),
    ministerio: str | None = Query(None, description="Partial match on ministerio"),
    subsecretaria: str | None = Query(
        None, description="Partial match on subsecretaría"
    ),
    search: str | None = Query(None, description="Search in materia"),
    anio: str | None = Query(None, description="Filter by año del reglamento"),
    estado: str | None = Query(None, description="Filter by estado (partial match)"),
    date_from: date | None = Query(None, description="Fecha ingreso from (YYYY-MM-DD)"),
    date_to: date | None = Query(None, description="Fecha ingreso to (YYYY-MM-DD)"),
    reingresado: bool | None = Query(
        None, description="Filter by reingresado (retirado y luego reingresado)"
    ),
    gobierno_actual: bool | None = Query(
        None, description="Only reglamentos with etapa activity from 2026-03-11 onwards"
    ),
    offset: int = Query(reglamentos_service.DEFAULT_OFFSET, ge=0),
    limit: int = Query(
        reglamentos_service.DEFAULT_LIMIT, ge=1, le=reglamentos_service.MAX_LIMIT
    ),
    db: Session = Depends(get_db),
):
    total, rows = reglamentos_service.list_reglamentos(
        db=db,
        categoria=categoria,
        ministerio=ministerio,
        subsecretaria=subsecretaria,
        search=search,
        anio=anio,
        estado=estado,
        date_from=date_from,
        date_to=date_to,
        reingresado=reingresado,
        gobierno_actual=gobierno_actual,
        offset=offset,
        limit=limit,
    )
    return ReglamentosResponse(
        count=total, data=[Reglamento.model_validate(row) for row in rows]
    )


@router.get("/recientes", response_model=list[ReglamentoTimeline])
def reglamentos_recientes(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return reglamentos_service.reglamentos_recientes(db=db, limit=limit)


@router.get("/stats/por-ministerio", response_model=list[ReglamentoStats])
def reglamentos_stats_por_ministerio(
    categoria: str | None = Query(
        None, description="en_tramite, tramitados, retirados"
    ),
    db: Session = Depends(get_db),
):
    return reglamentos_service.reglamentos_stats_por_ministerio(
        db=db, categoria=categoria
    )


@router.get("/stats/por-categoria")
def reglamentos_stats_por_categoria(db: Session = Depends(get_db)):
    return reglamentos_service.reglamentos_stats_por_categoria(db=db)


@router.get("/stats/tiempo-tramitacion")
def reglamentos_tiempo_tramitacion(
    categoria: str | None = Query("tramitados"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return reglamentos_service.reglamentos_tiempo_tramitacion(
        db=db, categoria=categoria, limit=limit
    )


@router.get("/stats/mas-etapas")
def reglamentos_mas_etapas(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return reglamentos_service.reglamentos_mas_etapas(db=db, limit=limit)


@router.get("/{reglamento_id}", response_model=ReglamentoDetail)
def get_reglamento(reglamento_id: int, db: Session = Depends(get_db)):
    row = reglamentos_service.get_reglamento(db=db, reglamento_id=reglamento_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Reglamento not found")
    return ReglamentoDetail.model_validate(row)

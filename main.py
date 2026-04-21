from datetime import date
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Depends
from sqlalchemy import func, desc, case
from sqlalchemy.orm import Session, joinedload

from db import get_db, NormaGeneralDB, ReglamentoDB, ReglamentoEtapaDB
from schemas import (
    Norma, NormasResponse,
    Reglamento, ReglamentoDetail, ReglamentosResponse,
    ReglamentoStats, ReglamentoTimeline
)

INICIO_GOBIERNO_ACTUAL = date(2026, 3, 11)

app = FastAPI(
    title="Diario Oficial de Chile — API",
    description="Public API to query normas generales and CGR reglamentos.",
    version="0.0.1",
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


@app.get("/normas", response_model=NormasResponse)
def list_normas(
    date_from: Optional[date] = Query(None, description="Filter from this date (inclusive, YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Filter up to this date (inclusive, YYYY-MM-DD)"),
    ministry: Optional[str] = Query(None, description="Filter by ministry (case-insensitive partial match)"),
    branch: Optional[str] = Query(None, description="Filter by branch (e.g. PODER EJECUTIVO)"),
    search: Optional[str] = Query(None, description="Full-text search on the norm title"),
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_db),
):
    """List normas generales with optional filters."""
    query = db.query(NormaGeneralDB)
    count_query = db.query(func.count(NormaGeneralDB.id))

    if date_from:
        query = query.filter(NormaGeneralDB.date >= date_from)
        count_query = count_query.filter(NormaGeneralDB.date >= date_from)
    if date_to:
        query = query.filter(NormaGeneralDB.date <= date_to)
        count_query = count_query.filter(NormaGeneralDB.date <= date_to)
    if ministry:
        query = query.filter(NormaGeneralDB.ministry.ilike(f"%{ministry}%"))
        count_query = count_query.filter(NormaGeneralDB.ministry.ilike(f"%{ministry}%"))
    if branch:
        query = query.filter(NormaGeneralDB.branch.ilike(f"%{branch}%"))
        count_query = count_query.filter(NormaGeneralDB.branch.ilike(f"%{branch}%"))
    if search:
        query = query.filter(NormaGeneralDB.title.ilike(f"%{search}%"))
        count_query = count_query.filter(NormaGeneralDB.title.ilike(f"%{search}%"))

    total = count_query.scalar()
    rows = query.order_by(NormaGeneralDB.date.desc()).offset(offset).limit(limit).all()

    print('rows:', rows)

    return NormasResponse(count=total, data=rows)


@app.get("/normas/por-importancia", response_model=list[Norma], tags=["Normas"])
def list_destacadas_por_importancia(
    min_score: int = Query(1, ge=1, le=10, description="Minimum importancia_ciudadana score"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Get AI-highlighted norms ranked by importancia_ciudadana (highest first)."""
    q = (
        db.query(NormaGeneralDB)
        .filter(NormaGeneralDB.importancia_ciudadana >= min_score)
        .order_by(NormaGeneralDB.importancia_ciudadana.desc(), NormaGeneralDB.date.desc())
        .limit(limit)
    )
    return q.all()


@app.get("/normas/{cve}", response_model=Norma)
def get_norma_by_cve(cve: str, db: Session = Depends(get_db)):
    """Get a single norma by its CVE code."""
    row = db.query(NormaGeneralDB).filter(NormaGeneralDB.cve == cve).first()
    if not row:
        raise HTTPException(status_code=404, detail="Norma not found")
    return row


@app.get("/normas/dates/available", response_model=list[str])
def list_available_dates(
    limit: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    """List distinct dates that have published normas, most recent first."""
    rows = (
        db.query(NormaGeneralDB.date)
        .distinct()
        .order_by(NormaGeneralDB.date.desc())
        .limit(limit)
        .all()
    )
    return [row.date.isoformat() for row in rows]


@app.get("/normas/stats/by-ministry", response_model=list[dict])
def stats_by_ministry(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Get count of normas grouped by ministry for a date range."""
    query = db.query(
        NormaGeneralDB.ministry,
        func.count(NormaGeneralDB.id).label("count"),
    )

    if date_from:
        query = query.filter(NormaGeneralDB.date >= date_from)
    if date_to:
        query = query.filter(NormaGeneralDB.date <= date_to)

    rows = query.group_by(NormaGeneralDB.ministry).order_by(func.count(NormaGeneralDB.id).desc()).all()

    return [{"ministry": row.ministry or "Unknown", "count": row.count} for row in rows]


# ═══════════════════════════════════════════════════════════════════════════════
#  REGLAMENTOS  — CGR Tramitación de Reglamentos
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/reglamentos", response_model=ReglamentosResponse, tags=["Reglamentos"])
def list_reglamentos(
    categoria: Optional[str] = Query(None, description="en_tramite, tramitados, retirados"),
    ministerio: Optional[str] = Query(None, description="Partial match on ministerio"),
    subsecretaria: Optional[str] = Query(None, description="Partial match on subsecretaría"),
    search: Optional[str] = Query(None, description="Search in materia"),
    anio: Optional[str] = Query(None, description="Filter by año del reglamento"),
    estado: Optional[str] = Query(None, description="Filter by estado (partial match)"),
    date_from: Optional[date] = Query(None, description="Fecha ingreso from (YYYY-MM-DD)"),
    date_to: Optional[date] = Query(None, description="Fecha ingreso to (YYYY-MM-DD)"),
    reingresado: Optional[bool] = Query(None, description="Filter by reingresado (retirado y luego reingresado)"),
    gobierno_actual: Optional[bool] = Query(None, description="Only reglamentos with etapa activity from 2026-03-11 onwards"),
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_db),
):
    """List reglamentos with filters."""
    q = db.query(ReglamentoDB)
    cq = db.query(func.count(ReglamentoDB.id))

    filters = []
    if categoria:
        filters.append(ReglamentoDB.categoria == categoria)
    if ministerio:
        filters.append(ReglamentoDB.ministerio.ilike(f"%{ministerio}%"))
    if subsecretaria:
        filters.append(ReglamentoDB.subsecretaria.ilike(f"%{subsecretaria}%"))
    if search:
        filters.append(ReglamentoDB.materia.ilike(f"%{search}%"))
    if anio:
        filters.append(ReglamentoDB.anio == anio)
    if estado:
        filters.append(ReglamentoDB.estado.ilike(f"%{estado}%"))
    if reingresado is not None:
        filters.append(ReglamentoDB.reingresado == reingresado)
    if date_from:
        filters.append(ReglamentoDB.fecha_ingreso >= date_from)
    if date_to:
        filters.append(ReglamentoDB.fecha_ingreso <= date_to)
    if gobierno_actual is True:
        filters.append(
            ReglamentoDB.id.in_(
                db.query(ReglamentoEtapaDB.reglamento_id)
                .filter(ReglamentoEtapaDB.fecha >= INICIO_GOBIERNO_ACTUAL)
                .distinct()
            )
        )
    elif gobierno_actual is False:
        filters.append(
            ~ReglamentoDB.id.in_(
                db.query(ReglamentoEtapaDB.reglamento_id)
                .filter(ReglamentoEtapaDB.fecha >= INICIO_GOBIERNO_ACTUAL)
                .distinct()
            )
        )

    for f in filters:
        q = q.filter(f)
        cq = cq.filter(f)

    total = cq.scalar()
    rows = q.order_by(ReglamentoDB.fecha_ingreso.desc()).offset(offset).limit(limit).all()
    return ReglamentosResponse(count=total, data=rows)


@app.get("/reglamentos/recientes", response_model=list[ReglamentoTimeline], tags=["Reglamentos"])
def reglamentos_recientes(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Reglamentos with the most recent activity (last etapa date).
    Useful to know which regulations had movement most recently."""
    ultima = (
        db.query(
            ReglamentoEtapaDB.reglamento_id,
            func.max(ReglamentoEtapaDB.fecha).label("ultima_fecha"),
            func.count(ReglamentoEtapaDB.id).label("total_etapas"),
        )
        .group_by(ReglamentoEtapaDB.reglamento_id)
        .subquery()
    )

    rows = (
        db.query(
            ReglamentoDB,
            ultima.c.ultima_fecha,
            ultima.c.total_etapas,
        )
        .join(ultima, ReglamentoDB.id == ultima.c.reglamento_id)
        .order_by(desc(ultima.c.ultima_fecha))
        .limit(limit)
        .all()
    )

    # Get the last action per reglamento
    result = []
    for reg, ultima_fecha, total_etapas in rows:
        last_etapa = (
            db.query(ReglamentoEtapaDB)
            .filter(
                ReglamentoEtapaDB.reglamento_id == reg.id,
                ReglamentoEtapaDB.fecha == ultima_fecha,
            )
            .order_by(ReglamentoEtapaDB.etapa.desc())
            .first()
        )
        result.append(ReglamentoTimeline(
            reglamento_id=reg.id,
            numero=reg.numero,
            anio=reg.anio,
            ministerio=reg.ministerio,
            materia=reg.materia,
            categoria=reg.categoria,
            estado=reg.estado,
            ultima_etapa_fecha=ultima_fecha,
            ultima_etapa_accion=last_etapa.accion if last_etapa else None,
            total_etapas=total_etapas,
        ))

    return result


@app.get("/reglamentos/stats/por-ministerio", response_model=list[ReglamentoStats], tags=["Reglamentos"])
def reglamentos_stats_por_ministerio(
    categoria: Optional[str] = Query(None, description="en_tramite, tramitados, retirados"),
    db: Session = Depends(get_db),
):
    """Count of reglamentos per ministerio, optionally filtered by categoría."""
    q = db.query(
        ReglamentoDB.ministerio,
        func.count(ReglamentoDB.id).label("count"),
    )
    if categoria:
        q = q.filter(ReglamentoDB.categoria == categoria)

    rows = q.group_by(ReglamentoDB.ministerio).order_by(desc("count")).all()
    return [ReglamentoStats(ministerio=r.ministerio, count=r.count) for r in rows]


@app.get("/reglamentos/stats/por-categoria", tags=["Reglamentos"])
def reglamentos_stats_por_categoria(db: Session = Depends(get_db)):
    """Count of reglamentos per categoría (en_tramite, tramitados, retirados)."""
    rows = (
        db.query(
            ReglamentoDB.categoria,
            func.count(ReglamentoDB.id).label("count"),
        )
        .group_by(ReglamentoDB.categoria)
        .order_by(desc("count"))
        .all()
    )
    return [{"categoria": r.categoria, "count": r.count} for r in rows]


@app.get("/reglamentos/stats/tiempo-tramitacion", tags=["Reglamentos"])
def reglamentos_tiempo_tramitacion(
    categoria: Optional[str] = Query("tramitados"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Reglamentos ranked by how long they took (difference between first and last etapa).
    Defaults to tramitados category. Shows which regulations took the longest to process."""
    primera = (
        db.query(
            ReglamentoEtapaDB.reglamento_id,
            func.min(ReglamentoEtapaDB.fecha).label("primera_fecha"),
            func.max(ReglamentoEtapaDB.fecha).label("ultima_fecha"),
            func.count(ReglamentoEtapaDB.id).label("total_etapas"),
        )
        .group_by(ReglamentoEtapaDB.reglamento_id)
        .subquery()
    )

    q = (
        db.query(
            ReglamentoDB,
            primera.c.primera_fecha,
            primera.c.ultima_fecha,
            primera.c.total_etapas,
            (func.extract("epoch", primera.c.ultima_fecha) - func.extract("epoch", primera.c.primera_fecha)).label("duracion_seg"),
        )
        .join(primera, ReglamentoDB.id == primera.c.reglamento_id)
    )

    if categoria:
        q = q.filter(ReglamentoDB.categoria == categoria)

    rows = q.order_by(desc("duracion_seg")).limit(limit).all()

    return [
        {
            "id": reg.id,
            "numero": reg.numero,
            "anio": reg.anio,
            "ministerio": reg.ministerio,
            "materia": reg.materia,
            "estado": reg.estado,
            "categoria": reg.categoria,
            "primera_etapa": str(primera_fecha) if primera_fecha else None,
            "ultima_etapa": str(ultima_fecha) if ultima_fecha else None,
            "dias_tramitacion": (ultima_fecha - primera_fecha).days if primera_fecha and ultima_fecha else None,
            "total_etapas": total_etapas,
        }
        for reg, primera_fecha, ultima_fecha, total_etapas, _ in rows
    ]


@app.get("/reglamentos/stats/mas-etapas", tags=["Reglamentos"])
def reglamentos_mas_etapas(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Reglamentos with the most stages/etapas. More stages often indicate
    complex reviews with multiple back-and-forth between sectors."""
    sub = (
        db.query(
            ReglamentoEtapaDB.reglamento_id,
            func.count(ReglamentoEtapaDB.id).label("total_etapas"),
        )
        .group_by(ReglamentoEtapaDB.reglamento_id)
        .subquery()
    )

    rows = (
        db.query(ReglamentoDB, sub.c.total_etapas)
        .join(sub, ReglamentoDB.id == sub.c.reglamento_id)
        .order_by(desc(sub.c.total_etapas))
        .limit(limit)
        .all()
    )

    return [
        {
            "id": reg.id,
            "numero": reg.numero,
            "anio": reg.anio,
            "ministerio": reg.ministerio,
            "materia": reg.materia,
            "estado": reg.estado,
            "categoria": reg.categoria,
            "total_etapas": total_etapas,
        }
        for reg, total_etapas in rows
    ]


@app.get("/reglamentos/{reglamento_id}", response_model=ReglamentoDetail, tags=["Reglamentos"])
def get_reglamento(reglamento_id: int, db: Session = Depends(get_db)):
    """Get a single reglamento with all its etapas/stages."""
    row = (
        db.query(ReglamentoDB)
        .options(joinedload(ReglamentoDB.etapas))
        .filter(ReglamentoDB.id == reglamento_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Reglamento not found")

    # Add gobierno_actual flag to each etapa
    for etapa in row.etapas:
        etapa.gobierno_actual = etapa.fecha is not None and etapa.fecha >= INICIO_GOBIERNO_ACTUAL

    return row



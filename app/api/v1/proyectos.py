from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import BillOrigin, BillStatus, BillType, ChamberType
from app.schemas.proyectos import (
    BillDetail,
    BillsResponse,
    BillSummary,
    Document,
    Stage,
    VotingResult,
)
from app.services import proyectos as svc

router = APIRouter(tags=["Bills"])


def _to_summary(bill) -> BillSummary:
    return svc.to_summary(bill)


def _to_detail(bill) -> BillDetail:
    extra = svc.bill_to_summary_extra(bill)
    svc.attribute_voting_sessions_to_stages(bill)
    svc.attribute_documents_to_stages(bill)
    # Authors are pre-resolved at bill.entry_date so each author's party/
    # chamber reflects the term they held when the bill was filed, not
    # today's active term. Mirrors voting's as-of-voting-date pattern.
    authors = svc.build_author_briefs(bill)
    ai_summary = svc.build_bill_ai_summary(bill, extra["last_activity_date"])
    return BillDetail.model_validate(
        {**bill.__dict__, **extra, "authors": authors, "ai_summary": ai_summary}
    )


@router.get("", response_model=BillsResponse)
def list_bills(
    db: Session = Depends(get_db),
    status: BillStatus | None = Query(None, description="Estado canónico del proyecto"),
    bill_type: BillType | None = Query(
        None, alias="tipo", description="Tipo de proyecto"
    ),
    origin: BillOrigin | None = Query(None, description="Origen canónico del proyecto"),
    topic_id: int | None = Query(None, alias="tema_id", description="Filtrar por tema"),
    current_chamber: ChamberType | None = Query(
        None, alias="camara", description="Cámara actual del proyecto"
    ),
    has_urgency: bool | None = Query(
        None, alias="con_urgencia", description="Solo proyectos con urgencia activa"
    ),
    search: str | None = Query(
        None, alias="buscar", description="Búsqueda en título o número de boletín"
    ),
    date_from: date | None = Query(
        None, alias="desde", description="Fecha de ingreso desde (YYYY-MM-DD)"
    ),
    date_to: date | None = Query(
        None, alias="hasta", description="Fecha de ingreso hasta (YYYY-MM-DD)"
    ),
    law_number: str | None = Query(
        None, alias="ley", description="Número de ley (solo leyes aprobadas)"
    ),
    sort: svc.BillSort = Query(
        svc.BillSort.RECENT_ACTIVITY,
        description="Orden del listado de proyectos",
    ),
    offset: int = Query(svc.DEFAULT_OFFSET, ge=0),
    limit: int = Query(svc.DEFAULT_LIMIT, ge=1, le=svc.MAX_LIMIT),
):
    total, bills = svc.list_bills(
        db,
        status=status,
        bill_type=bill_type,
        origin=origin,
        topic_id=topic_id,
        current_chamber=current_chamber,
        has_urgency=has_urgency,
        search=search,
        date_from=date_from,
        date_to=date_to,
        law_number=law_number,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    return BillsResponse(count=total, data=[_to_summary(b) for b in bills])


@router.get("/{bill_id}", response_model=BillDetail)
def get_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = svc.get_bill(db, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return _to_detail(bill)


@router.get("/{bill_id}/etapas", response_model=list[Stage])
def get_bill_stages(bill_id: int, db: Session = Depends(get_db)):
    bill = svc.get_bill(db, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    stages = sorted(bill.stages, key=lambda s: s.start_date)
    return [Stage.model_validate(s) for s in stages]


@router.get("/{bill_id}/votaciones", response_model=list[VotingResult])
def get_bill_voting(bill_id: int, db: Session = Depends(get_db)):
    bill = svc.get_bill(db, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    svc.attribute_voting_sessions_to_stages(bill)
    sessions = sorted(bill.voting_sessions, key=lambda v: v.voting_date)
    return [VotingResult.model_validate(v) for v in sessions]


@router.get("/{bill_id}/documentos", response_model=list[Document])
def get_bill_documents(bill_id: int, db: Session = Depends(get_db)):
    bill = svc.get_bill(db, bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    svc.attribute_documents_to_stages(bill)
    docs = sorted(
        bill.documents, key=lambda d: d.document_date or date.min, reverse=True
    )
    return [Document.model_validate(d) for d in docs]

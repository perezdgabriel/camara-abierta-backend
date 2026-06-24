from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import CalendarEventKind, ChamberType
from app.schemas.calendar import CalendarEventOut, CalendarEventsResponse
from app.services import calendar as svc

router = APIRouter(tags=["Calendar"])


@router.get("", response_model=CalendarEventsResponse)
def list_calendar_events(
    db: Session = Depends(get_db),
    start_date: date | None = Query(
        None, alias="desde", description="Inicio del rango (YYYY-MM-DD, inclusive)"
    ),
    end_date: date | None = Query(
        None, alias="hasta", description="Fin del rango (YYYY-MM-DD, inclusive)"
    ),
    kinds: list[CalendarEventKind] | None = Query(
        None, alias="tipo", description="Tipos de evento a incluir"
    ),
    chamber_type: ChamberType | None = Query(
        None, alias="camara", description="Filtrar por cámara"
    ),
    offset: int = Query(svc.DEFAULT_OFFSET, ge=0),
    limit: int = Query(svc.DEFAULT_LIMIT, ge=1, le=svc.MAX_LIMIT),
):
    total, events = svc.list_events(
        db,
        start_date=start_date,
        end_date=end_date,
        kinds=kinds,
        chamber_type=chamber_type,
        offset=offset,
        limit=limit,
    )
    return CalendarEventsResponse(
        count=total,
        data=[CalendarEventOut.model_validate(e) for e in events],
    )

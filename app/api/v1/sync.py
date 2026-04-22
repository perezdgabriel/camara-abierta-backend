from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.diario_oficial import NormasSyncResponse
from app.schemas.reglamentos import ReglamentosSyncResponse
from app.services import sync as sync_service

router = APIRouter(tags=["Sync"])


@router.get("/normas", response_model=NormasSyncResponse)
def sync_normas(
    since_version: int = Query(0, ge=0, description="Último sync_version conocido por el cliente"),
    limit: int = Query(sync_service.DEFAULT_LIMIT, ge=1, le=sync_service.MAX_LIMIT),
    db: Session = Depends(get_db),
):
    """
    Delta sync de normas del Diario Oficial.

    Retorna todas las normas cuyo `sync_version` sea mayor a `since_version`,
    ordenadas ascendentemente. El cliente debe guardar el `meta.current_version`
    y usarlo como `since_version` en la siguiente llamada.

    Los registros eliminados (soft-delete) aparecen en `deleted_ids`.
    """
    items, deleted_ids, meta = sync_service.delta_sync_normas(
        db=db,
        since_version=since_version,
        limit=limit,
    )
    return NormasSyncResponse(items=items, deleted_ids=deleted_ids, meta=meta)


@router.get("/reglamentos", response_model=ReglamentosSyncResponse)
def sync_reglamentos(
    since_version: int = Query(0, ge=0, description="Último sync_version conocido por el cliente"),
    limit: int = Query(sync_service.DEFAULT_LIMIT, ge=1, le=sync_service.MAX_LIMIT),
    db: Session = Depends(get_db),
):
    """
    Delta sync de reglamentos CGR.

    Retorna reglamentos con sus etapas. El campo `gobierno_actual` en cada etapa
    indica si la acción ocurrió desde el inicio del gobierno actual (2026-03-11).
    """
    items, deleted_ids, meta = sync_service.delta_sync_reglamentos(
        db=db,
        since_version=since_version,
        limit=limit,
    )
    return ReglamentosSyncResponse(items=items, deleted_ids=deleted_ids, meta=meta)


from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.diario_oficial import OfficialGazetteNorm, Regulation
from app.schemas.common import SyncMeta

DEFAULT_LIMIT = 500
MAX_LIMIT = 1000


def delta_sync_normas(
    db: Session,
    since_version: int,
    limit: int,
) -> tuple[list[OfficialGazetteNorm], list[int], SyncMeta]:
    """
    Returns (active_items, deleted_ids, meta).

    Fetches all normas with sync_version > since_version ordered ascending,
    capped at `limit`. Active rows go into items; soft-deleted rows contribute
    their id to deleted_ids.
    """
    batch_plus = (
        db.query(OfficialGazetteNorm)
        .filter(OfficialGazetteNorm.sync_version > since_version)
        .order_by(OfficialGazetteNorm.sync_version.asc())
        .limit(limit + 1)
        .all()
    )

    has_more = len(batch_plus) > limit
    batch = batch_plus[:limit]

    items = [r for r in batch if r.deleted_at is None]
    deleted_ids = [r.id for r in batch if r.deleted_at is not None]
    current_version = batch[-1].sync_version if batch else since_version

    return (
        items,
        deleted_ids,
        SyncMeta(
            current_version=current_version,
            has_more=has_more,
            count=len(items),
        ),
    )


def delta_sync_reglamentos(
    db: Session,
    since_version: int,
    limit: int,
) -> tuple[list[Regulation], list[int], SyncMeta]:
    """
    Returns (active_items, deleted_ids, meta).

    Active reglamentos include their etapas eagerly loaded with
    gobierno_actual computed per etapa.
    """
    batch_plus = (
        db.query(Regulation)
        .options(joinedload(Regulation.etapas))
        .filter(Regulation.sync_version > since_version)
        .order_by(Regulation.sync_version.asc())
        .limit(limit + 1)
        .all()
    )

    has_more = len(batch_plus) > limit
    batch = batch_plus[:limit]

    active = [r for r in batch if r.deleted_at is None]
    deleted_ids = [r.id for r in batch if r.deleted_at is not None]

    # Annotate gobierno_actual on each etapa (not a DB column, computed at read time)
    for reg in active:
        for etapa in reg.etapas:
            etapa.gobierno_actual = (
                etapa.fecha is not None
                and etapa.fecha >= settings.gobierno_actual_inicio
            )

    current_version = batch[-1].sync_version if batch else since_version

    return (
        active,
        deleted_ids,
        SyncMeta(
            current_version=current_version,
            has_more=has_more,
            count=len(active),
        ),
    )

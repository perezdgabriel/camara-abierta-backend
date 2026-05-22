from datetime import date

from sqlalchemy import desc, func
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.diario_oficial import Regulation, RegulationStage
from app.schemas.reglamentos import ReglamentoStats, ReglamentoTimeline

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def _gobierno_actual_subquery(db: Session):
    return (
        db.query(RegulationStage.reglamento_id)
        .filter(RegulationStage.fecha >= settings.gobierno_actual_inicio)
        .distinct()
    )


def list_reglamentos(
    db: Session,
    categoria: str | None,
    ministerio: str | None,
    subsecretaria: str | None,
    search: str | None,
    anio: str | None,
    estado: str | None,
    date_from: date | None,
    date_to: date | None,
    reingresado: bool | None,
    gobierno_actual: bool | None,
    offset: int,
    limit: int,
) -> tuple[int, list[Regulation]]:
    query = db.query(Regulation)
    count_query = db.query(func.count(Regulation.id))

    filters = []
    if categoria:
        filters.append(Regulation.categoria == categoria)
    if ministerio:
        filters.append(Regulation.ministerio.ilike(f"%{ministerio}%"))
    if subsecretaria:
        filters.append(Regulation.subsecretaria.ilike(f"%{subsecretaria}%"))
    if search:
        filters.append(Regulation.materia.ilike(f"%{search}%"))
    if anio:
        filters.append(Regulation.anio == anio)
    if estado:
        filters.append(Regulation.estado.ilike(f"%{estado}%"))
    if reingresado is not None:
        filters.append(Regulation.reingresado == reingresado)
    if date_from:
        filters.append(Regulation.fecha_ingreso >= date_from)
    if date_to:
        filters.append(Regulation.fecha_ingreso <= date_to)
    if gobierno_actual is True:
        filters.append(Regulation.id.in_(_gobierno_actual_subquery(db)))
    elif gobierno_actual is False:
        filters.append(~Regulation.id.in_(_gobierno_actual_subquery(db)))

    for predicate in filters:
        query = query.filter(predicate)
        count_query = count_query.filter(predicate)

    total = count_query.scalar() or 0
    rows = (
        query.order_by(Regulation.fecha_ingreso.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, rows


def reglamentos_recientes(db: Session, limit: int) -> list[ReglamentoTimeline]:
    ultima = (
        db.query(
            RegulationStage.reglamento_id,
            func.max(RegulationStage.fecha).label("ultima_fecha"),
            func.count(RegulationStage.id).label("total_etapas"),
        )
        .group_by(RegulationStage.reglamento_id)
        .subquery()
    )

    ultima_accion = db.query(
        RegulationStage.reglamento_id.label("reglamento_id"),
        RegulationStage.accion.label("ultima_etapa_accion"),
        func.row_number()
        .over(
            partition_by=RegulationStage.reglamento_id,
            order_by=(
                RegulationStage.fecha.desc(),
                RegulationStage.etapa.desc(),
                RegulationStage.id.desc(),
            ),
        )
        .label("row_number"),
    ).subquery()

    rows = (
        db.query(
            Regulation,
            ultima.c.ultima_fecha,
            ultima.c.total_etapas,
            ultima_accion.c.ultima_etapa_accion,
        )
        .join(ultima, Regulation.id == ultima.c.reglamento_id)
        .outerjoin(
            ultima_accion,
            (Regulation.id == ultima_accion.c.reglamento_id)
            & (ultima_accion.c.row_number == 1),
        )
        .order_by(desc(ultima.c.ultima_fecha))
        .limit(limit)
        .all()
    )

    return [
        ReglamentoTimeline(
            reglamento_id=reg.id,
            numero=reg.numero,
            anio=reg.anio,
            ministerio=reg.ministerio,
            materia=reg.materia,
            categoria=reg.categoria,
            estado=reg.estado,
            ultima_etapa_fecha=ultima_fecha,
            ultima_etapa_accion=ultima_etapa_accion,
            total_etapas=total_etapas,
        )
        for reg, ultima_fecha, total_etapas, ultima_etapa_accion in rows
    ]


def reglamentos_stats_por_ministerio(
    db: Session, categoria: str | None
) -> list[ReglamentoStats]:
    query = db.query(
        Regulation.ministerio,
        func.count(Regulation.id).label("count"),
    )
    if categoria:
        query = query.filter(Regulation.categoria == categoria)

    rows = query.group_by(Regulation.ministerio).order_by(desc("count")).all()
    return [
        ReglamentoStats(ministerio=ministerio, count=count)
        for ministerio, count in rows
    ]


def reglamentos_stats_por_categoria(db: Session) -> list[dict[str, int | str]]:
    rows = (
        db.query(
            Regulation.categoria,
            func.count(Regulation.id).label("count"),
        )
        .group_by(Regulation.categoria)
        .order_by(desc("count"))
        .all()
    )
    return [
        {"categoria": categoria, "count": count}
        for categoria, count in rows
    ]


def reglamentos_tiempo_tramitacion(
    db: Session, categoria: str | None, limit: int
) -> list[dict[str, int | str | None]]:
    primera = (
        db.query(
            RegulationStage.reglamento_id,
            func.min(RegulationStage.fecha).label("primera_fecha"),
            func.max(RegulationStage.fecha).label("ultima_fecha"),
            func.count(RegulationStage.id).label("total_etapas"),
        )
        .group_by(RegulationStage.reglamento_id)
        .subquery()
    )

    query = db.query(
        Regulation,
        primera.c.primera_fecha,
        primera.c.ultima_fecha,
        primera.c.total_etapas,
        (
            func.extract("epoch", primera.c.ultima_fecha)
            - func.extract("epoch", primera.c.primera_fecha)
        ).label("duracion_seg"),
    ).join(primera, Regulation.id == primera.c.reglamento_id)

    if categoria:
        query = query.filter(Regulation.categoria == categoria)

    rows = query.order_by(desc("duracion_seg")).limit(limit).all()
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
            "dias_tramitacion": (ultima_fecha - primera_fecha).days
            if primera_fecha and ultima_fecha
            else None,
            "total_etapas": total_etapas,
        }
        for reg, primera_fecha, ultima_fecha, total_etapas, _ in rows
    ]


def reglamentos_mas_etapas(
    db: Session, limit: int
) -> list[dict[str, int | str | None]]:
    subquery = (
        db.query(
            RegulationStage.reglamento_id,
            func.count(RegulationStage.id).label("total_etapas"),
        )
        .group_by(RegulationStage.reglamento_id)
        .subquery()
    )

    rows = (
        db.query(Regulation, subquery.c.total_etapas)
        .join(subquery, Regulation.id == subquery.c.reglamento_id)
        .order_by(desc(subquery.c.total_etapas))
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


def get_reglamento(db: Session, reglamento_id: int) -> Regulation | None:
    row = (
        db.query(Regulation)
        .options(joinedload(Regulation.etapas))
        .filter(Regulation.id == reglamento_id)
        .first()
    )
    if row is None:
        return None

    for etapa in row.etapas:
        setattr(
            etapa,
            "gobierno_actual",
            etapa.fecha is not None and etapa.fecha >= settings.gobierno_actual_inicio,
        )
    return row

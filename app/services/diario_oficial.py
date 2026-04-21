from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.diario_oficial import NormaGeneral

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def list_normas(
    db: Session,
    date_from: date | None,
    date_to: date | None,
    ministry: str | None,
    branch: str | None,
    search: str | None,
    offset: int,
    limit: int,
) -> tuple[int, list[NormaGeneral]]:
    query = db.query(NormaGeneral)
    count_query = db.query(func.count(NormaGeneral.id))

    if date_from:
        query = query.filter(NormaGeneral.date >= date_from)
        count_query = count_query.filter(NormaGeneral.date >= date_from)
    if date_to:
        query = query.filter(NormaGeneral.date <= date_to)
        count_query = count_query.filter(NormaGeneral.date <= date_to)
    if ministry:
        query = query.filter(NormaGeneral.ministry.ilike(f"%{ministry}%"))
        count_query = count_query.filter(NormaGeneral.ministry.ilike(f"%{ministry}%"))
    if branch:
        query = query.filter(NormaGeneral.branch.ilike(f"%{branch}%"))
        count_query = count_query.filter(NormaGeneral.branch.ilike(f"%{branch}%"))
    if search:
        query = query.filter(NormaGeneral.title.ilike(f"%{search}%"))
        count_query = count_query.filter(NormaGeneral.title.ilike(f"%{search}%"))

    total = count_query.scalar() or 0
    rows = query.order_by(NormaGeneral.date.desc()).offset(offset).limit(limit).all()
    return total, rows


def list_destacadas_por_importancia(db: Session, min_score: int, limit: int) -> list[NormaGeneral]:
    return (
        db.query(NormaGeneral)
        .filter(NormaGeneral.importancia_ciudadana >= min_score)
        .order_by(NormaGeneral.importancia_ciudadana.desc(), NormaGeneral.date.desc())
        .limit(limit)
        .all()
    )


def get_norma_by_cve(db: Session, cve: str) -> NormaGeneral | None:
    return db.query(NormaGeneral).filter(NormaGeneral.cve == cve).first()


def list_available_dates(db: Session, limit: int) -> list[str]:
    rows = (
        db.query(NormaGeneral.date)
        .distinct()
        .order_by(NormaGeneral.date.desc())
        .limit(limit)
        .all()
    )
    return [row.date.isoformat() for row in rows]


def stats_by_ministry(db: Session, date_from: date | None, date_to: date | None) -> list[dict[str, int | str]]:
    query = db.query(
        NormaGeneral.ministry,
        func.count(NormaGeneral.id).label("count"),
    )

    if date_from:
        query = query.filter(NormaGeneral.date >= date_from)
    if date_to:
        query = query.filter(NormaGeneral.date <= date_to)

    rows = query.group_by(NormaGeneral.ministry).order_by(func.count(NormaGeneral.id).desc()).all()
    return [{"ministry": row.ministry or "Unknown", "count": row.count} for row in rows]

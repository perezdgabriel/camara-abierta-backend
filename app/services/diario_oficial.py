from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.diario_oficial import OfficialGazetteNorm

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
) -> tuple[int, list[OfficialGazetteNorm]]:
    query = db.query(OfficialGazetteNorm)
    count_query = db.query(func.count(OfficialGazetteNorm.id))

    if date_from:
        query = query.filter(OfficialGazetteNorm.date >= date_from)
        count_query = count_query.filter(OfficialGazetteNorm.date >= date_from)
    if date_to:
        query = query.filter(OfficialGazetteNorm.date <= date_to)
        count_query = count_query.filter(OfficialGazetteNorm.date <= date_to)
    if ministry:
        query = query.filter(OfficialGazetteNorm.ministry.ilike(f"%{ministry}%"))
        count_query = count_query.filter(
            OfficialGazetteNorm.ministry.ilike(f"%{ministry}%")
        )
    if branch:
        query = query.filter(OfficialGazetteNorm.branch.ilike(f"%{branch}%"))
        count_query = count_query.filter(
            OfficialGazetteNorm.branch.ilike(f"%{branch}%")
        )
    if search:
        query = query.filter(OfficialGazetteNorm.title.ilike(f"%{search}%"))
        count_query = count_query.filter(OfficialGazetteNorm.title.ilike(f"%{search}%"))

    total = count_query.scalar() or 0
    rows = (
        query.order_by(OfficialGazetteNorm.date.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return total, rows


def list_destacadas_por_importancia(
    db: Session, min_score: int, limit: int
) -> list[OfficialGazetteNorm]:
    return (
        db.query(OfficialGazetteNorm)
        .filter(OfficialGazetteNorm.importancia_ciudadana >= min_score)
        .order_by(
            OfficialGazetteNorm.importancia_ciudadana.desc(),
            OfficialGazetteNorm.date.desc(),
        )
        .limit(limit)
        .all()
    )


def get_norma_by_cve(db: Session, cve: str) -> OfficialGazetteNorm | None:
    return db.query(OfficialGazetteNorm).filter(OfficialGazetteNorm.cve == cve).first()


def list_available_dates(db: Session, limit: int) -> list[str]:
    rows = (
        db.query(OfficialGazetteNorm.date)
        .distinct()
        .order_by(OfficialGazetteNorm.date.desc())
        .limit(limit)
        .all()
    )
    return [row.date.isoformat() for row in rows]


def stats_by_ministry(
    db: Session, date_from: date | None, date_to: date | None
) -> list[dict[str, int | str]]:
    query = db.query(
        OfficialGazetteNorm.ministry,
        func.count(OfficialGazetteNorm.id).label("count"),
    )

    if date_from:
        query = query.filter(OfficialGazetteNorm.date >= date_from)
    if date_to:
        query = query.filter(OfficialGazetteNorm.date <= date_to)

    rows = (
        query.group_by(OfficialGazetteNorm.ministry)
        .order_by(func.count(OfficialGazetteNorm.id).desc())
        .all()
    )
    return [
        {"ministry": ministry or "Unknown", "count": count}
        for ministry, count in rows
    ]

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.diario_oficial import NormaGeneral
from app.models.ingestor_state import IngestorState


def get_scrape_health(db: Session) -> dict:
    states = db.execute(select(IngestorState)).scalars().all()
    ingestor_status = {
        s.entity_type: s.last_sync_date.isoformat() if s.last_sync_date else None
        for s in states
    }
    latest_norma_date = db.execute(
        select(NormaGeneral.date).order_by(NormaGeneral.date.desc()).limit(1)
    ).scalar_one_or_none()

    return {
        "ingestors": ingestor_status,
        "latest_norma_date": latest_norma_date.isoformat() if latest_norma_date else None,
    }

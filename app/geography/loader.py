from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.geography.dataset import DEFAULT_GEOGRAPHY_DATASET_PATH, load_geography_dataset
from app.models.ingestor_state import IngestorState
from app.services.write import apply_geography_dataset


def run_load_geography(
    *,
    dataset_path: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    path = (
        Path(dataset_path)
        if dataset_path is not None
        else DEFAULT_GEOGRAPHY_DATASET_PATH
    )
    dataset = load_geography_dataset(path)

    db = SessionLocal()
    try:
        counts = apply_geography_dataset(db, dataset)
        state = _get_or_create_geography_state(db)
        state.last_sync_date = date.today()
        state.last_cursor = dataset.version
        db.flush()

        result = {
            "dry_run": dry_run,
            "dataset_path": str(path),
            "version": dataset.version,
            **counts,
        }
        if dry_run:
            db.rollback()
        else:
            db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _get_or_create_geography_state(db: Session) -> IngestorState:
    state = db.execute(
        select(IngestorState).where(IngestorState.entity_type == "geography")
    ).scalar_one_or_none()
    if state is None:
        state = IngestorState(entity_type="geography")
        db.add(state)
    return state

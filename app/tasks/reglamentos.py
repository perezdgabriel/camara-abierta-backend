from sqlalchemy import select

from app.core.celery_app import app
from app.core.session import task_session
from app.models.diario_oficial import Regulation
from app.services.notifications import send_alerta_reglamento
from app.services.write import upsert_reglamento
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.reglamentos.sync_reglamento", bind=True, base=DatabaseTask)
def sync_reglamento(self, data: dict) -> dict:
    with task_session() as db:
        existing = db.execute(
            select(Regulation)
            .where(Regulation.numero == data["numero"])
            .where(Regulation.anio == data["anio"])
            .where(Regulation.ministerio == data["ministerio"])
            .where(Regulation.categoria == data["categoria"])
        ).scalar_one_or_none()
        previous_status = existing.estado if existing is not None else None
        created = existing is None
        reglamento = upsert_reglamento(db, data)
        reglamento_id = reglamento.id

    if created or previous_status != data.get("estado"):
        send_alerta_reglamento(data, "nuevo" if created else "estado actualizado")
    return {"reglamento_id": reglamento_id, "created": created}

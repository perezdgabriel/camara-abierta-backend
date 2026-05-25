import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload, sessionmaker

from app.models.core import Topic
from app.models.proyecto import Bill, BillEvent, BillStage, BillUrgency
from app.services.write import upsert_bill

from .bill_payloads import make_initial_bill_payload, make_updated_bill_payload

pytestmark = pytest.mark.integration


def _load_bill(session_maker: sessionmaker, bulletin_number: str) -> Bill:
    with session_maker() as session:
        return session.execute(
            select(Bill)
            .options(
                selectinload(Bill.topics),
                selectinload(Bill.events),
                selectinload(Bill.stages),
                selectinload(Bill.urgencies),
            )
            .where(Bill.bulletin_number == bulletin_number)
        ).scalar_one()


def test_upsert_bill_tracks_create_update_and_noop_contract(
    db_session, db_session_factory: sessionmaker
):
    created_bill, created_change = upsert_bill(db_session, make_initial_bill_payload())
    db_session.flush()

    assert created_bill.bulletin_number == "100-06"
    assert created_change == {
        "is_new": True,
        "status_changed": False,
        "stage_changed": False,
        "old_status": None,
        "new_status": created_bill.status,
    }

    persisted_bill = _load_bill(db_session_factory, "100-06")
    assert persisted_bill.title == "Proyecto de integracion inicial"
    assert {topic.name for topic in persisted_bill.topics} == {
        "Transparencia",
        "Probidad",
    }
    assert len(persisted_bill.stages) == 1
    assert persisted_bill.stages[0].description == "Ingreso al Senado"
    assert len(persisted_bill.events) == 1
    assert persisted_bill.events[0].title == "Ingreso al Senado"
    assert persisted_bill.events[0].description == "Primer trámite constitucional"
    assert len(persisted_bill.urgencies) == 1
    assert persisted_bill.urgencies[0].is_active is True

    updated_bill, updated_change = upsert_bill(db_session, make_updated_bill_payload())
    db_session.flush()

    assert updated_bill.id == created_bill.id
    assert updated_change["is_new"] is False
    assert updated_change["status_changed"] is True
    assert updated_change["stage_changed"] is True

    refreshed_bill = _load_bill(db_session_factory, "100-06")
    assert refreshed_bill.title == "Proyecto de integracion actualizado"
    assert {topic.name for topic in refreshed_bill.topics} == {"Salud"}
    assert len(refreshed_bill.stages) == 1
    assert refreshed_bill.stages[0].description == "Pasa a segundo tramite"
    assert len(refreshed_bill.events) == 1
    assert refreshed_bill.events[0].title == "Pasa a segundo tramite"
    assert refreshed_bill.events[0].description == "Segundo trámite constitucional"
    assert len(refreshed_bill.urgencies) == 2
    assert sum(1 for urgency in refreshed_bill.urgencies if urgency.is_active) == 1

    third_bill, third_change = upsert_bill(db_session, make_updated_bill_payload())
    db_session.flush()

    assert third_bill.id == created_bill.id
    assert third_change["is_new"] is False
    assert third_change["status_changed"] is False
    assert third_change["stage_changed"] is False

    with db_session_factory() as session:
        bill_count = session.execute(
            select(func.count())
            .select_from(Bill)
            .where(Bill.bulletin_number == "100-06")
        ).scalar_one()
        stage_count = session.execute(
            select(func.count())
            .select_from(BillStage)
            .where(BillStage.bill_id == created_bill.id)
        ).scalar_one()
        event_count = session.execute(
            select(func.count())
            .select_from(BillEvent)
            .where(BillEvent.bill_id == created_bill.id)
        ).scalar_one()
        topic_names = set(session.execute(select(Topic.name)).scalars().all())
        urgency_count = session.execute(
            select(func.count())
            .select_from(BillUrgency)
            .where(BillUrgency.bill_id == created_bill.id)
        ).scalar_one()

    assert bill_count == 1
    assert stage_count == 1
    assert event_count == 1
    assert "Salud" in topic_names
    assert urgency_count == 2

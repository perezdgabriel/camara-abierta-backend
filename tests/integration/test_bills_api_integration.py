from datetime import date

import pytest

from app.models.enums import ChamberType, StageType, VotingType
from app.models.proyecto import BillStage
from app.services.write import (
    apply_bill_topic_classification,
    upsert_bill,
    upsert_voting_session,
)

from .bill_payloads import make_initial_bill_payload, make_secondary_bill_payload

pytestmark = pytest.mark.integration


def test_list_bills_returns_real_persisted_summary_and_filters_by_status(
    client, db_session
):
    first_bill, _ = upsert_bill(db_session, make_initial_bill_payload())
    upsert_bill(db_session, make_secondary_bill_payload())
    db_session.flush()

    response = client.get(
        "/api/v1/bills",
        params={"status": "pending", "limit": 10},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["data"][0]["id"] == first_bill.id
    assert body["data"][0]["bulletin_number"] == "100-06"
    assert body["data"][0]["status"] == "pending"
    assert body["data"][0]["active_urgency_type"] == "simple"
    assert body["data"][0]["current_stage_type"] == "first_constitutional_tramite"
    assert body["data"][0]["last_activity_date"] == "2026-05-02"


def test_list_bills_uses_recent_activity_by_default_and_supports_entry_date_sort(
    client, db_session
):
    first_payload = make_initial_bill_payload()
    first_payload["events"] = [
        {
            "event_date": "2026-05-20",
            "title": "Discusión en sala",
            "description": "Primer trámite constitucional",
            "_chamber_type": first_payload["events"][0]["_chamber_type"],
        }
    ]
    first_bill, _ = upsert_bill(db_session, first_payload)
    second_bill, _ = upsert_bill(db_session, make_secondary_bill_payload())
    db_session.flush()

    response = client.get("/api/v1/bills", params={"limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert [item["id"] for item in body["data"]] == [first_bill.id, second_bill.id]
    assert body["data"][0]["last_activity_date"] == "2026-05-20"
    assert body["data"][1]["last_activity_date"] == "2026-05-04"

    response = client.get("/api/v1/bills", params={"sort": "entry_date", "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["data"]] == [second_bill.id, first_bill.id]


def test_get_bill_returns_nested_relations_from_real_database(client, db_session):
    bill, _ = upsert_bill(db_session, make_initial_bill_payload())
    apply_bill_topic_classification(db_session, bill, ["Transparencia", "Probidad"])
    db_session.flush()

    response = client.get(f"/api/v1/bills/{bill.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == bill.id
    assert body["bulletin_number"] == "100-06"
    assert {topic["name"] for topic in body["topics"]} == {
        "Transparencia",
        "Probidad",
    }
    assert body["last_activity_date"] == "2026-05-02"
    assert len(body["stages"]) == 1
    assert body["stages"][0]["stage_type"] == "first_constitutional_tramite"
    assert body["stages"][0]["description"] == "Ingreso al Senado"
    assert len(body["events"]) == 1
    assert body["events"][0]["title"] == "Ingreso al Senado"
    assert body["events"][0]["description"] == "Primer trámite constitucional"
    assert len(body["urgencies"]) == 1
    assert body["urgencies"][0]["urgency_type"] == "simple"
    assert body["documents"] == []
    assert body["voting_sessions"] == []


def _make_bill_with_two_stages(db_session):
    payload = make_initial_bill_payload()
    payload["stages"] = [
        {
            "stage_type": StageType.FIRST_CONSTITUTIONAL_TRAMITE,
            "start_date": "2026-05-02",
            "_chamber_type": ChamberType.SENATE,
            "description": "Primer trámite",
        },
        {
            "stage_type": StageType.SECOND_CONSTITUTIONAL_TRAMITE,
            "start_date": "2026-06-01",
            "_chamber_type": ChamberType.DEPUTIES,
            "description": "Segundo trámite",
        },
    ]
    bill, _ = upsert_bill(db_session, payload)
    db_session.flush()
    db_session.refresh(bill, attribute_names=["stages"])
    first = next(s for s in bill.stages if s.start_date == date(2026, 5, 2))
    first.end_date = date(2026, 5, 31)
    db_session.flush()
    return bill, first


def _make_voting_session(db_session, *, bcn_id, chamber, voting_date, bulletin):
    return upsert_voting_session(
        db_session,
        {
            "bcn_id": bcn_id,
            "_chamber_type": chamber,
            "voting_date": voting_date,
            "voting_type": VotingType.GENERAL,
            "subject": f"Votación {bcn_id}",
            "result": "approved",
            "votes_for": 80,
            "votes_against": 30,
            "abstentions": 5,
        },
        bill_bulletin=bulletin,
    )


def test_get_bill_attributes_voting_sessions_to_stages_by_date_and_chamber(
    client, db_session
):
    bill, first_stage = _make_bill_with_two_stages(db_session)
    second_stage = next(s for s in bill.stages if s != first_stage)

    in_first = _make_voting_session(
        db_session,
        bcn_id="test:vot:1",
        chamber=ChamberType.SENATE,
        voting_date="2026-05-15T10:00:00",
        bulletin=bill.bulletin_number,
    )
    in_second = _make_voting_session(
        db_session,
        bcn_id="test:vot:2",
        chamber=ChamberType.DEPUTIES,
        voting_date="2026-06-10T15:00:00",
        bulletin=bill.bulletin_number,
    )
    orphan = _make_voting_session(
        db_session,
        bcn_id="test:vot:3",
        chamber=ChamberType.SENATE,
        voting_date="2026-04-20T09:00:00",
        bulletin=bill.bulletin_number,
    )
    db_session.flush()

    response = client.get(f"/api/v1/bills/{bill.id}")

    assert response.status_code == 200
    body = response.json()
    attribution = {s["id"]: s["bill_stage_id"] for s in body["voting_sessions"]}
    assert attribution[in_first.id] == first_stage.id
    assert attribution[in_second.id] == second_stage.id
    assert attribution[orphan.id] is None


def test_get_bill_picks_latest_start_date_when_stage_windows_overlap(
    client, db_session
):
    """Comisión mixta scenario: two stages open concurrently for the same chamber.

    Attribution must tie-break by latest ``start_date`` so a sesión during the
    overlap lands on the more specific (current-er) stage, not the older one.
    """
    bill, first_stage = _make_bill_with_two_stages(db_session)
    first_stage.end_date = date(2026, 6, 30)
    second_stage = next(s for s in bill.stages if s != first_stage)
    second_stage.chamber_id = first_stage.chamber_id
    db_session.add(
        BillStage(
            bill_id=bill.id,
            stage_type=StageType.MIXED_COMMISSION,
            chamber_id=first_stage.chamber_id,
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 20),
            description="Comisión mixta",
            is_current=False,
        )
    )
    db_session.flush()
    db_session.refresh(bill, attribute_names=["stages"])
    mixed = next(s for s in bill.stages if s.stage_type == StageType.MIXED_COMMISSION)

    overlap = _make_voting_session(
        db_session,
        bcn_id="test:vot:overlap",
        chamber=ChamberType.SENATE,
        voting_date="2026-06-10T11:00:00",
        bulletin=bill.bulletin_number,
    )
    db_session.flush()

    response = client.get(f"/api/v1/bills/{bill.id}")

    assert response.status_code == 200
    body = response.json()
    attribution = {s["id"]: s["bill_stage_id"] for s in body["voting_sessions"]}
    assert attribution[overlap.id] == mixed.id


def test_get_bill_attributes_documents_to_stages_by_date_window(client, db_session):
    bill, first_stage = _make_bill_with_two_stages(db_session)
    second_stage = next(s for s in bill.stages if s != first_stage)

    payload = make_initial_bill_payload()
    payload["bulletin_number"] = bill.bulletin_number
    payload["stages"] = [
        {
            "stage_type": first_stage.stage_type,
            "start_date": first_stage.start_date.isoformat(),
            "_chamber_type": ChamberType.SENATE,
            "description": first_stage.description or "",
        },
        {
            "stage_type": second_stage.stage_type,
            "start_date": second_stage.start_date.isoformat(),
            "_chamber_type": ChamberType.DEPUTIES,
            "description": second_stage.description or "",
        },
    ]
    payload["documents"] = [
        {
            "document_type": "report",
            "title": "Informe en primer trámite",
            "document_url": "https://example.test/informe-1.pdf",
            "document_date": "2026-05-15",
        },
        {
            "document_type": "official_communication",
            "title": "Oficio en segundo trámite",
            "document_url": "https://example.test/oficio-1.pdf",
            "document_date": "2026-06-10",
        },
        {
            "document_type": "report",
            "title": "Informe previo a los trámites",
            "document_url": "https://example.test/informe-orphan.pdf",
            "document_date": "2026-04-20",
        },
        {
            "document_type": "comparison",
            "title": "Texto comparado sin fecha",
            "document_url": "https://example.test/comparado.pdf",
            "document_date": None,
        },
    ]
    upsert_bill(db_session, payload)
    db_session.flush()

    response = client.get(f"/api/v1/bills/{bill.id}")

    assert response.status_code == 200
    body = response.json()
    attribution = {d["title"]: d["bill_stage_id"] for d in body["documents"]}
    assert attribution["Informe en primer trámite"] == first_stage.id
    assert attribution["Oficio en segundo trámite"] == second_stage.id
    assert attribution["Informe previo a los trámites"] is None
    assert attribution["Texto comparado sin fecha"] is None


def test_get_bill_persists_full_official_communication_document_type(
    client, db_session
):
    """Regression: BillDocument.document_type used to be String(20), silently
    truncating ``official_communication`` to ``official_communicati`` on insert.
    """
    payload = make_initial_bill_payload()
    payload["documents"] = [
        {
            "document_type": "official_communication",
            "title": "Oficio de prueba",
            "document_url": "https://example.test/oficio.pdf",
            "document_date": "2026-05-15",
        },
    ]
    bill, _ = upsert_bill(db_session, payload)
    db_session.flush()

    response = client.get(f"/api/v1/bills/{bill.id}")

    assert response.status_code == 200
    body = response.json()
    assert len(body["documents"]) == 1
    assert body["documents"][0]["document_type"] == "official_communication"

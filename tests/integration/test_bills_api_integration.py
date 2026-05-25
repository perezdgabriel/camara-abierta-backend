import pytest

from app.services.write import upsert_bill

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

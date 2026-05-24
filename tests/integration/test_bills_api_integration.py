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
    assert len(body["stages"]) == 1
    assert body["stages"][0]["stage_type"] == "first_constitutional_tramite"
    assert body["stages"][0]["description"] == "Ingreso al Senado"
    assert len(body["urgencies"]) == 1
    assert body["urgencies"][0]["urgency_type"] == "simple"
    assert body["documents"] == []
    assert body["voting_sessions"] == []

from collections.abc import Iterator
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.router import router as api_router
from app.api.v1 import legislators as legislators_api
from app.api.v1 import proyectos as bills_api
from app.api.v1 import voting as voting_api
from app.core.database import get_db
from app.models.enums import (
    BillOrigin,
    BillStatus,
    BillType,
    ChamberType,
    SignalType,
    StageType,
    UrgencyType,
    VotingResult,
    VotingType,
)


def ns(**kwargs):
    return SimpleNamespace(**kwargs)


@pytest.fixture
def fake_db() -> object:
    return object()


@pytest.fixture
def api_app(fake_db: object) -> Iterator[FastAPI]:
    app = FastAPI()
    app.include_router(api_router)

    def override_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = override_get_db
    yield app


@pytest.fixture
def client(api_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(api_app) as test_client:
        yield test_client


def make_bill() -> SimpleNamespace:
    now = datetime(2026, 5, 22, 12, 0, 0)
    chamber = ns(
        id=1, chamber_type=ChamberType.DEPUTIES, name="Camara de Diputadas y Diputados"
    )
    committee = ns(id=2, name="Hacienda")
    topic = ns(id=3, name="Transparencia", slug="transparencia", icon=None)
    urgency = ns(
        id=4,
        urgency_type=UrgencyType.SIMPLE,
        chamber=chamber,
        entry_date=date(2026, 5, 1),
        withdrawal_date=None,
        deadline_date=None,
        is_active=True,
    )
    stage = ns(
        id=5,
        stage_type=StageType.FIRST_CONSTITUTIONAL_TRAMITE,
        chamber=chamber,
        committee=committee,
        start_date=date(2026, 5, 2),
        end_date=None,
        result=None,
        description="Primer tramite",
        is_current=True,
    )
    event = ns(
        id=6,
        event_date=date(2026, 5, 3),
        title="Ingreso",
        description="Primer trámite constitucional",
        chamber=chamber,
        bill_stage_id=None,
    )
    voting_session = ns(
        id=7,
        voting_date=now,
        votes_for=80,
        votes_against=40,
        abstentions=5,
    )
    return ns(
        id=10,
        bulletin_number="123-06",
        title="Proyecto de prueba",
        bill_type=BillType.PROJECT,
        origin=BillOrigin.EXECUTIVE,
        status=BillStatus.PENDING,
        entry_date=date(2026, 5, 1),
        publication_date=None,
        law_number=None,
        origin_chamber=chamber,
        current_chamber=chamber,
        current_committee=committee,
        topics=[topic],
        events=[event],
        urgencies=[urgency],
        stages=[stage],
        voting_sessions=[voting_session],
        created_at=now,
        updated_at=now,
        sync_version=99,
    )


def make_legislator() -> SimpleNamespace:
    """Build a fake legislator mirroring the new property-based shape (ADR-0015).

    ``Legislator`` no longer carries chamber/party/district/circumscription as
    columns; the schema reads ``current_*`` properties. The namespace exposes
    those names directly so the Pydantic ``validation_alias`` resolves.
    """
    now = datetime(2026, 5, 22, 12, 0, 0)
    party = ns(id=1, name="Partido Demo", abbreviation="PD", color="#112233")
    district = ns(id=2, number=8, name="Distrito 8")
    circumscription = ns(id=3, number=7, name="Circunscripcion 7")
    return ns(
        id=20,
        current_chamber_external_id="senado:20",
        full_name="Ada Demo",
        current_chamber_type=ChamberType.SENATE,
        photo_thumbnail_url=None,
        current_party=party,
        current_district=district,
        current_circumscription=circumscription,
        is_active=True,
        created_at=now,
        updated_at=now,
        sync_version=101,
    )


def make_voting_session(signals=None) -> SimpleNamespace:
    now = datetime(2026, 5, 22, 12, 0, 0)
    chamber = ns(id=1, chamber_type=ChamberType.SENATE, name="Senado de la Republica")
    bill = ns(id=10, bulletin_number="123-06", title="Proyecto de prueba")
    return ns(
        id=30,
        bcn_id="senado:vot:123-06:1",
        chamber=chamber,
        bill=bill,
        voting_date=now,
        voting_type=VotingType.GENERAL,
        subject="Votacion general",
        result=VotingResult.APPROVED,
        votes_for=23,
        votes_against=10,
        abstentions=1,
        dispensed_count=0,
        absences=0,
        paired_count=2,
        quorum_type="simple",
        session_ref="42",
        stage_label="Primer trámite constitucional",
        article_text=None,
        constitutional_procedure_id=None,
        constitutional_procedure_label=None,
        regulatory_procedure_id=None,
        regulatory_procedure_label=None,
        signals=signals if signals is not None else [],
        votes=[],
        created_at=now,
        updated_at=now,
        sync_version=202,
    )


def make_voting_signal(
    signal_type: SignalType = SignalType.VOTACION_DIVIDIDA,
    severity: float = 0.95,
    payload: dict | None = None,
) -> SimpleNamespace:
    return ns(
        signal_type=signal_type,
        severity=severity,
        payload=payload
        if payload is not None
        else {"margin": 2, "participation": 0.97},
    )


def test_bills_endpoint_uses_english_prefix_and_enum_filters(
    client, fake_db, monkeypatch
):
    captured: dict[str, object] = {}

    def fake_list_bills(db, **kwargs):
        captured["db"] = db
        captured.update(kwargs)
        return 1, [make_bill()]

    monkeypatch.setattr(bills_api.svc, "list_bills", fake_list_bills)

    response = client.get(
        "/api/v1/bills",
        params={
            "status": "pending",
            "origin": "executive",
            "tipo": "project",
            "limit": 1,
        },
    )

    assert response.status_code == 200
    assert client.get("/api/v1/proyectos").status_code == 404
    assert captured["db"] is fake_db
    assert captured["status"] is BillStatus.PENDING
    assert captured["origin"] is BillOrigin.EXECUTIVE
    assert captured["bill_type"] is BillType.PROJECT
    assert captured["sort"] is bills_api.svc.BillSort.RECENT_ACTIVITY
    body = response.json()
    assert body["count"] == 1
    assert body["data"][0]["bulletin_number"] == "123-06"
    assert body["data"][0]["status"] == "pending"
    assert body["data"][0]["current_stage_type"] == "first_constitutional_tramite"
    assert body["data"][0]["active_urgency_type"] == "simple"
    assert body["data"][0]["last_activity_date"] == "2026-05-03"


def test_bills_endpoint_accepts_entry_date_sort(client, fake_db, monkeypatch):
    captured: dict[str, object] = {}

    def fake_list_bills(db, **kwargs):
        captured["db"] = db
        captured.update(kwargs)
        return 1, [make_bill()]

    monkeypatch.setattr(bills_api.svc, "list_bills", fake_list_bills)

    response = client.get("/api/v1/bills", params={"sort": "entry_date"})

    assert response.status_code == 200
    assert captured["db"] is fake_db
    assert captured["sort"] is bills_api.svc.BillSort.ENTRY_DATE


def test_legislators_endpoint_uses_new_prefix_and_canonical_chamber_filter(
    client, fake_db, monkeypatch
):
    captured: dict[str, object] = {}

    def fake_list_legislators(db, **kwargs):
        captured["db"] = db
        captured.update(kwargs)
        return 1, [make_legislator()]

    monkeypatch.setattr(
        legislators_api.legislators_service, "list_legislators", fake_list_legislators
    )

    response = client.get(
        "/api/v1/legislators",
        params={"chamber_type": "senate", "district": 8},
    )

    assert response.status_code == 200
    assert captured["db"] is fake_db
    assert captured["chamber_type"] is ChamberType.SENATE
    assert captured["district"] == 8
    # Active-only default: include_inactive must be False unless explicitly set
    assert captured["include_inactive"] is False
    body = response.json()
    assert body["count"] == 1
    assert body["data"][0]["full_name"] == "Ada Demo"
    assert body["data"][0]["chamber_type"] == "senate"


def test_legislators_endpoint_forwards_new_filters(client, monkeypatch):
    captured: dict[str, object] = {}

    def fake_list_legislators(db, **kwargs):
        captured.update(kwargs)
        return 0, []

    monkeypatch.setattr(
        legislators_api.legislators_service, "list_legislators", fake_list_legislators
    )

    response = client.get(
        "/api/v1/legislators",
        params={
            "q": "ara",
            "party": "PS",
            "region": 5,
            "chamber_type": "deputies",
            "include_inactive": "true",
        },
    )

    assert response.status_code == 200
    assert captured["q"] == "ara"
    assert captured["party"] == "PS"
    assert captured["region"] == 5
    assert captured["chamber_type"] is ChamberType.DEPUTIES
    assert captured["include_inactive"] is True


def test_legislators_endpoint_forwards_independent_sentinel(client, monkeypatch):
    captured: dict[str, object] = {}

    def fake_list_legislators(db, **kwargs):
        captured.update(kwargs)
        return 0, []

    monkeypatch.setattr(
        legislators_api.legislators_service, "list_legislators", fake_list_legislators
    )

    response = client.get(
        "/api/v1/legislators",
        params={"party": "__independent__"},
    )

    assert response.status_code == 200
    assert captured["party"] == "__independent__"


def test_voting_sessions_endpoint_uses_new_prefix_and_serializes_summary(
    client, fake_db, monkeypatch
):
    captured: dict[str, object] = {}
    signal = make_voting_signal(
        signal_type=SignalType.QUIEBRE_BLOQUE,
        severity=0.42,
        payload={"parties_below_threshold": [], "threshold": 0.8},
    )

    def fake_list_voting_sessions(db, **kwargs):
        captured["db"] = db
        captured.update(kwargs)
        return 1, [make_voting_session(signals=[signal])]

    monkeypatch.setattr(
        voting_api.voting_service, "list_voting_sessions", fake_list_voting_sessions
    )

    response = client.get(
        "/api/v1/voting-sessions",
        params={"chamber": "senate", "bill_id": 10},
    )

    assert response.status_code == 200
    assert captured["db"] is fake_db
    assert captured["chamber"] is ChamberType.SENATE
    assert captured["bill_id"] == 10
    body = response.json()
    assert body["count"] == 1
    assert body["data"][0]["bcn_id"] == "senado:vot:123-06:1"
    assert body["data"][0]["voting_type"] == "general"
    assert body["data"][0]["result"] == "approved"
    # Light SignalRef shape on list responses: type + severity, no payload.
    assert body["data"][0]["signals"] == [
        {"signal_type": "quiebre_bloque", "severity": 0.42}
    ]


def test_voting_session_detail_serializes_senado_metadata(client, fake_db, monkeypatch):
    captured: dict[str, object] = {}
    signal = make_voting_signal(
        signal_type=SignalType.VOTACION_DIVIDIDA,
        severity=0.95,
        payload={"margin": 2, "margin_ratio": 0.013, "participation": 0.97},
    )

    def fake_get_voting_session(db, voting_session_id):
        captured["db"] = db
        captured["voting_session_id"] = voting_session_id
        return make_voting_session(signals=[signal])

    monkeypatch.setattr(
        voting_api.voting_service, "get_voting_session", fake_get_voting_session
    )

    response = client.get("/api/v1/voting-sessions/30")

    assert response.status_code == 200
    assert captured["db"] is fake_db
    assert captured["voting_session_id"] == 30
    body = response.json()
    assert body["paired_count"] == 2
    assert body["session_ref"] == "42"
    assert body["stage_label"] == "Primer trámite constitucional"
    # Full BareSignal shape on detail responses: type + severity + payload.
    assert body["signals"] == [
        {
            "signal_type": "votacion_dividida",
            "severity": 0.95,
            "payload": {"margin": 2, "margin_ratio": 0.013, "participation": 0.97},
        }
    ]


# ── /legislators/{id}/authored-bills ────────────────────────────────────


def _make_mocion(*, id: int, bulletin: str, title: str) -> SimpleNamespace:
    bill = make_bill()
    bill.id = id
    bill.bulletin_number = bulletin
    bill.title = title
    bill.origin = BillOrigin.DEPUTIES
    return bill


def test_authored_bills_endpoint_returns_items_and_total(
    client, fake_db, monkeypatch
):
    captured: dict[str, object] = {}

    def fake_get_legislator(*, db, legislator_id):
        captured["legislator_id"] = legislator_id
        return make_legislator()

    def fake_authored_bills(db, legislator_id, limit):
        captured["limit"] = limit
        return (
            [
                _make_mocion(id=101, bulletin="100-06", title="Moción A"),
                _make_mocion(id=102, bulletin="200-07", title="Moción B"),
            ],
            42,
        )

    monkeypatch.setattr(
        legislators_api.legislators_service, "get_legislator", fake_get_legislator
    )
    monkeypatch.setattr(
        legislators_api.legislators_service,
        "get_legislator_authored_bills",
        fake_authored_bills,
    )

    response = client.get("/api/v1/legislators/55/authored-bills")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 42
    assert len(body["items"]) == 2
    assert body["items"][0]["bulletin_number"] == "100-06"
    assert body["items"][0]["origin"] == "deputies"
    # Default limit applied from service constant
    assert captured["limit"] == (
        legislators_api.legislators_service.DEFAULT_AUTHORED_BILLS_LIMIT
    )
    assert captured["legislator_id"] == 55


def test_authored_bills_endpoint_404s_for_unknown_legislator(
    client, fake_db, monkeypatch
):
    monkeypatch.setattr(
        legislators_api.legislators_service,
        "get_legislator",
        lambda *, db, legislator_id: None,
    )

    response = client.get("/api/v1/legislators/9999/authored-bills")
    assert response.status_code == 404


def test_authored_bills_endpoint_forwards_explicit_limit(
    client, fake_db, monkeypatch
):
    captured: dict[str, object] = {}

    def fake_authored_bills(db, legislator_id, limit):
        captured["limit"] = limit
        return [], 0

    monkeypatch.setattr(
        legislators_api.legislators_service,
        "get_legislator",
        lambda *, db, legislator_id: make_legislator(),
    )
    monkeypatch.setattr(
        legislators_api.legislators_service,
        "get_legislator_authored_bills",
        fake_authored_bills,
    )

    response = client.get(
        "/api/v1/legislators/55/authored-bills", params={"limit": 25}
    )
    assert response.status_code == 200
    assert captured["limit"] == 25


def test_authored_bills_endpoint_rejects_out_of_range_limit(client, monkeypatch):
    monkeypatch.setattr(
        legislators_api.legislators_service,
        "get_legislator",
        lambda *, db, legislator_id: make_legislator(),
    )
    # MAX is 100; 101 must 422.
    response = client.get(
        "/api/v1/legislators/55/authored-bills", params={"limit": 101}
    )
    assert response.status_code == 422

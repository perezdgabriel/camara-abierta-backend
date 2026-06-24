from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import (
    CalendarEventKind,
    CalendarEventSource,
    ChamberType,
)
from app.models.legislature import CalendarEvent
from app.services.write import upsert_calendar_event

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def test_upsert_calendar_event_inserts_manual_row(db_session):
    starts = _now() + timedelta(days=1)

    event = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.SESION,
            "starts_at": starts,
            "title": "Sala — vota Boletín 100-06",
            "chamber_type": ChamberType.DEPUTIES,
        },
    )

    assert event.id is not None
    assert event.kind is CalendarEventKind.SESION
    assert event.source is CalendarEventSource.MANUAL
    assert event.external_ref is None
    assert event.chamber_type is ChamberType.DEPUTIES


def test_upsert_calendar_event_manual_rows_always_insert(db_session):
    starts = _now() + timedelta(days=1)
    payload = {
        "kind": CalendarEventKind.OTRO,
        "starts_at": starts,
        "title": "Mismo título, sin external_ref",
    }

    first = upsert_calendar_event(db_session, payload)
    second = upsert_calendar_event(db_session, payload)

    assert first.id != second.id, "manual rows (null external_ref) must always insert"


def test_upsert_calendar_event_dedups_by_source_and_external_ref(db_session):
    starts = _now() + timedelta(days=2)
    ref = "camara_agenda:2026-06-30:sala"

    first = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.SESION,
            "starts_at": starts,
            "title": "Sesión original",
            "source": CalendarEventSource.MANUAL,
            "external_ref": ref,
        },
    )
    initial_sync_version = first.sync_version

    second = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.SESION,
            "starts_at": starts,
            "title": "Sesión con título actualizado",
            "source": CalendarEventSource.MANUAL,
            "external_ref": ref,
        },
    )

    assert second.id == first.id, "matching (source, external_ref) must update in place"
    assert second.title == "Sesión con título actualizado"
    assert second.sync_version > initial_sync_version

    rows = db_session.query(CalendarEvent).filter_by(external_ref=ref).all()
    assert len(rows) == 1


def test_upsert_calendar_event_assumes_utc_when_naive(db_session):
    naive = datetime(2026, 7, 1, 10, 30)

    event = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.COMISION,
            "starts_at": naive,
            "title": "Comisión de Hacienda",
        },
    )

    assert event.starts_at.tzinfo is not None
    assert event.starts_at == naive.replace(tzinfo=timezone.utc)


def test_get_calendar_returns_upcoming_events_in_window(client, db_session):
    today = _now().replace(hour=12, minute=0, second=0)
    past = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.SESION,
            "starts_at": today - timedelta(days=3),
            "title": "Sesión pasada",
        },
    )
    upcoming = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.INTERPELACION,
            "starts_at": today + timedelta(days=2),
            "title": "Interpelación al Ministro",
            "chamber_type": ChamberType.DEPUTIES,
        },
    )
    far_future = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.PLAZO,
            "starts_at": today + timedelta(days=30),
            "title": "Plazo lejano",
        },
    )
    db_session.flush()

    response = client.get("/api/v1/calendar")
    assert response.status_code == 200
    body = response.json()
    ids = [row["id"] for row in body["data"]]
    assert upcoming.id in ids
    assert past.id not in ids
    assert far_future.id not in ids
    assert body["count"] == len(ids)
    row = next(r for r in body["data"] if r["id"] == upcoming.id)
    assert row["kind"] == "interpelacion"
    assert row["chamber_type"] == "deputies"
    assert row["source"] == "manual"


def test_get_calendar_filters_by_kind_and_chamber(client, db_session):
    today = _now().replace(hour=9, minute=0, second=0)
    sesion = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.SESION,
            "starts_at": today + timedelta(days=1),
            "title": "Sesión Sala",
            "chamber_type": ChamberType.DEPUTIES,
        },
    )
    comision = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.COMISION,
            "starts_at": today + timedelta(days=2),
            "title": "Comisión Senado",
            "chamber_type": ChamberType.SENATE,
        },
    )
    db_session.flush()

    response = client.get("/api/v1/calendar", params={"tipo": "sesion"})
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["data"]]
    assert sesion.id in ids
    assert comision.id not in ids

    response = client.get("/api/v1/calendar", params={"camara": "senate"})
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["data"]]
    assert comision.id in ids
    assert sesion.id not in ids


def test_get_calendar_excludes_soft_deleted_rows(client, db_session):
    today = _now()
    event = upsert_calendar_event(
        db_session,
        {
            "kind": CalendarEventKind.MENSAJE,
            "starts_at": today + timedelta(days=1),
            "title": "Mensaje presidencial",
        },
    )
    event.deleted_at = today
    db_session.flush()

    response = client.get("/api/v1/calendar")
    assert response.status_code == 200
    ids = [row["id"] for row in response.json()["data"]]
    assert event.id not in ids

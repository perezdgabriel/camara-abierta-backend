from datetime import date
from pathlib import Path

import pytest

from app.ingestors.parsers.tabla_semanal import parse_tabla_semanal_pdf
from app.models.enums import (
    CalendarEventKind,
    CalendarEventSource,
    ChamberType,
)

FIXTURE = Path(__file__).parent / "fixtures" / "tabla_semanal_2026-06-22.pdf"


@pytest.fixture(scope="module")
def events() -> list[dict]:
    return parse_tabla_semanal_pdf(FIXTURE.read_bytes())


def _by_kind(events: list[dict], kind: CalendarEventKind) -> list[dict]:
    return [ev for ev in events if ev["kind"] is kind]


def _by_bulletin(events: list[dict], bulletin: str) -> dict | None:
    for ev in events:
        if ev.get("bulletin_number") == bulletin:
            return ev
    return None


def test_three_sesion_rows_for_mon_tue_wed(events: list[dict]) -> None:
    sesiones = _by_kind(events, CalendarEventKind.SESION)
    assert len(sesiones) == 3
    dates = sorted(ev["starts_at"].date() for ev in sesiones)
    assert dates == [date(2026, 6, 22), date(2026, 6, 23), date(2026, 6, 24)]


def test_session_times_match_pdf_in_santiago(events: list[dict]) -> None:
    sesiones = {
        ev["starts_at"].date(): ev for ev in _by_kind(events, CalendarEventKind.SESION)
    }
    lunes = sesiones[date(2026, 6, 22)]
    assert lunes["starts_at"].hour == 17 and lunes["starts_at"].minute == 0
    assert lunes["ends_at"] is not None and lunes["ends_at"].hour == 19
    assert lunes["starts_at"].tzinfo is not None
    assert lunes["starts_at"].utcoffset().total_seconds() in (-3 * 3600, -4 * 3600)

    martes = sesiones[date(2026, 6, 23)]
    assert martes["starts_at"].hour == 10
    assert martes["ends_at"] is None

    miercoles = sesiones[date(2026, 6, 24)]
    assert miercoles["starts_at"].hour == 10
    assert miercoles["ends_at"] is not None and miercoles["ends_at"].hour == 14


def test_chamber_type_hardcoded_to_deputies(events: list[dict]) -> None:
    for ev in events:
        assert ev["chamber_type"] is ChamberType.DEPUTIES


def test_all_rows_carry_tabla_semanal_source(events: list[dict]) -> None:
    for ev in events:
        assert ev["source"] is CalendarEventSource.TABLA_SEMANAL
        assert ev["external_ref"].startswith("tabla-semanal:")


def test_acusacion_constitucional_emitted_on_martes(events: list[dict]) -> None:
    acus = _by_kind(events, CalendarEventKind.ACUSACION_CONSTITUCIONAL)
    assert len(acus) == 1
    assert acus[0]["starts_at"].date() == date(2026, 6, 23)
    assert "Grau" in (acus[0]["description"] or "")


def test_three_informe_cei_rows(events: list[dict]) -> None:
    ceis = _by_kind(events, CalendarEventKind.INFORME_CEI)
    assert len(ceis) == 3
    refs = sorted(ev["external_ref"] for ev in ceis)
    assert any("cei-70" in r for r in refs)
    assert any("cei-73" in r for r in refs)
    assert any("cei-69-y-71" in r for r in refs)


def test_fifteen_votacion_rows_with_bulletin(events: list[dict]) -> None:
    votaciones = _by_kind(events, CalendarEventKind.VOTACION)
    assert len(votaciones) == 15
    for ev in votaciones:
        assert ev.get("bulletin_number") is not None


def test_refundidos_pick_first_boletin_and_describe_rest(events: list[dict]) -> None:
    lobby = _by_bulletin(events, "16593-06")
    assert lobby is not None
    assert lobby["related_bulletins"] == ["16888-06", "16988-06"]
    assert "16888-06" in (lobby["description"] or "")
    assert "16988-06" in (lobby["description"] or "")
    assert lobby["external_ref"] == "tabla-semanal:16593-06:2026-06-22"

    aguas = _by_bulletin(events, "17324-33")
    assert aguas is not None
    assert aguas["related_bulletins"] == ["17325-33"]


def test_boletin_split_across_line_break_is_recovered(events: list[dict]) -> None:
    # "Boletín N° 10986-24" wraps as "10986-\n24" in the cell — the parser
    # mends it back. The Baldomero Lillo Lota row is on Lunes.
    monumento = _by_bulletin(events, "10986-24")
    assert monumento is not None
    assert monumento["starts_at"].date() == date(2026, 6, 22)


def test_external_refs_are_unique(events: list[dict]) -> None:
    refs = [ev["external_ref"] for ev in events]
    assert len(refs) == len(set(refs))


def test_session_external_ref_shape(events: list[dict]) -> None:
    sesiones = _by_kind(events, CalendarEventKind.SESION)
    refs = sorted(ev["external_ref"] for ev in sesiones)
    assert refs == [
        "tabla-semanal:sesion:2026-06-22",
        "tabla-semanal:sesion:2026-06-23",
        "tabla-semanal:sesion:2026-06-24",
    ]


def test_total_event_count_matches_pdf(events: list[dict]) -> None:
    # Page 1: Lun 5, Mar 1, Mié 5  → 11
    # Page 2: Lun 4, Mar 0, Mié 4  → 8
    # Plus 3 sesiones.
    assert len(events) == 22

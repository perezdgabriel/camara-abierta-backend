"""End-to-end tests for the ``GET /legislators/{id}`` detail endpoint, focused
on the term + period payload that drives the *Trayectoria parlamentaria* UI.

Three shapes are exercised:

1. One term inside one Período (the common active-deputy case).
2. Two terms inside one Período — a mid-Período party switch (ADR-0015 "one
   row per per-stint party window"): the legislator should surface both terms,
   each tagged to the same Período, with the Período listed once.
3. One term spanning two Períodos — the senate mandate case. The term carries
   ``period_id`` pointing at the older Período (per
   :func:`_resolve_term_period`), but the ``periods`` array should list *both*
   Períodos so the frontend can render the term row under each.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models.enums import ChamberType
from app.models.core import Circumscription, District, Region
from app.models.legislature import (
    Chamber,
    LegislativePeriod,
    Legislator,
    LegislatorTerm,
    PoliticalParty,
)

pytestmark = pytest.mark.integration


def _make_period(
    db: Session, *, number: int, start: date, end: date
) -> LegislativePeriod:
    period = LegislativePeriod(number=number, start_date=start, end_date=end)
    db.add(period)
    db.flush()
    return period


def _make_chamber(
    db: Session, *, chamber_type: ChamberType, name: str, seats: int
) -> Chamber:
    chamber = Chamber(chamber_type=chamber_type, name=name, total_seats=seats)
    db.add(chamber)
    db.flush()
    return chamber


def _make_party(db: Session, *, name: str, abbr: str) -> PoliticalParty:
    party = PoliticalParty(name=name, abbreviation=abbr, color="#123456")
    db.add(party)
    db.flush()
    return party


def _make_legislator(db: Session, *, first: str, last: str) -> Legislator:
    leg = Legislator(first_name=first, last_name=last, full_name=f"{first} {last}")
    db.add(leg)
    db.flush()
    return leg


def _make_district(db: Session, *, number: int, name: str) -> District:
    region = Region(
        number=number, name=f"Region for D{number}", capital=f"Capital {number}"
    )
    db.add(region)
    db.flush()
    district = District(number=number, name=name, region_id=region.id)
    db.add(district)
    db.flush()
    return district


def _make_circumscription(db: Session, *, number: int, name: str) -> Circumscription:
    circ = Circumscription(number=number, name=name)
    db.add(circ)
    db.flush()
    return circ


def test_detail_single_term_single_period(client, db_session: Session) -> None:
    period = _make_period(
        db_session, number=56, start=date(2026, 3, 11), end=date(2030, 3, 11)
    )
    deputies = _make_chamber(
        db_session, chamber_type=ChamberType.DEPUTIES, name="Cámara", seats=155
    )
    party = _make_party(db_session, name="Partido Demo", abbr="PD")
    district = _make_district(db_session, number=8, name="Distrito 8")

    leg = _make_legislator(db_session, first="Ada", last="Demo")
    db_session.add(
        LegislatorTerm(
            legislator_id=leg.id,
            period_id=period.id,
            chamber_id=deputies.id,
            party_id=party.id,
            district_id=district.id,
            start_date=date(2026, 3, 11),
            end_date=None,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/legislators/{leg.id}")
    assert response.status_code == 200
    body = response.json()

    assert len(body["terms"]) == 1
    term = body["terms"][0]
    assert term["period"] == {
        "id": period.id,
        "number": 56,
        "start_date": "2026-03-11",
        "end_date": "2030-03-11",
    }
    assert term["chamber"]["chamber_type"] == "deputies"
    assert term["party"]["abbreviation"] == "PD"
    assert term["district"]["number"] == 8
    assert term["circumscription"] is None

    assert len(body["periods"]) == 1
    assert body["periods"][0]["number"] == 56


def test_detail_two_terms_same_period_party_switch(client, db_session: Session) -> None:
    period = _make_period(
        db_session, number=56, start=date(2026, 3, 11), end=date(2030, 3, 11)
    )
    deputies = _make_chamber(
        db_session, chamber_type=ChamberType.DEPUTIES, name="Cámara", seats=155
    )
    party_old = _make_party(db_session, name="Partido Original", abbr="PO")
    party_new = _make_party(db_session, name="Partido Nuevo", abbr="PN")
    district = _make_district(db_session, number=4, name="Distrito 4")
    leg = _make_legislator(db_session, first="Beto", last="Switchman")

    db_session.add(
        LegislatorTerm(
            legislator_id=leg.id,
            period_id=period.id,
            chamber_id=deputies.id,
            party_id=party_old.id,
            district_id=district.id,
            start_date=date(2026, 3, 11),
            end_date=date(2028, 4, 30),
            end_reason="renunció",
        )
    )
    db_session.add(
        LegislatorTerm(
            legislator_id=leg.id,
            period_id=period.id,
            chamber_id=deputies.id,
            party_id=party_new.id,
            district_id=district.id,
            start_date=date(2028, 5, 1),
            end_date=None,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/legislators/{leg.id}")
    assert response.status_code == 200
    body = response.json()

    # Terms come back ordered desc by start_date.
    assert len(body["terms"]) == 2
    assert [t["party"]["abbreviation"] for t in body["terms"]] == ["PN", "PO"]
    assert body["terms"][1]["end_reason"] == "renunció"
    # Both terms share the same Período.
    assert {t["period"]["id"] for t in body["terms"]} == {period.id}

    # The Período list should only list it once.
    assert [p["id"] for p in body["periods"]] == [period.id]


def test_detail_term_spanning_two_periods(client, db_session: Session) -> None:
    """Senate mandate case: one term spans two Períodos.

    ``period_id`` on the term resolves to the *older* Período (the one the
    term started in), but the legislator's ``periods`` array must enumerate
    *both* periods the term overlaps so the UI can render the same term row
    under each.
    """
    older = _make_period(
        db_session, number=55, start=date(2022, 3, 11), end=date(2026, 3, 11)
    )
    newer = _make_period(
        db_session, number=56, start=date(2026, 3, 11), end=date(2030, 3, 11)
    )
    senate = _make_chamber(
        db_session, chamber_type=ChamberType.SENATE, name="Senado", seats=50
    )
    party = _make_party(db_session, name="Partido Senatorial", abbr="PS")
    circumscription = _make_circumscription(db_session, number=7, name="Circ 7")
    leg = _make_legislator(db_session, first="Carla", last="Octenal")

    db_session.add(
        LegislatorTerm(
            legislator_id=leg.id,
            period_id=older.id,  # term started in 2022, so it lands here
            chamber_id=senate.id,
            party_id=party.id,
            circumscription_id=circumscription.id,
            start_date=date(2022, 3, 11),
            end_date=date(2030, 3, 10),
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/legislators/{leg.id}")
    assert response.status_code == 200
    body = response.json()

    assert len(body["terms"]) == 1
    assert body["terms"][0]["period"]["id"] == older.id
    assert body["terms"][0]["circumscription"]["number"] == 7
    assert body["terms"][0]["district"] is None

    # Both Períodos must surface — the UI renders the same term under each.
    period_ids = [p["id"] for p in body["periods"]]
    assert period_ids == [newer.id, older.id]  # sorted desc by start_date

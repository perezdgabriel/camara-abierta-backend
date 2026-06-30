"""Integration test for the moción authorship audit CLI command."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.models.enums import BillOrigin, BillStatus, BillType, ChamberType
from app.models.legislature import Chamber, Legislator
from app.models.proyecto import Bill, BillAuthorship
from app.services.audit import mocion_authors as audit

pytestmark = pytest.mark.integration


def _make_chamber(db, chamber_type: ChamberType, seats: int) -> Chamber:
    chamber = Chamber(
        chamber_type=chamber_type,
        name=chamber_type.value.title(),
        total_seats=seats,
    )
    db.add(chamber)
    db.flush()
    return chamber


def _make_legislator(db, *, full_name: str) -> Legislator:
    first, *rest = full_name.split(" ", 1)
    legislator = Legislator(
        first_name=first,
        last_name=rest[0] if rest else "",
        full_name=full_name,
    )
    db.add(legislator)
    db.flush()
    return legislator


def _make_bill(
    db,
    *,
    bulletin: str,
    origin: BillOrigin,
    origin_chamber: Chamber | None,
    entry_date: date = date(2026, 1, 1),
    title: str = "Proyecto",
) -> Bill:
    bill = Bill(
        bulletin_number=bulletin,
        title=title,
        bill_type=BillType.PROJECT,
        origin=origin,
        origin_chamber_id=origin_chamber.id if origin_chamber else None,
        status=BillStatus.PENDING,
        entry_date=entry_date,
    )
    db.add(bill)
    db.flush()
    return bill


def test_audit_collects_mociones_and_skips_executive_bills(db_session):
    senate = _make_chamber(db_session, ChamberType.SENATE, 50)
    deputies = _make_chamber(db_session, ChamberType.DEPUTIES, 155)

    ada = _make_legislator(db_session, full_name="Ada Lovelace")

    # Moción with one matched author
    bill_with_author = _make_bill(
        db_session,
        bulletin="100-06",
        origin=BillOrigin.DEPUTIES,
        origin_chamber=senate,
        entry_date=date(2026, 3, 1),
    )
    db_session.add(BillAuthorship(bill_id=bill_with_author.id, legislator_id=ada.id))

    # Moción with zero authors
    _make_bill(
        db_session,
        bulletin="200-07",
        origin=BillOrigin.DEPUTIES,
        origin_chamber=deputies,
        entry_date=date(2025, 6, 1),
    )

    # Mensaje — should be ignored by the audit
    _make_bill(
        db_session,
        bulletin="300-08",
        origin=BillOrigin.EXECUTIVE,
        origin_chamber=senate,
        entry_date=date(2026, 2, 1),
    )

    db_session.flush()

    rows = audit.collect_mocion_rows(db_session)
    bulletins = {r.bulletin for r in rows}

    assert bulletins == {"100-06", "200-07"}
    by_bulletin = {r.bulletin: r for r in rows}
    assert by_bulletin["100-06"].db_author_count == 1
    assert by_bulletin["200-07"].db_author_count == 0
    assert by_bulletin["100-06"].origin_chamber == ChamberType.SENATE
    assert by_bulletin["200-07"].origin_chamber == ChamberType.DEPUTIES


def test_run_reports_zero_author_and_reparse_surfaces_unmatched_name(
    db_session, capsys
):
    senate = _make_chamber(db_session, ChamberType.SENATE, 50)
    _make_legislator(db_session, full_name="Ada Lovelace")

    # Two mociones, both with zero DB authors — both qualify for reparse.
    _make_bill(
        db_session,
        bulletin="100-06",
        origin=BillOrigin.DEPUTIES,
        origin_chamber=senate,
        entry_date=date(2026, 3, 1),
        title="Moción A",
    )
    _make_bill(
        db_session,
        bulletin="200-07",
        origin=BillOrigin.DEPUTIES,
        origin_chamber=senate,
        entry_date=date(2026, 4, 1),
        title="Moción B",
    )
    db_session.flush()

    upstream = {
        # Ada is in the DB, so she matches; Grace is not, so she's unmatched.
        "100-06": {
            "authors": [
                {"legislator": "Ada Lovelace"},
                {"legislator": "Grace Hopper"},
            ]
        },
        # Both unmatched — Grace appears again so she should top the list.
        "200-07": {
            "authors": [
                {"legislator": "Grace Hopper"},
                {"legislator": "Margaret Hamilton"},
            ]
        },
    }

    def fake_fetcher(bulletin: str) -> dict[str, Any] | None:
        return upstream.get(bulletin)

    payload = audit.run(db_session, reparse=True, export_csv=None, fetcher=fake_fetcher)

    captured = capsys.readouterr().out
    assert "Zero-author mociones: 2" in captured
    assert "Grace Hopper" in captured
    # Grace appears twice, Margaret once — Grace should rank first.
    grace_index = captured.find("Grace Hopper")
    margaret_index = captured.find("Margaret Hamilton")
    assert grace_index != -1 and grace_index < margaret_index

    assert payload["total_mociones"] == 2
    assert payload["zero_author"] == 2
    assert payload["reparsed"] is True

    # Confirm Ada was matched (so she does NOT appear in unmatched names)
    assert "Ada Lovelace" not in captured.split("Top unmatched")[1]

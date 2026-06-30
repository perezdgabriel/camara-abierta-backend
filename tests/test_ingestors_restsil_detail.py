"""Tests for the restsil bill-detail dispatch path (ADR-0020).

Companion to the legacy wspublico tests in ``test_ingestors.py``. Each test
here pins ``settings.ingestor_bill_detail_source = "restsil"`` and asserts
that ``run_ingest_bills`` routes through ``_fetch_bill_details_restsil`` and
``BillParser.parse_restsil_detail`` instead of ``fetch_bills_parallel``.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings
from app.tasks import ingestors as ingestor_tasks

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _restsil_bill_detail_source(monkeypatch):
    monkeypatch.setattr(settings, "ingestor_bill_detail_source", "restsil")
    # Senate votes default is restsil; pin to keep these tests isolated from
    # any default change.
    monkeypatch.setattr(settings, "ingestor_senate_votes_source", "restsil")


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_restsil_discovery_carries_proy_id_into_detail_fetch(monkeypatch):
    """Happy path: restsil discovery + restsil detail.

    Discovery yields one bulletin with ``PROYID``. The fetcher must reuse
    that proy_id (no cross-source ``search_bills`` lookup), call
    ``afetch_bill_details``, and dispatch through ``parse_restsil_detail``.
    """
    dispatched: list[tuple[object, dict]] = []

    class FakeRestsilClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def iter_bills_desc(self, **filters):
            yield {
                "PROYID": 18872,
                "PROYNUMEROBOLETIN": "18216-05",
                "PROYFECHAINGRESO": "22/04/2026",
                "PROYORIGEN": "D",
                "CAMARA_ORIGEN": "C.Diputados",
                "PROYSUMA": "Para la reconstrucción nacional",
                "ETAPA": "Segundo trámite constitucional (Senado)",
                "SUBETAPA": "Boletín de indicaciones",
                "PROYINICIATIVA": 30,
                "PROYDESCINICIATIVA": "Mensaje",
                "PROYURGENCIA": "Suma",
                "AUTORES": "Ministerio de Hacienda",
                "ID_PROYECTO": 18872,
            }

        def search_bills(self, **kwargs):  # pragma: no cover — happy path skips this
            raise AssertionError(
                "search_bills must not be called when discovery already "
                "supplied proy_id"
            )

    seen_proy_ids: list[Any] = []

    async def fake_afetch_bill_details(proy_ids, *, concurrency=None):
        seen_proy_ids.extend(proy_ids)
        return [(pid, _load("restsil_bill_detail_18872.json")) for pid in proy_ids]

    async def fake_fetch_bill_details_parallel(bulletins):
        return [(bn, None) for bn in bulletins]

    monkeypatch.setattr(ingestor_tasks, "RestsilSenadoClient", FakeRestsilClient)
    monkeypatch.setattr(ingestor_tasks, "afetch_bill_details", fake_afetch_bill_details)
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bill_details_parallel", fake_fetch_bill_details_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, payload: dispatched.append((task, payload)),
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)
    monkeypatch.setattr(
        ingestor_tasks, "_mark_past_years_scanned", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(ingestor_tasks, "_should_scan_past_years", lambda _now: False)

    result = ingestor_tasks.run_ingest_bills(
        dry_run=False,
        source="restsil",
        since=datetime.date.today().isoformat(),
    )

    assert seen_proy_ids == [18872]
    assert result["source"] == "restsil"
    assert result["detail_source"] == "restsil"
    assert result["candidates"] == 1
    assert result["dispatched"] == 1
    assert len(dispatched) == 1
    task, payload = dispatched[0]
    assert task is ingestor_tasks.sync_bill
    assert payload["bulletin_number"] == "18216-05"
    assert payload["message_url"].startswith(
        "https://microservicio-documentos.senado.cl/"
    )
    assert payload["authors"] == [{"name": "Ministerio de Hacienda"}]


def test_single_bulletin_run_triggers_cross_source_proy_id_lookup(monkeypatch):
    """When discovery is bypassed (e.g. ``--bulletin``) the detail path
    must resolve proy_id via ``search_bills(boletin=X)``.

    Same shape applies to the cross-source combination (opendata discovery
    + restsil detail) where the discovery row also lacks ``PROYID``.
    """
    dispatched: list[tuple[object, dict]] = []
    search_calls: list[str] = []

    class FakeRestsilClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def iter_bills_desc(self, **filters):  # pragma: no cover
            raise AssertionError("discovery should be skipped for --bulletin")

        def search_bills(self, *, boletin, limit=1, **kwargs):
            search_calls.append(boletin)
            return {
                "data": [
                    {
                        "PROYID": 19090,
                        "PROYNUMEROBOLETIN": boletin,
                        "PROYFECHAINGRESO": "24/06/2026",
                        "PROYORIGEN": "S",
                        "PROYINICIATIVA": 31,
                        "AUTORES": "Sepúlveda Orbenes, Alejandra/ Velásquez Núñez, Esteban",
                    }
                ]
            }

    async def fake_afetch_bill_details(proy_ids, *, concurrency=None):
        return [(pid, _load("restsil_bill_detail_19090.json")) for pid in proy_ids]

    async def fake_fetch_bill_details_parallel(bulletins):
        return [(bn, None) for bn in bulletins]

    monkeypatch.setattr(ingestor_tasks, "RestsilSenadoClient", FakeRestsilClient)
    monkeypatch.setattr(ingestor_tasks, "afetch_bill_details", fake_afetch_bill_details)
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bill_details_parallel", fake_fetch_bill_details_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, payload: dispatched.append((task, payload)),
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)

    result = ingestor_tasks.run_ingest_bills(bulletin="18407-25", dry_run=False)

    assert search_calls == ["18407-25"]
    assert result["dispatched"] == 1
    assert len(dispatched) == 1
    _, payload = dispatched[0]
    assert payload["bulletin_number"] == "18407-25"
    assert payload["authors"] == [
        {"name": "Sepúlveda Orbenes, Alejandra"},
        {"name": "Velásquez Núñez, Esteban"},
    ]


def test_failed_detail_fetch_counts_as_error_and_does_not_dispatch(monkeypatch):
    dispatched: list[Any] = []

    class FakeRestsilClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def iter_bills_desc(self, **filters):
            yield {
                "PROYID": 99999,
                "PROYNUMEROBOLETIN": "9999-99",
                "PROYFECHAINGRESO": "01/01/2026",
                "PROYORIGEN": "S",
                "PROYINICIATIVA": 31,
            }

        def search_bills(self, **kwargs):  # pragma: no cover
            raise AssertionError("no cross-source lookup expected")

    async def fake_afetch_bill_details(proy_ids, *, concurrency=None):
        return [(pid, None) for pid in proy_ids]

    async def fake_fetch_bill_details_parallel(bulletins):
        return [(bn, None) for bn in bulletins]

    monkeypatch.setattr(ingestor_tasks, "RestsilSenadoClient", FakeRestsilClient)
    monkeypatch.setattr(ingestor_tasks, "afetch_bill_details", fake_afetch_bill_details)
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bill_details_parallel", fake_fetch_bill_details_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks, "_dispatch", lambda *_args: dispatched.append(_args)
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)
    monkeypatch.setattr(ingestor_tasks, "_should_scan_past_years", lambda _now: False)

    result = ingestor_tasks.run_ingest_bills(
        dry_run=False, source="restsil", since=datetime.date.today().isoformat()
    )

    assert dispatched == []
    assert result["dispatched"] == 0
    assert result["errors"] >= 1

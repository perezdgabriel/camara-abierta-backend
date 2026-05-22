import datetime
from contextlib import contextmanager

from app.tasks import ingestors as ingestor_tasks


def session_sequence(*dbs):
    queue = list(dbs)

    @contextmanager
    def _task_session():
        assert queue, "unexpected task_session() call"
        yield queue.pop(0)

    return _task_session


def test_run_ingest_bills_uses_senado_incremental_mode_for_explicit_since(monkeypatch):
    requested_since: list[datetime.date] = []
    fetched_bulletins: list[list[str]] = []

    class FakeSenadoClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_bills_by_date(self, since_date: datetime.date) -> list[str]:
            requested_since.append(since_date)
            return ["111-06", "111-06", "222-07"]

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        fetched_bulletins.append(bulletins)
        return [
            ("111-06", {"bulletin": "111-06", "title": "Uno"}),
            ("222-07", {"bulletin": "222-07", "title": "Dos"}),
        ]

    monkeypatch.setattr(ingestor_tasks, "SenadoClient", FakeSenadoClient)
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks.BillParser,
        "parse_bill",
        lambda raw: {"bulletin_number": raw["bulletin"]},
    )

    result = ingestor_tasks.run_ingest_bills(since="2026-05-01", dry_run=True)

    assert requested_since == [datetime.date(2026, 5, 1)]
    assert fetched_bulletins == [["111-06", "222-07"]]
    assert result["mode"] == "incremental"
    assert result["since"] == "2026-05-01"
    assert result["candidates"] == 2
    assert result["would_dispatch"] == 2


def test_run_ingest_bills_uses_ingestor_state_for_incremental_mode(monkeypatch):
    first_db = object()
    requested_since: list[datetime.date] = []

    class FakeSenadoClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_bills_by_date(self, since_date: datetime.date) -> list[str]:
            requested_since.append(since_date)
            return []

    monkeypatch.setattr(ingestor_tasks, "task_session", session_sequence(first_db))
    monkeypatch.setattr(
        ingestor_tasks,
        "_get_state",
        lambda db, entity_type, create=False: type(
            "State",
            (),
            {"last_sync_date": datetime.date(2026, 5, 20)},
        )(),
    )
    monkeypatch.setattr(ingestor_tasks, "SenadoClient", FakeSenadoClient)

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        return []

    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )

    result = ingestor_tasks.run_ingest_bills(dry_run=True)

    assert requested_since == [datetime.date(2026, 5, 20)]
    assert result["mode"] == "incremental"
    assert result["since"] == "2026-05-20"
    assert result["candidates"] == 0


def test_run_ingest_bills_falls_back_to_full_scan_without_state(monkeypatch):
    fetched_years: list[int] = []
    fetched_bulletins: list[list[str]] = []

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_mensajes_x_anno(self, year: int):
            fetched_years.append(year)
            return [{"bulletin_number": "333-06"}]

        def get_mociones_x_anno(self, year: int):
            return [{"bulletin_number": "333-06"}, {"bulletin_number": "444-06"}]

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        fetched_bulletins.append(bulletins)
        return [(bulletin, {"bulletin": bulletin}) for bulletin in bulletins]

    monkeypatch.setattr(ingestor_tasks, "task_session", session_sequence(object()))
    monkeypatch.setattr(
        ingestor_tasks,
        "_get_state",
        lambda db, entity_type, create=False: None,
    )
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks.BillParser,
        "parse_bill",
        lambda raw: {"bulletin_number": raw["bulletin"]},
    )
    monkeypatch.setattr(ingestor_tasks.settings, "ingestor_bills_start_year", 2026)

    result = ingestor_tasks.run_ingest_bills(dry_run=True)

    assert fetched_years == [2026]
    assert fetched_bulletins == [["333-06", "444-06"]]
    assert result["mode"] == "full_scan"
    assert result["since"] is None
    assert result["candidates"] == 2


def test_run_ingest_bills_falls_back_to_full_scan_when_state_lookup_fails(monkeypatch):
    fetched_bulletins: list[list[str]] = []

    class BrokenTaskSession:
        def __call__(self):
            raise RuntimeError("db unavailable")

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_mensajes_x_anno(self, year: int):
            return [{"bulletin_number": "555-06"}]

        def get_mociones_x_anno(self, year: int):
            return []

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        fetched_bulletins.append(bulletins)
        return [(bulletin, {"bulletin": bulletin}) for bulletin in bulletins]

    monkeypatch.setattr(ingestor_tasks, "task_session", BrokenTaskSession())
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks.BillParser,
        "parse_bill",
        lambda raw: {"bulletin_number": raw["bulletin"]},
    )
    monkeypatch.setattr(ingestor_tasks.settings, "ingestor_bills_start_year", 2026)

    result = ingestor_tasks.run_ingest_bills(dry_run=True)

    assert fetched_bulletins == [["555-06"]]
    assert result["mode"] == "full_scan"
    assert result["since"] is None
    assert result["would_dispatch"] == 1

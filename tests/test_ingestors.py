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

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_bill_detail(self, bulletin_number: str):
            return None

    monkeypatch.setattr(ingestor_tasks, "SenadoClient", FakeSenadoClient)
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
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

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

        def get_bill_detail(self, bulletin_number: str):
            return None

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
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

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

        def get_bill_detail(self, bulletin_number: str):
            return None

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
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

    result = ingestor_tasks.run_ingest_bills(dry_run=True)

    assert fetched_bulletins == [["555-06"]]
    assert result["mode"] == "full_scan"
    assert result["since"] is None
    assert result["would_dispatch"] == 1


def test_run_ingest_legislators_dispatches_both_sources_with_geography(monkeypatch):
    dispatched: list[tuple[object, dict]] = []

    class FakeSenadoWebClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_senators(self) -> list[dict]:
            return [
                {
                    "ID_PARLAMENTARIO": 42,
                    "NOMBRE": "Ada",
                    "APELLIDO_PATERNO": "Demo",
                    "APELLIDO_MATERNO": "Senadora",
                    "NOMBRE_COMPLETO": "Ada Demo Senadora",
                    "PARTIDO": "Partido Demo",
                    "CIRCUNSCRIPCION_ID": 7,
                    "REGION": "Valparaiso",
                    "EMAIL": "ada@example.com",
                    "FONO": "+56 2 1234 5678",
                    "SEXO": "1",
                    "SEXO_ETIQUETA": "Mujer",
                    "SLUG": "ada-demo-senadora-sen",
                }
            ]

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_diputados_periodo_actual(self) -> list[dict]:
            return [
                {
                    "id": 1254,
                    "first_name": "Ignacio",
                    "second_name": "",
                    "last_name_father": "Urcullú",
                    "last_name_mother": "Clèment-Lund",
                    "birth_date": "1976-03-07",
                    "gender": "Masculino",
                    "gender_code": "1",
                    "district_number": 8,
                    "militancias": [
                        {
                            "start_date": "2026-03-11",
                            "end_date": "2030-03-10",
                            "party_name": "Partido Republicano",
                            "party_alias": "PREP",
                        }
                    ],
                }
            ]

    monkeypatch.setattr(ingestor_tasks, "SenadoWebClient", FakeSenadoWebClient)
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, payload: dispatched.append((task, payload)),
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

    result = ingestor_tasks.run_ingest_legislators(dry_run=False)

    assert result == {"errors": 0, "dry_run": False, "dispatched": 2}
    assert dispatched[0][1]["bcn_id"] == "camara:1254"
    assert dispatched[0][1]["_party_name"] == "Partido Republicano"
    assert dispatched[0][1]["_district_number"] == 8
    assert dispatched[1][1]["bcn_id"] == "senado:42"
    assert dispatched[1][1]["_party_name"] == "Partido Demo"
    assert dispatched[1][1]["_circumscription_number"] == 7


def test_run_ingest_reference_data_dispatches_topics_only(monkeypatch):
    dispatched: list[tuple[object, dict]] = []

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_materias(self) -> list[dict]:
            return [{"name": "Transparencia", "source_id": 3}]

    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, payload: dispatched.append((task, payload)),
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

    result = ingestor_tasks.run_ingest_reference_data(dry_run=False)

    assert result == {"errors": 0, "dry_run": False, "dispatched": 1}
    assert dispatched == [
        (
            ingestor_tasks.sync_topic,
            {"name": "Transparencia", "source_id": 3},
        ),
    ]


def test_run_ingest_voting_sessions_uses_explicit_since_and_deduplicates_bulletins(
    monkeypatch,
):
    requested_since: list[datetime.date] = []
    fetched_vote_bulletins: list[str] = []

    class FakeSenadoClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_bills_by_date(self, since_date: datetime.date) -> list[str]:
            requested_since.append(since_date)
            return ["111-06", "111-06", "222-07"]

        def get_votes_by_bulletin(self, bulletin: str) -> list[dict]:
            fetched_vote_bulletins.append(bulletin)
            return [
                {"session": f"{bulletin}-1"},
                {"session": f"{bulletin}-2"},
            ]

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_bill_detail(self, bulletin_number: str):
            return None

    monkeypatch.setattr(ingestor_tasks, "SenadoClient", FakeSenadoClient)
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(
        ingestor_tasks.VoteParser,
        "parse_senate_vote",
        lambda raw_vote, bulletin: {
            "bcn_id": f"senado:vot:{bulletin}:{raw_vote['session']}",
        },
    )
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

    result = ingestor_tasks.run_ingest_voting_sessions(since="2026-05-01", dry_run=True)

    assert requested_since == [datetime.date(2026, 5, 1)]
    assert fetched_vote_bulletins == ["111-06", "222-07"]
    assert result["since"] == "2026-05-01"
    assert result["would_dispatch"] == 4


def test_run_ingest_voting_sessions_uses_ingestor_state_since(monkeypatch):
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

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_bill_detail(self, bulletin_number: str):
            return None

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
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )

    result = ingestor_tasks.run_ingest_voting_sessions(dry_run=True)

    assert requested_since == [datetime.date(2026, 5, 20)]
    assert result["since"] == "2026-05-20"
    assert result["would_dispatch"] == 0


def test_run_ingest_bills_merges_opendata_detail_before_dispatch(monkeypatch):
    dispatched: list[tuple[object, dict]] = []

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        assert bulletins == ["111-06"]
        return [("111-06", {"bulletin": "111-06", "title": "Uno"})]

    async def fake_fetch_bill_details_parallel(bulletins: list[str]):
        assert bulletins == ["111-06"]
        return [
            (
                "111-06",
                {
                    "sponsoring_ministries": [
                        {"id": 12, "name": "Ministerio de Hacienda"}
                    ],
                    "chamber_votes": [{"id": 88980}],
                },
            )
        ]

    async def fake_fetch_voting_details_parallel(voting_ids: list[int]):
        assert voting_ids == [88980]
        return [(88980, {"id": 88980, "individual_votes": [{"deputy_id": 803}]})]

    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bill_details_parallel", fake_fetch_bill_details_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "fetch_voting_details_parallel",
        fake_fetch_voting_details_parallel,
    )
    monkeypatch.setattr(
        ingestor_tasks.BillParser,
        "parse_bill",
        lambda raw: {"bulletin_number": raw["bulletin"]},
    )
    monkeypatch.setattr(
        ingestor_tasks.BillParser,
        "parse_opendata_enrichment",
        lambda raw: {
            "sponsoring_ministries": raw["sponsoring_ministries"],
            "_camara_votaciones": raw["chamber_votes"],
        },
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, payload: dispatched.append((task, payload)),
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

    result = ingestor_tasks.run_ingest_bills(bulletin="111-06", dry_run=False)

    assert result["dispatched"] == 1
    assert dispatched == [
        (
            ingestor_tasks.sync_bill,
            {
                "bulletin_number": "111-06",
                "sponsoring_ministries": [{"id": 12, "name": "Ministerio de Hacienda"}],
                "_camara_votaciones": [
                    {"id": 88980, "individual_votes": [{"deputy_id": 803}]}
                ],
            },
        )
    ]


def test_run_ingest_voting_sessions_dispatches_chamber_votes_from_opendata(
    monkeypatch,
):
    dispatched: list[tuple[object, dict, str]] = []

    async def fake_fetch_bill_details_parallel(bulletins: list[str]):
        assert bulletins == ["111-06"]
        return [("111-06", {"chamber_votes": [{"id": 88980}]})]

    async def fake_fetch_voting_details_parallel(voting_ids: list[int]):
        assert voting_ids == [88980]
        return [(88980, {"id": 88980, "individual_votes": [{"deputy_id": 803}]})]

    class FakeSenadoClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_bills_by_date(self, since_date: datetime.date) -> list[str]:
            return ["111-06"]

        def get_votes_by_bulletin(self, bulletin: str) -> list[dict]:
            return []

    monkeypatch.setattr(ingestor_tasks, "SenadoClient", FakeSenadoClient)
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bill_details_parallel", fake_fetch_bill_details_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "fetch_voting_details_parallel",
        fake_fetch_voting_details_parallel,
    )
    monkeypatch.setattr(
        ingestor_tasks.VoteParser,
        "parse_chamber_vote",
        lambda raw_vote, bulletin: {
            "bcn_id": f"camara:vot:{raw_vote['id']}",
            "individual_votes": raw_vote["individual_votes"],
            "bill_bulletin": bulletin,
        },
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, payload, bulletin: dispatched.append((task, payload, bulletin)),
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

    result = ingestor_tasks.run_ingest_voting_sessions(
        since="2026-05-01", dry_run=False
    )

    assert result["dispatched"] == 1
    assert dispatched == [
        (
            ingestor_tasks.sync_voting_session,
            {
                "bcn_id": "camara:vot:88980",
                "individual_votes": [{"deputy_id": 803}],
                "bill_bulletin": "111-06",
            },
            "111-06",
        )
    ]

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


def test_run_ingest_bills_scans_opendata_from_since_year_for_explicit_since(
    monkeypatch,
):
    scanned_years: list[int] = []
    fetched_bulletins: list[list[str]] = []

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_mensajes_x_anno(self, year: int):
            scanned_years.append(year)
            return [{"bulletin_number": "111-06"}, {"bulletin_number": "111-06"}]

        def get_mociones_x_anno(self, year: int):
            return [{"bulletin_number": "222-07"}]

        def get_bill_detail(self, bulletin_number: str):
            return None

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        fetched_bulletins.append(bulletins)
        return [
            ("111-06", {"bulletin": "111-06", "title": "Uno"}),
            ("222-07", {"bulletin": "222-07", "title": "Dos"}),
        ]

    async def fake_fetch_votes_parallel(bulletins: list[str]):
        return [(bulletin, []) for bulletin in bulletins]

    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_votes_parallel", fake_fetch_votes_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks.BillParser,
        "parse_bill",
        lambda raw: {"bulletin_number": raw["bulletin"]},
    )

    result = ingestor_tasks.run_ingest_bills(since="2026-05-01", dry_run=True)

    # --since is coarsened to its year: re-scan from that year through the current one.
    current_year = datetime.date.today().year
    assert scanned_years == list(range(2026, current_year + 1))
    assert fetched_bulletins == [["111-06", "222-07"]]
    assert result["mode"] == "incremental"
    assert result["since"] == "2026-05-01"
    assert result["candidates"] == 2
    assert result["would_dispatch"] == 2


def test_run_ingest_bills_uses_ingestor_state_for_incremental_mode(monkeypatch):
    first_db = object()
    scanned_years: list[int] = []

    class FakeOpenDataCamaraClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_mensajes_x_anno(self, year: int):
            scanned_years.append(year)
            return []

        def get_mociones_x_anno(self, year: int):
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
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        return []

    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )

    result = ingestor_tasks.run_ingest_bills(dry_run=True)

    # The last successful sync (2026-05-20) is coarsened to its year as the scan start.
    current_year = datetime.date.today().year
    assert scanned_years == list(range(2026, current_year + 1))
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

    async def fake_fetch_votes_parallel(bulletins: list[str]):
        return [(bulletin, []) for bulletin in bulletins]

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
        ingestor_tasks, "fetch_votes_parallel", fake_fetch_votes_parallel
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

    async def fake_fetch_votes_parallel(bulletins: list[str]):
        return [(bulletin, []) for bulletin in bulletins]

    monkeypatch.setattr(ingestor_tasks, "task_session", BrokenTaskSession())
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_votes_parallel", fake_fetch_votes_parallel
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
    dispatched: list[tuple[object, tuple]] = []

    class FakeSenadoWebClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_full_catalog(self) -> dict[int, dict]:
            return {
                42: {
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
            }

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

    class FakeBCNClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_active_appointments(self) -> list[dict]:
            return [
                {
                    "personUri": "http://datos.bcn.cl/recurso/persona/4558",
                    "appointmentUri": "http://datos.bcn.cl/recurso/persona/4558/nombramiento/2",
                    "cargoId": "2",
                    "idSenado": "42",
                    "idCamara": None,
                    "full_name": "Ada Demo Senadora",
                    "term_start": "2022-03-11",
                    "term_end": "2030-03-11",
                },
                {
                    "personUri": "http://datos.bcn.cl/recurso/persona/1254",
                    "appointmentUri": "http://datos.bcn.cl/recurso/persona/1254/nombramiento/1",
                    "cargoId": "1",
                    "idSenado": None,
                    "idCamara": "1254",
                    "full_name": "Ignacio Urcullú Clèment-Lund",
                    "term_start": "2026-03-11",
                    "term_end": "2030-03-11",
                },
            ]

    profiles_by_uri = {
        "http://datos.bcn.cl/recurso/persona/4558": {
            "personUri": "http://datos.bcn.cl/recurso/persona/4558",
            "full_name": "Ada Demo Senadora",
            "profession": "Abogada",
            "twitter": "ada_demo",
            "bcn_wiki_url": "https://www.bcn.cl/historiapolitica/Demo",
            "gender": "mujer",
        },
        "http://datos.bcn.cl/recurso/persona/1254": {
            "personUri": "http://datos.bcn.cl/recurso/persona/1254",
            "full_name": "Ignacio Urcullú Clèment-Lund",
            "profession": "Ingeniero",
            "twitter": "iurcullu",
            "gender": "hombre",
        },
    }
    appointments_by_uri = {
        "http://datos.bcn.cl/recurso/persona/4558": [
            {
                "appointmentUri": "http://datos.bcn.cl/recurso/persona/4558/nombramiento/2",
                "cargoId": "2",
                "term_start": "2022-03-11",
                "term_end": "2030-03-11",
            }
        ],
        "http://datos.bcn.cl/recurso/persona/1254": [
            {
                "appointmentUri": "http://datos.bcn.cl/recurso/persona/1254/nombramiento/1",
                "cargoId": "1",
                "term_start": "2026-03-11",
                "term_end": "2030-03-11",
            }
        ],
    }

    async def fake_fetch_profiles(uris):
        return {uri: profiles_by_uri.get(uri) for uri in uris}

    async def fake_fetch_appointments(uris):
        return {uri: appointments_by_uri.get(uri, []) for uri in uris}

    monkeypatch.setattr(ingestor_tasks, "SenadoWebClient", FakeSenadoWebClient)
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(ingestor_tasks, "BCNClient", FakeBCNClient)
    monkeypatch.setattr(
        ingestor_tasks, "fetch_person_profiles_parallel", fake_fetch_profiles
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "fetch_person_appointments_parallel",
        fake_fetch_appointments,
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, *args: dispatched.append((task, args)),
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)
    monkeypatch.setattr(ingestor_tasks.time, "sleep", lambda _: None)

    result = ingestor_tasks.run_ingest_legislators(dry_run=False)

    # 2 sync_legislator (deputy + senator) + 2 BCN enrichment + 2 appointment.
    assert result == {"errors": 0, "dry_run": False, "dispatched": 6}

    by_task: dict[str, list[tuple]] = {}
    for task, args in dispatched:
        by_task.setdefault(task.name, []).append(args)

    legislator_dispatches = by_task[ingestor_tasks.sync_legislator.name]
    enrichment_dispatches = by_task[ingestor_tasks.sync_legislator_bcn_enrichment.name]
    appointment_dispatches = by_task[ingestor_tasks.sync_parliamentary_appointment.name]

    deputy_payload = legislator_dispatches[0][0]
    senator_payload = legislator_dispatches[1][0]
    assert deputy_payload["bcn_id"] == "camara:1254"
    assert deputy_payload["_party_name"] == "Partido Republicano"
    assert deputy_payload["_district_number"] == 8
    assert senator_payload["bcn_id"] == "senado:42"
    assert senator_payload["_party_name"] == "Partido Demo"
    assert senator_payload["_circumscription_number"] == 7
    assert senator_payload["bcn_uri"].endswith("/persona/4558")

    enrichment_by_bcn_id = {args[0]: args[1] for args in enrichment_dispatches}
    assert enrichment_by_bcn_id["camara:1254"]["profession"] == "Ingeniero"
    assert enrichment_by_bcn_id["camara:1254"]["twitter_handle"] == "iurcullu"
    assert enrichment_by_bcn_id["senado:42"]["gender"] == "F"
    assert enrichment_by_bcn_id["senado:42"]["bcn_wiki_url"].startswith(
        "https://www.bcn.cl/historiapolitica/"
    )

    appointment_by_bcn_id = {args[0]: args[1] for args in appointment_dispatches}
    assert appointment_by_bcn_id["senado:42"]["chamber_type"] == "senate"
    assert appointment_by_bcn_id["senado:42"]["end_date"] == "2030-03-11"
    assert appointment_by_bcn_id["camara:1254"]["chamber_type"] == "deputies"


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

    async def fake_fetch_votes_parallel(bulletins: list[str]):
        assert bulletins == ["111-06"]
        return [("111-06", [{"session": "26/374", "votes_for": 31}])]

    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_votes_parallel", fake_fetch_votes_parallel
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
                "_votaciones": [{"session": "26/374", "votes_for": 31}],
                "sponsoring_ministries": [{"id": 12, "name": "Ministerio de Hacienda"}],
                "_camara_votaciones": [
                    {"id": 88980, "individual_votes": [{"deputy_id": 803}]}
                ],
            },
        )
    ]


def test_run_ingest_bills_sources_senate_votes_from_votaciones_endpoint(monkeypatch):
    # The embedded <votacion> in tramitacion.php is often empty (parse_bill yields
    # _votaciones=[]); the dedicated votaciones.php fetch must still supply the
    # Senate votes that flow on to sync_voting_session (ADR-0008).
    dispatched: list[tuple[object, dict]] = []

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        return [("15767-07", {"bulletin": "15767-07"})]

    async def fake_fetch_votes_parallel(bulletins: list[str]):
        assert bulletins == ["15767-07"]
        return [("15767-07", [{"session": "26/374", "votes_for": 31}])]

    async def fake_fetch_bill_details_parallel(bulletins: list[str]):
        return [("15767-07", None)]

    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_votes_parallel", fake_fetch_votes_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bill_details_parallel", fake_fetch_bill_details_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks.BillParser,
        "parse_bill",
        lambda raw: {"bulletin_number": raw["bulletin"], "_votaciones": []},
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, payload: dispatched.append((task, payload)),
    )
    monkeypatch.setattr(ingestor_tasks, "_mark_synced", lambda entity_type: None)

    result = ingestor_tasks.run_ingest_bills(bulletin="15767-07", dry_run=False)

    assert result["dispatched"] == 1
    assert dispatched == [
        (
            ingestor_tasks.sync_bill,
            {
                "bulletin_number": "15767-07",
                "_votaciones": [{"session": "26/374", "votes_for": 31}],
            },
        )
    ]

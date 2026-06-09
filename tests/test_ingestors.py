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

    # ``source="opendata"`` selects the legacy ADR-0008 year-scan; the
    # restsil branch (ADR-0009) is covered by a separate test below.
    result = ingestor_tasks.run_ingest_bills(
        since="2026-05-01", dry_run=True, source="opendata"
    )

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

    result = ingestor_tasks.run_ingest_bills(dry_run=True, source="opendata")

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

    result = ingestor_tasks.run_ingest_bills(dry_run=True, source="opendata")

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

    result = ingestor_tasks.run_ingest_bills(dry_run=True, source="opendata")

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
    # In failover mode senate votes still ride on bill ingest via
    # ``fetch_votes_parallel``; in the new restsil-primary mode the dedicated
    # senate-votes task owns them, so we'd see _votaciones=[] here.
    monkeypatch.setattr(
        ingestor_tasks.settings, "ingestor_senate_votes_source", "wspublico"
    )

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
    # Failover behavior: with ``ingestor_senate_votes_source="wspublico"`` the
    # dedicated restsil-driven senate-votes task no-ops and the per-bulletin
    # votaciones.php fetch supplies the Senate votes that flow on to
    # ``sync_voting_session`` (ADR-0008 path preserved by ADR-0009).
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
    monkeypatch.setattr(
        ingestor_tasks.settings, "ingestor_senate_votes_source", "wspublico"
    )

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


# --- restsil (ADR-0009) ingestion-task tests --------------------------------


def test_run_ingest_bills_restsil_current_year_then_dispatches_via_tramitacion(
    monkeypatch,
):
    # Restsil discovery walks the desc-paged bill list, then per-bulletin
    # detail is still fetched from wspublico tramitacion.php. Senate votes
    # default to the dedicated task (so _votaciones is forced empty here).
    dispatched: list[tuple[object, dict]] = []

    class FakeRestsilClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def iter_bills_desc(self, **filters):
            assert filters.get("fecha_desde") == datetime.date.today().year
            assert filters.get("fecha_hasta") == datetime.date.today().year
            yield {
                "PROYNUMEROBOLETIN": "18300-04",
                "PROYFECHAINGRESO": "03/06/2026",
                "PROYORIGEN": "S",
                "PROYINICIATIVA": 31,
                "PROYSUMA": "Demo",
            }

    async def fake_fetch_bills_parallel(bulletins: list[str]):
        assert bulletins == ["18300-04"]
        return [("18300-04", {"bulletin": "18300-04"})]

    async def fake_fetch_bill_details_parallel(bulletins: list[str]):
        return [("18300-04", None)]

    monkeypatch.setattr(ingestor_tasks, "RestsilSenadoClient", FakeRestsilClient)
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bills_parallel", fake_fetch_bills_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks, "fetch_bill_details_parallel", fake_fetch_bill_details_parallel
    )
    monkeypatch.setattr(
        ingestor_tasks.BillParser,
        "parse_bill",
        lambda raw: {"bulletin_number": raw["bulletin"]},
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
    # Past-years sweep is daily-gated; skip it for the current-year-only path
    # this test exercises.
    monkeypatch.setattr(ingestor_tasks, "_should_scan_past_years", lambda _now: False)

    result = ingestor_tasks.run_ingest_bills(
        dry_run=False, source="restsil", since=datetime.date.today().isoformat()
    )

    assert result["source"] == "restsil"
    assert result["candidates"] == 1
    assert dispatched == [
        (
            ingestor_tasks.sync_bill,
            {"bulletin_number": "18300-04", "_votaciones": []},
        )
    ]


def test_run_ingest_senate_votes_advances_watermark_and_dispatches(monkeypatch):
    dispatched: list[tuple[object, tuple]] = []

    rows = [
        {
            "ID_VOTACION": 11110,
            "BOLETIN": "15767-29",
            "TEMA": "Discusión en general",
            "SI": 31,
            "NO": 0,
            "ABS": 0,
            "PAREO": 0,
            "FECHA_VOTACION": "03-06-2026 18:45:02",
            "QUORUM": "Mayoría simple",
            "NUMERO_SESION": 26,
            "VOTACIONES": {"SI": 0, "NO": 0, "ABSTENCION": 0, "PAREO": 0},
        },
        {
            "ID_VOTACION": 11107,
            "BOLETIN": "15975-25",
            "TEMA": "Particular",
            "SI": 21,
            "NO": 22,
            "ABS": 0,
            "PAREO": 0,
            "FECHA_VOTACION": "03-06-2026 18:32:16",
            "QUORUM": "Mayoría simple",
            "NUMERO_SESION": 26,
            "VOTACIONES": {"SI": 0, "NO": 0, "ABSTENCION": 0, "PAREO": 0},
        },
    ]
    watermark_after: list[int] = []

    class FakeRestsilClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def iter_votes_desc(self, *, stop_at_id, max_pages, boletin):
            # Cold-start (no watermark) → yield all rows.
            assert stop_at_id is None
            assert boletin is None
            for row in rows:
                yield row

    monkeypatch.setattr(ingestor_tasks, "RestsilSenadoClient", FakeRestsilClient)
    monkeypatch.setattr(ingestor_tasks, "_get_senate_votes_watermark", lambda: None)
    monkeypatch.setattr(
        ingestor_tasks,
        "_set_senate_votes_watermark",
        lambda new_max: watermark_after.append(new_max),
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, *args: dispatched.append((task, args)),
    )

    result = ingestor_tasks.run_ingest_senate_votes(dry_run=False, source="restsil")

    assert result["source"] == "restsil"
    assert result["candidates"] == 2
    assert result["dispatched"] == 2
    assert result["mode"] == "cold_start"
    assert watermark_after == [11110]
    # Both votes were dispatched with their bulletin attached for FK linkage.
    assert {args[1] for _, args in dispatched} == {"15767-29", "15975-25"}


def test_run_ingest_senate_votes_skips_when_source_is_wspublico(monkeypatch):
    # When the senate-votes source flag is flipped to wspublico the dedicated
    # task should no-op (failover puts vote capture back on the bills ingest).
    called = {"restsil_constructed": False}

    class FakeRestsilClient:
        def __init__(self, *args, **kwargs):
            called["restsil_constructed"] = True

    monkeypatch.setattr(ingestor_tasks, "RestsilSenadoClient", FakeRestsilClient)

    result = ingestor_tasks.run_ingest_senate_votes(source="wspublico")

    assert result["source"] == "wspublico"
    assert result["mode"] == "skip"
    assert called["restsil_constructed"] is False


# --- iter_votes_desc parallel-paging tests ---------------------------------


def _make_restsil_client(monkeypatch):
    """Construct a RestsilSenadoClient with the apikey guard satisfied.

    The sync client refuses to instantiate without
    ``settings.ingestor_restsil_api_key`` — these tests don't touch the
    real upstream but they do go through ``__init__``.
    """
    from app.core.config import settings as core_settings
    from app.ingestors.clients import restsil_senado

    monkeypatch.setattr(core_settings, "ingestor_restsil_api_key", "test-key")
    return restsil_senado, restsil_senado.RestsilSenadoClient()


def test_iter_votes_desc_does_not_invoke_async_helper_on_single_page(monkeypatch):
    # Page 1 covers ``total`` rows → there is nothing left to fan out, so
    # ``afetch_votes_pages`` must not be touched. We monkeypatch it to a
    # sentinel that raises if invoked.
    restsil_senado, client = _make_restsil_client(monkeypatch)

    def boom(*args, **kwargs):
        raise AssertionError("afetch_votes_pages should not be called")

    monkeypatch.setattr(restsil_senado, "afetch_votes_pages", boom)
    monkeypatch.setattr(
        client,
        "search_votes",
        lambda **kwargs: {
            "total": 2,
            "data": [{"ID_VOTACION": 222}, {"ID_VOTACION": 111}],
        },
    )

    rows = list(client.iter_votes_desc(page_size=100))

    assert [r["ID_VOTACION"] for r in rows] == [222, 111]


def test_iter_votes_desc_short_first_page_does_not_invoke_async_helper(monkeypatch):
    # ``len(rows) < limit`` is the "last page" signal — also no fan-out.
    restsil_senado, client = _make_restsil_client(monkeypatch)

    monkeypatch.setattr(
        restsil_senado,
        "afetch_votes_pages",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not be called")
        ),
    )
    monkeypatch.setattr(
        client,
        "search_votes",
        lambda **kwargs: {
            "total": 25,  # implies later pages, but page 1 came back partial
            "data": [{"ID_VOTACION": i} for i in range(5, 0, -1)],
        },
    )

    rows = list(client.iter_votes_desc(page_size=100))

    assert [r["ID_VOTACION"] for r in rows] == [5, 4, 3, 2, 1]


def test_iter_votes_desc_watermark_hit_on_first_page_skips_async_helper(monkeypatch):
    # Cutoff fires inside page 1 → also no fan-out.
    restsil_senado, client = _make_restsil_client(monkeypatch)

    monkeypatch.setattr(
        restsil_senado,
        "afetch_votes_pages",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not be called")
        ),
    )
    monkeypatch.setattr(
        client,
        "search_votes",
        lambda **kwargs: {
            "total": 999,  # plenty of later pages, but we won't ask
            "data": [
                {"ID_VOTACION": 100},
                {"ID_VOTACION": 99},
                {"ID_VOTACION": 50},  # watermark
                {"ID_VOTACION": 49},
            ],
        },
    )

    rows = list(client.iter_votes_desc(page_size=100, stop_at_id=50))

    assert [r["ID_VOTACION"] for r in rows] == [100, 99]


def test_iter_votes_desc_fans_out_remaining_pages_in_desc_order(monkeypatch):
    # Multi-page result: page 1 sequential, remaining via afetch_votes_pages.
    # Verify rows come out in correct desc order across the boundary.
    restsil_senado, client = _make_restsil_client(monkeypatch)

    page1 = {
        "total": 250,
        "data": [{"ID_VOTACION": i} for i in range(250, 150, -1)],  # 250..151
    }
    page2 = {"data": [{"ID_VOTACION": i} for i in range(150, 50, -1)]}  # 150..51
    page3 = {"data": [{"ID_VOTACION": i} for i in range(50, 0, -1)]}  # 50..1

    captured_offsets: list[list[int]] = []
    captured_page_size: list[int] = []

    async def fake_afetch(offsets, *, page_size, boletin=None, concurrency=None):
        captured_offsets.append(list(offsets))
        captured_page_size.append(page_size)
        # Caller expects ``(offset, envelope)`` pairs in input order.
        envelopes = {100: page2, 200: page3}
        return [(o, envelopes.get(o)) for o in offsets]

    monkeypatch.setattr(restsil_senado, "afetch_votes_pages", fake_afetch)
    monkeypatch.setattr(client, "search_votes", lambda **kwargs: page1)

    rows = list(client.iter_votes_desc(page_size=100))

    assert captured_offsets == [[100, 200]]
    assert captured_page_size == [100]
    # 250 rows in strict desc order, no duplicates, no skips.
    ids = [r["ID_VOTACION"] for r in rows]
    assert ids == list(range(250, 0, -1))
    assert len(ids) == 250


def test_iter_votes_desc_applies_watermark_cutoff_inside_parallel_batch(monkeypatch):
    # Even after fan-out the watermark must cut the stream cleanly.
    restsil_senado, client = _make_restsil_client(monkeypatch)

    page1 = {
        "total": 250,
        "data": [{"ID_VOTACION": i} for i in range(250, 150, -1)],
    }
    page2 = {"data": [{"ID_VOTACION": i} for i in range(150, 50, -1)]}
    page3 = {"data": [{"ID_VOTACION": i} for i in range(50, 0, -1)]}

    async def fake_afetch(offsets, *, page_size, boletin=None, concurrency=None):
        envelopes = {100: page2, 200: page3}
        return [(o, envelopes.get(o)) for o in offsets]

    monkeypatch.setattr(restsil_senado, "afetch_votes_pages", fake_afetch)
    monkeypatch.setattr(client, "search_votes", lambda **kwargs: page1)

    rows = list(client.iter_votes_desc(page_size=100, stop_at_id=120))
    ids = [r["ID_VOTACION"] for r in rows]

    # Cutoff fires at 120 (exclusive — watermark row not yielded).
    assert ids[0] == 250
    assert ids[-1] == 121
    assert 120 not in ids
    assert len(ids) == 250 - 120


def test_iter_bills_desc_fans_out_remaining_pages_in_desc_order(monkeypatch):
    restsil_senado, client = _make_restsil_client(monkeypatch)

    page1 = {
        "total": 250,
        "data": [{"PROYNUMEROBOLETIN": f"B{i}"} for i in range(250, 150, -1)],
    }
    page2 = {"data": [{"PROYNUMEROBOLETIN": f"B{i}"} for i in range(150, 50, -1)]}
    page3 = {"data": [{"PROYNUMEROBOLETIN": f"B{i}"} for i in range(50, 0, -1)]}

    captured = {}

    async def fake_afetch(offsets, *, page_size, filters=None, concurrency=None):
        captured["offsets"] = list(offsets)
        captured["filters"] = filters
        envelopes = {100: page2, 200: page3}
        return [(o, envelopes.get(o)) for o in offsets]

    monkeypatch.setattr(restsil_senado, "afetch_bills_pages", fake_afetch)
    monkeypatch.setattr(client, "search_bills", lambda **kwargs: page1)

    rows = list(
        client.iter_bills_desc(
            page_size=100, fecha_desde=2024, fecha_hasta=2025, estado="T"
        )
    )

    assert captured["offsets"] == [100, 200]
    # Filter dict propagated to the async helper with Nones stripped.
    assert captured["filters"] == {
        "fecha_desde": 2024,
        "fecha_hasta": 2025,
        "estado": "T",
    }
    bulletins = [r["PROYNUMEROBOLETIN"] for r in rows]
    assert bulletins == [f"B{i}" for i in range(250, 0, -1)]


def test_iter_votes_desc_respects_max_pages_cap_when_fanning_out(monkeypatch):
    # max_pages=3 (combined) → page 1 sequential + 2 parallel offsets only,
    # even though ``total`` implies many more pages.
    restsil_senado, client = _make_restsil_client(monkeypatch)

    page1 = {
        "total": 10_000,
        "data": [{"ID_VOTACION": i} for i in range(10_000, 9_900, -1)],
    }
    captured_offsets: list[list[int]] = []

    async def fake_afetch(offsets, *, page_size, boletin=None, concurrency=None):
        captured_offsets.append(list(offsets))
        return [(o, {"data": [{"ID_VOTACION": -o}]}) for o in offsets]

    monkeypatch.setattr(restsil_senado, "afetch_votes_pages", fake_afetch)
    monkeypatch.setattr(client, "search_votes", lambda **kwargs: page1)

    list(client.iter_votes_desc(page_size=100, max_pages=3))

    # cap=3 ⇒ 1 sequential + 2 parallel = 3 total.
    assert captured_offsets == [[100, 200]]

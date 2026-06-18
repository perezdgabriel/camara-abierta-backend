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

    # ``source="opendata"`` selects the legacy year-scan failover; the
    # restsil branch (ADR-0013 default) is covered by a separate test below.
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


class _FakeSenadoWebClient:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get_historical_catalog(self) -> list[dict]:
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
                "IMAGEN_450": "https://cdn.senado.cl/ada_450.jpg",
                "IMAGEN_120": "https://cdn.senado.cl/ada_120.jpg",
                "PERIODOS": [
                    {"CAMARA": "S", "DESDE": "2026", "HASTA": "2030", "VIGENTE": 1},
                ],
            }
        ]


class _FakeOpenDataCamaraClient:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get_all_diputados(self) -> list[dict]:
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


class _FakeBCNRestClient:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get_active_parliamentarians(self) -> list[dict]:
        return [
            {
                "bcn_id": 4558,
                "bcn_uri": "http://datos.bcn.cl/recurso/persona/4558",
                "id_en_camara_de_origen": 42,
                "nombres": "Ada",
                "apellido_paterno": "Demo",
                "apellido_materno": "Senadora",
                "nombre_completo": "Ada Demo Senadora",
                "fecha_nacimiento": "1970-04-15",
                "email": "ada@example.com",
                "id_wiki": "Ada_Demo_Senadora",
                "camara_id": 261,
                "partido_nombre": "Partido Demo",
                "partido_acronimo": "PD",
                "division_tipo": "Circunscripcion",
                "division_id": 7,
                "division_descripcion": "Circunscripción VII Demo",
                "region_nombre": "Valparaiso",
            },
            {
                "bcn_id": 1254,
                "bcn_uri": "http://datos.bcn.cl/recurso/persona/1254",
                "id_en_camara_de_origen": 1254,
                "nombres": "Ignacio",
                "apellido_paterno": "Urcullú",
                "apellido_materno": "Clèment-Lund",
                "nombre_completo": "Ignacio Urcullú Clèment-Lund",
                "fecha_nacimiento": "1976-03-07",
                "email": "ignacio@example.com",
                "id_wiki": "Ignacio_Urcullú_Clèment-Lund",
                "camara_id": 288,
                "partido_nombre": "Partido Republicano de Chile",
                "partido_acronimo": "PREP",
                "division_tipo": "Distrito",
                "division_id": 4321,
                "division_descripcion": "Distrito N° 8",
            },
        ]


_PROFILES_BY_URI = {
    "http://datos.bcn.cl/recurso/persona/4558": {
        "personUri": "http://datos.bcn.cl/recurso/persona/4558",
        "full_name": "Ada Demo Senadora",
        "profession": "Abogada",
        "twitter": "ada_demo",
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
_APPOINTMENTS_BY_URI = {
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


def _wire_legislator_ingest_mocks(monkeypatch, dispatched, *, sparql_raises=False):
    monkeypatch.setattr(ingestor_tasks, "SenadoWebClient", _FakeSenadoWebClient)
    monkeypatch.setattr(
        ingestor_tasks, "OpenDataCamaraClient", _FakeOpenDataCamaraClient
    )
    monkeypatch.setattr(ingestor_tasks, "BCNRestClient", _FakeBCNRestClient)

    async def fake_fetch_profiles(uris):
        if sparql_raises:
            raise RuntimeError("BCN SPARQL profile fetch failed")
        return {uri: _PROFILES_BY_URI.get(uri) for uri in uris}

    async def fake_fetch_appointments(uris):
        if sparql_raises:
            raise RuntimeError("BCN SPARQL appointments fetch failed")
        return {uri: _APPOINTMENTS_BY_URI.get(uri, []) for uri in uris}

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


def _group_by_task(dispatched):
    by_task: dict[str, list[tuple]] = {}
    for task, args in dispatched:
        by_task.setdefault(task.name, []).append(args)
    return by_task


def test_run_ingest_legislators_dispatches_historical_rosters_and_enrichment(
    monkeypatch,
):
    """Ingest dispatches one ``sync_legislator`` per historical row, plus
    chamber-keyed BCN REST enrichments. See ADR-0015 for the cutover from the
    current-only path to full historical."""
    dispatched: list[tuple[object, tuple]] = []
    _wire_legislator_ingest_mocks(monkeypatch, dispatched)

    result = ingestor_tasks.run_ingest_legislators(dry_run=False)

    # 1 historical deputy + 1 historical senator + 2 BCN REST enrichments = 4.
    assert result == {"errors": 0, "dry_run": False, "dispatched": 4}

    by_task = _group_by_task(dispatched)
    legislator_dispatches = by_task[ingestor_tasks.sync_legislator.name]
    enrichment_dispatches = by_task[ingestor_tasks.sync_legislator_bcn_enrichment.name]
    # SPARQL passes (profile + appointments) moved to the out-of-band command.
    assert ingestor_tasks.sync_parliamentary_appointment.name not in by_task

    payloads_by_external_id = {
        args[0]["source_external_id"]: args[0] for args in legislator_dispatches
    }
    senator_payload = payloads_by_external_id["42"]
    deputy_payload = payloads_by_external_id["1254"]

    # Senate seed carries per-PERIODO terms; current period gets the PARTIDO.
    senate_term = senator_payload["terms"][0]
    assert senate_term["chamber_external_id"] == "senado:42"
    assert senate_term["party_name"] == "Partido Demo"
    assert senate_term["party_source"] == "senado_abbreviation"
    assert senator_payload["gender"] == "F"
    assert senator_payload["photo_url"].endswith("ada_450.jpg")

    # Deputy seed carries one militancia → one term with camara: bridge.
    deputy_term = deputy_payload["terms"][0]
    assert deputy_term["chamber_external_id"] == "camara:1254"
    assert deputy_term["party_name"] == "Partido Republicano"
    assert deputy_term["party_source"] == "opendata"

    # BCN REST enrichment is keyed by the chamber bridge (used to look up the
    # legislator via LegislatorTerm in the write service).
    enrichment_by_bridge = {
        bridge: payload for bridge, payload in enrichment_dispatches
    }
    senator_enrichment = enrichment_by_bridge["senado:42"]
    assert senator_enrichment["bcn_uri"].endswith("/persona/4558")
    assert senator_enrichment["bcn_wiki_url"].endswith("/Ada_Demo_Senadora")
    assert "profession" not in senator_enrichment
    assert "twitter_handle" not in senator_enrichment


def test_run_ingest_bcn_sparql_enrichment_dispatches_profile_and_appointments(
    monkeypatch,
):
    """SPARQL profile + appointment passes key by ``bcn_uri`` (the BCN person
    URI is the cross-chamber identity post ADR-0015)."""
    dispatched: list[tuple[object, tuple]] = []
    _wire_legislator_ingest_mocks(monkeypatch, dispatched)

    result = ingestor_tasks.run_ingest_bcn_sparql_enrichment(dry_run=False)

    # 2 SPARQL profile enrichments + 2 appointment dispatches = 4.
    assert result == {"errors": 0, "dry_run": False, "dispatched": 4}

    by_task = _group_by_task(dispatched)
    enrichment_dispatches = by_task[ingestor_tasks.sync_legislator_bcn_enrichment.name]
    appointment_dispatches = by_task[ingestor_tasks.sync_parliamentary_appointment.name]
    # The roster-write task is not dispatched by the SPARQL command.
    assert ingestor_tasks.sync_legislator.name not in by_task

    enrichment_by_uri = {key: payload for key, payload in enrichment_dispatches}
    senator_uri = "http://datos.bcn.cl/recurso/persona/4558"
    deputy_uri = "http://datos.bcn.cl/recurso/persona/1254"
    assert enrichment_by_uri[senator_uri]["profession"] == "Abogada"
    assert enrichment_by_uri[senator_uri]["twitter_handle"] == "ada_demo"
    assert enrichment_by_uri[senator_uri]["gender"] == "F"
    assert enrichment_by_uri[deputy_uri]["profession"] == "Ingeniero"

    appointment_by_uri = {args[0]: args[1] for args in appointment_dispatches}
    assert appointment_by_uri[senator_uri]["chamber_type"] == "senate"
    assert appointment_by_uri[senator_uri]["end_date"] == "2030-03-11"
    assert appointment_by_uri[deputy_uri]["chamber_type"] == "deputies"


def test_run_ingest_bcn_sparql_enrichment_tolerates_sparql_outage(monkeypatch):
    dispatched: list[tuple[object, tuple]] = []
    _wire_legislator_ingest_mocks(monkeypatch, dispatched, sparql_raises=True)

    result = ingestor_tasks.run_ingest_bcn_sparql_enrichment(dry_run=False)

    # No dispatches — both SPARQL passes raised — but no errors counted either,
    # the function degrades to a no-op rather than failing.
    assert result == {"errors": 0, "dry_run": False, "dispatched": 0}
    assert dispatched == []


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
    # ``sync_voting_session`` (legacy embedded path preserved per ADR-0013).
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


# --- restsil (ADR-0013) ingestion-task tests --------------------------------


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


def test_run_ingest_senate_votes_targeted_bulletin_ignores_watermark(monkeypatch):
    # Regression: targeted ``--bulletin`` recovery used to pass the global
    # watermark to ``iter_votes_desc``, which stopped the walk on the first
    # row because the target IDs (historical bulletins) are well below the
    # watermark. Operator-driven recovery must ignore the watermark.
    captured_stop_at_id: list[int | None] = []

    class FakeRestsilClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def iter_votes_desc(self, *, stop_at_id, max_pages, boletin):
            captured_stop_at_id.append(stop_at_id)
            assert boletin == "7011-07"
            return iter([])

    monkeypatch.setattr(ingestor_tasks, "RestsilSenadoClient", FakeRestsilClient)
    monkeypatch.setattr(ingestor_tasks, "_get_senate_votes_watermark", lambda: 11129)

    result = ingestor_tasks.run_ingest_senate_votes(
        bulletin="7011-07", source="restsil"
    )

    assert captured_stop_at_id == [None]
    assert result["mode"] == "single_bulletin"


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


# --- Chamber votes — bulk OpenData ingest (ADR-0013) ----------------------


def test_parse_dt_with_time_preserves_chile_wall_clock():
    from app.ingestors.clients.opendata_camara import OpenDataCamaraClient

    client = OpenDataCamaraClient()
    # Upstream ``Fecha`` is naive Chile local with HH:MM:SS — must round-trip
    # without losing the time (the legacy ``_parse_dt`` stripped it, which
    # caused voting_date to be stored as UTC midnight and rendered ~4h
    # earlier in the admin panel).
    assert client._parse_dt_with_time("2026-06-10T13:16:55") == "2026-06-10T13:16:55"
    # Space-separated variant.
    assert client._parse_dt_with_time("2026-06-10 13:16:55") == "2026-06-10T13:16:55"
    # Falls back to date-only when no time is present.
    assert client._parse_dt_with_time("2026-06-10") == "2026-06-10"
    assert client._parse_dt_with_time("") is None
    assert client._parse_dt_with_time(None) is None  # type: ignore[arg-type]


def test_parse_bulletin_from_description():
    from app.ingestors.clients.opendata_camara import (
        parse_bulletin_from_description,
    )

    assert parse_bulletin_from_description("Boletín N° 15936-18") == "15936-18"
    assert parse_bulletin_from_description("Boletín N°15936-18") == "15936-18"
    assert parse_bulletin_from_description("Boletin N 15936-18") is None
    # Joint-bulletin votes link to the first parsed bulletin.
    assert (
        parse_bulletin_from_description("Boletines N° 15936-18, 15937-18") == "15936-18"
    )
    # Free-text procedural votes don't carry a bulletin and are out of scope.
    assert parse_bulletin_from_description("Cuenta de la sesión") is None
    assert parse_bulletin_from_description(None) is None
    assert parse_bulletin_from_description("") is None


def test_run_ingest_chamber_votes_advances_watermark_and_dispatches(monkeypatch):
    dispatched: list[tuple[object, tuple]] = []
    watermark_after: list[int] = []
    triggered_bill_ingests: list[str] = []

    bulk_rows = [
        {
            "id": 89113,
            "description": "Boletín N° 15936-18",
            "date": "2026-06-10",
            "votes_for": 106,
            "votes_against": 5,
            "abstentions": 32,
            "dispensed_count": 0,
            "quorum": "Quórum Simple",
            "quorum_code": 1,
            "result": "Aprobado",
            "result_code": 1,
            "type": "Proyecto de Ley",
            "type_code": 1,
        },
        {
            "id": 89112,
            "description": "Boletín N° 15800-18",
            "date": "2026-06-09",
            "votes_for": 90,
            "votes_against": 30,
            "abstentions": 10,
            "dispensed_count": 0,
            "quorum": "Quórum Simple",
            "quorum_code": 1,
            "result": "Aprobado",
            "result_code": 1,
            "type": "Proyecto de Ley",
            "type_code": 1,
        },
        # Below watermark — should be skipped.
        {
            "id": 89000,
            "description": "Boletín N° 15700-18",
            "date": "2026-05-01",
        },
        # Non-bill vote — should be skipped, watermark continues advancing.
        {
            "id": 89111,
            "description": "Cuenta de la sesión",
            "date": "2026-06-08",
        },
    ]

    class FakeOpenData:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_votes_by_year(self, year):
            assert year == 2026
            return bulk_rows

    monkeypatch.setattr(ingestor_tasks, "OpenDataCamaraClient", FakeOpenData)
    monkeypatch.setattr(ingestor_tasks, "_get_chamber_votes_watermark", lambda: 89000)
    monkeypatch.setattr(
        ingestor_tasks,
        "_set_chamber_votes_watermark",
        lambda new_max: watermark_after.append(new_max),
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, *args: dispatched.append((task, args)),
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_trigger_targeted_bill_ingest",
        lambda bulletin: triggered_bill_ingests.append(bulletin),
    )
    # 15936-18 is unknown; 15800-18 is known.
    monkeypatch.setattr(
        ingestor_tasks,
        "_bill_exists",
        lambda bulletin: bulletin == "15800-18",
    )

    # Stub the rich-summary and per-vote-detail fan-outs.
    monkeypatch.setattr(
        ingestor_tasks,
        "_fetch_rich_summaries",
        lambda bulletins: {
            89113: {
                "voting_type": "Única",
                "voting_type_code": 6,
                "article_text": "Artículo único.",
                "constitutional_procedure": "Tercer Trámite",
                "constitutional_procedure_id": 3,
                "regulatory_procedure": "Sin Informe",
                "regulatory_procedure_id": 7,
            },
            89112: {
                "voting_type": "General",
                "voting_type_code": 1,
                "article_text": "Artículo 1.",
            },
        },
    )
    monkeypatch.setattr(
        ingestor_tasks,
        "_fetch_vote_details",
        lambda vote_ids: {
            89113: {"individual_votes": []},
            89112: {"individual_votes": []},
        },
    )

    result = ingestor_tasks.run_ingest_chamber_votes(dry_run=False, source="bulk")

    assert result["source"] == "bulk"
    assert result["candidates"] == 2  # 89000 below wm, non-bill skipped
    assert result["dispatched"] == 2
    assert result["mode"] == "incremental"
    assert watermark_after == [89113]
    # Both votes were dispatched with their bulletin attached.
    assert {args[1] for _, args in dispatched} == {"15936-18", "15800-18"}
    # Only the unknown bulletin triggered a targeted bill ingest.
    assert triggered_bill_ingests == ["15936-18"]


def test_run_ingest_chamber_votes_cold_start_walks_year_range(monkeypatch):
    years_called: list[int] = []

    class FakeOpenData:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_votes_by_year(self, year):
            years_called.append(year)
            return []

    monkeypatch.setattr(ingestor_tasks, "OpenDataCamaraClient", FakeOpenData)
    monkeypatch.setattr(ingestor_tasks, "_get_chamber_votes_watermark", lambda: None)
    monkeypatch.setattr(ingestor_tasks.settings, "ingestor_bills_start_year", 2024)
    monkeypatch.setattr(
        ingestor_tasks.settings, "ingestor_chamber_votes_max_years_per_tick", 5
    )

    result = ingestor_tasks.run_ingest_chamber_votes(
        dry_run=False, source="bulk", max_years=5
    )

    # Cold start with no data — no dispatch, but years are walked newest-first.
    assert result["mode"] == "cold_start"
    today_year = datetime.date.today().year
    assert years_called[0] == today_year
    assert years_called[-1] == max(2024, today_year - 4)
    assert all(
        years_called[i] >= years_called[i + 1] for i in range(len(years_called) - 1)
    )


def test_run_ingest_chamber_votes_skips_when_source_is_bill_detail(monkeypatch):
    called = {"opendata_constructed": False}

    class FakeOpenData:
        def __init__(self, *args, **kwargs):
            called["opendata_constructed"] = True

    monkeypatch.setattr(ingestor_tasks, "OpenDataCamaraClient", FakeOpenData)

    result = ingestor_tasks.run_ingest_chamber_votes(source="bill_detail")

    assert result["source"] == "bill_detail"
    assert result["mode"] == "skip"
    assert called["opendata_constructed"] is False


def test_run_ingest_chamber_votes_targeted_bulletin_skips_discovery(monkeypatch):
    dispatched: list[tuple[object, tuple]] = []
    discovery_called = {"flag": False}

    class FakeOpenData:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_votes_by_year(self, year):
            discovery_called["flag"] = True
            return []

        def get_chamber_votes_for_bulletin(self, bulletin):
            assert bulletin == "15936-18"
            return [
                {
                    "id": 89113,
                    "description": "Boletín N° 15936-18",
                    "date": "2026-06-10",
                    "voting_type": "Única",
                    "voting_type_code": 6,
                    "article_text": "Artículo único.",
                    "votes_for": 1,
                    "votes_against": 0,
                    "abstentions": 0,
                    "dispensed_count": 0,
                    "result": "Aprobado",
                    "result_code": 1,
                }
            ]

    monkeypatch.setattr(ingestor_tasks, "OpenDataCamaraClient", FakeOpenData)
    monkeypatch.setattr(ingestor_tasks, "_get_chamber_votes_watermark", lambda: None)
    monkeypatch.setattr(
        ingestor_tasks,
        "_dispatch",
        lambda task, *args: dispatched.append((task, args)),
    )
    monkeypatch.setattr(ingestor_tasks, "_bill_exists", lambda bulletin: True)
    monkeypatch.setattr(
        ingestor_tasks, "_fetch_vote_details", lambda vote_ids: {89113: {}}
    )
    # Stub watermark advance — targeted runs should NOT call it.
    watermark_advances: list[int] = []
    monkeypatch.setattr(
        ingestor_tasks,
        "_set_chamber_votes_watermark",
        lambda new_max: watermark_advances.append(new_max),
    )

    result = ingestor_tasks.run_ingest_chamber_votes(
        bulletin="15936-18", source="bulk", dry_run=False
    )

    assert discovery_called["flag"] is False
    assert result["mode"] == "single_bulletin"
    assert result["candidates"] == 1
    assert result["dispatched"] == 1
    # Targeted runs do not advance the watermark.
    assert watermark_advances == []

import asyncio
import datetime
import json
from typing import Any

import httpx
import pytest

from app.ingestors.clients import bcn as bcn_module
from app.ingestors.clients.bcn import (
    BCNClient,
    fetch_person_appointments_parallel,
    fetch_person_profiles_parallel,
)


def _str_binding(value: str | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"type": "literal", "value": value}


def _uri_binding(value: str | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"type": "uri", "value": value}


def _roster_binding(
    *,
    person: str,
    appointment: str,
    cargo: str,
    nombre: str,
    inicio: str,
    fin: str,
    id_senado: str | None = None,
    id_camara: str | None = None,
) -> dict[str, Any]:
    binding = {
        "personUri": _uri_binding(person),
        "appointmentUri": _uri_binding(appointment),
        "cargoId": _str_binding(cargo),
        "nombre": _str_binding(nombre),
        "fechaInicio": _str_binding(inicio),
        "fechaFin": _str_binding(fin),
    }
    if id_senado is not None:
        binding["idSenado"] = _str_binding(id_senado)
    if id_camara is not None:
        binding["idCamara"] = _str_binding(id_camara)
    return {k: v for k, v in binding.items() if v is not None}


def test_get_active_appointments_extracts_one_row_per_active_appointment(monkeypatch):
    captured_queries: list[str] = []

    def fake_sparql(self, query: str) -> list[dict[str, Any]]:
        captured_queries.append(query)
        return [
            _roster_binding(
                person="http://datos.bcn.cl/recurso/persona/1017",
                appointment="http://datos.bcn.cl/recurso/persona/1017/nombramiento/3",
                cargo="1",
                id_camara="1017",
                nombre="Álvaro Carter Fernández",
                inicio="2026-03-11",
                fin="2030-03-11",
            ),
            _roster_binding(
                person="http://datos.bcn.cl/recurso/persona/4558",
                appointment="http://datos.bcn.cl/recurso/persona/4558/nombramiento/5",
                cargo="2",
                id_senado="1234",
                nombre="Ana Pérez Soto",
                inicio="2022-03-11",
                fin="2030-03-11",
            ),
        ]

    monkeypatch.setattr(BCNClient, "_sparql", fake_sparql)

    with BCNClient() as client:
        rows = client.get_active_appointments(today=datetime.date(2026, 5, 28))

    assert len(rows) == 2
    deputy, senator = rows
    assert deputy["cargoId"] == "1"
    assert deputy["idCamara"] == "1017"
    assert deputy["idSenado"] is None
    assert deputy["full_name"] == "Álvaro Carter Fernández"
    assert deputy["term_end"] == "2030-03-11"
    assert deputy["appointmentUri"].endswith("/nombramiento/3")
    assert senator["cargoId"] == "2"
    assert senator["idSenado"] == "1234"
    assert "2026-05-28" in captured_queries[0]
    assert 'FILTER(?cargoId IN ("1", "2"))' in captured_queries[0]


def test_get_active_appointments_drops_empty_optionals(monkeypatch):
    def fake_sparql(self, query: str) -> list[dict[str, Any]]:
        return [
            {
                "personUri": _uri_binding("http://datos.bcn.cl/recurso/persona/99"),
                "appointmentUri": _uri_binding(
                    "http://datos.bcn.cl/recurso/persona/99/nombramiento/1"
                ),
                "cargoId": _str_binding("1"),
                "nombre": _str_binding("Sin IDs"),
                "fechaInicio": _str_binding("2026-03-11"),
                "fechaFin": _str_binding("2030-03-11"),
            }
        ]

    monkeypatch.setattr(BCNClient, "_sparql", fake_sparql)

    with BCNClient() as client:
        rows = client.get_active_appointments()

    assert rows[0]["idSenado"] is None
    assert rows[0]["idCamara"] is None


def test_get_person_profile_returns_none_when_no_bindings(monkeypatch):
    monkeypatch.setattr(BCNClient, "_sparql", lambda self, query: [])

    with BCNClient() as client:
        assert (
            client.get_person_profile("http://datos.bcn.cl/recurso/persona/9999")
            is None
        )


def test_get_person_profile_picks_first_value_across_bindings(monkeypatch):
    def fake_sparql(self, query: str) -> list[dict[str, Any]]:
        return [
            {
                "nombre": _str_binding("Álvaro Jorge Carter Fernández"),
                "profesion": _str_binding("Diseñador Industrial"),
                "imagen": _uri_binding(
                    "https://www.bcn.cl/laborparlamentaria/imagen/4558.jpg"
                ),
                "thumbnail": _uri_binding(
                    "https://www.bcn.cl/laborparlamentaria/imagen/110x110/4558.jpg"
                ),
                "twitter": _str_binding("Alvaro_CarterF"),
                "paginaWiki": _uri_binding(
                    "https://www.bcn.cl/historiapolitica/resenas_parlamentarias/wiki/Carter"
                ),
                "genero": _str_binding("hombre"),
                "idCamara": _str_binding("1017"),
            },
            # Second binding (e.g. duplicate property) — should be ignored.
            {
                "nombre": _str_binding("DUPLICATE NAME"),
                "twitter": _str_binding("duplicate_handle"),
            },
        ]

    monkeypatch.setattr(BCNClient, "_sparql", fake_sparql)

    with BCNClient() as client:
        profile = client.get_person_profile("http://datos.bcn.cl/recurso/persona/4558")

    assert profile is not None
    assert profile["full_name"] == "Álvaro Jorge Carter Fernández"
    assert profile["twitter"] == "Alvaro_CarterF"
    assert profile["bcn_wiki_url"].startswith("https://www.bcn.cl/historiapolitica/")
    assert profile["profession"] == "Diseñador Industrial"
    assert profile["idSenado"] is None
    assert profile["idCamara"] == "1017"
    assert profile["personUri"] == "http://datos.bcn.cl/recurso/persona/4558"


def test_get_person_appointments_returns_all_periods(monkeypatch):
    def fake_sparql(self, query: str) -> list[dict[str, Any]]:
        return [
            {
                "appointmentUri": _uri_binding(
                    "http://datos.bcn.cl/recurso/persona/4558/nombramiento/1"
                ),
                "cargoId": _str_binding("1"),
                "fechaInicio": _str_binding("2014-03-11"),
                "fechaFin": _str_binding("2018-03-11"),
            },
            {
                "appointmentUri": _uri_binding(
                    "http://datos.bcn.cl/recurso/persona/4558/nombramiento/2"
                ),
                "cargoId": _str_binding("2"),
                "fechaInicio": _str_binding("2022-03-11"),
                "fechaFin": _str_binding("2030-03-11"),
            },
        ]

    monkeypatch.setattr(BCNClient, "_sparql", fake_sparql)

    with BCNClient() as client:
        terms = client.get_person_appointments(
            "http://datos.bcn.cl/recurso/persona/4558"
        )

    assert [t["cargoId"] for t in terms] == ["1", "2"]
    assert terms[0]["term_end"] == "2018-03-11"
    assert terms[1]["appointmentUri"].endswith("/nombramiento/2")


def test_get_person_profile_returns_none_for_empty_uri():
    with BCNClient() as client:
        assert client.get_person_profile("") is None


def test_get_person_appointments_returns_empty_for_empty_uri():
    with BCNClient() as client:
        assert client.get_person_appointments("") == []


def test_sparql_request_uses_get_with_format_and_accept_header(monkeypatch):
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["accept"] = request.headers.get("accept")
        captured["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(
            200,
            content=json.dumps({"results": {"bindings": []}}),
            headers={"content-type": "application/sparql-results+json"},
        )

    transport = httpx.MockTransport(handler)
    client = BCNClient()
    client._client = httpx.Client(
        transport=transport,
        headers={
            "accept": "application/sparql-results+json",
            "user-agent": "CamaraAbierta-Engine/3.0",
        },
    )

    try:
        rows = client.get_active_appointments(today=datetime.date(2026, 5, 28))
    finally:
        client.close()

    assert rows == []
    assert "datos.bcn.cl/sparql" in captured["url"]
    assert "format=application" in captured["url"]
    assert "2026-05-28" in captured["url"]
    assert captured["accept"] == "application/sparql-results+json"
    assert captured["user_agent"] == "CamaraAbierta-Engine/3.0"


def _profile_payload(person: str, twitter: str | None = None) -> bytes:
    binding: dict[str, Any] = {"nombre": _str_binding(f"Person of {person}")}
    if twitter:
        binding["twitter"] = _str_binding(twitter)
    return json.dumps({"results": {"bindings": [binding]}}).encode()


def test_fetch_person_profiles_parallel_returns_dict_keyed_by_uri(monkeypatch):
    # Neutralize the retry backoff so the 500-response path fails fast rather
    # than burning through ~135s of exponential waits.
    monkeypatch.setattr(bcn_module, "_bcn_backoff", lambda attempt: 0.0)
    monkeypatch.setattr(bcn_module, "MAX_RETRIES", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        if "persona/1" in query:
            return httpx.Response(200, content=_profile_payload("1", twitter="one"))
        if "persona/2" in query:
            return httpx.Response(200, content=_profile_payload("2", twitter="two"))
        if "persona/3" in query:
            return httpx.Response(500, content=b"oops")
        if "persona/4" in query:
            return httpx.Response(200, content=b"{not json")
        return httpx.Response(404)

    def fake_build_async_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(bcn_module, "_build_async_client", fake_build_async_client)

    profiles = asyncio.run(
        fetch_person_profiles_parallel(
            [
                "http://datos.bcn.cl/recurso/persona/1",
                "http://datos.bcn.cl/recurso/persona/2",
                "http://datos.bcn.cl/recurso/persona/3",
                "http://datos.bcn.cl/recurso/persona/4",
                "http://datos.bcn.cl/recurso/persona/1",  # duplicate
            ]
        )
    )

    assert set(profiles.keys()) == {
        "http://datos.bcn.cl/recurso/persona/1",
        "http://datos.bcn.cl/recurso/persona/2",
        "http://datos.bcn.cl/recurso/persona/3",
        "http://datos.bcn.cl/recurso/persona/4",
    }
    assert profiles["http://datos.bcn.cl/recurso/persona/1"]["twitter"] == "one"
    assert profiles["http://datos.bcn.cl/recurso/persona/2"]["twitter"] == "two"
    assert profiles["http://datos.bcn.cl/recurso/persona/3"] is None
    assert profiles["http://datos.bcn.cl/recurso/persona/4"] is None


def test_fetch_person_appointments_parallel_returns_lists_keyed_by_uri(monkeypatch):
    monkeypatch.setattr(bcn_module, "_bcn_backoff", lambda attempt: 0.0)
    monkeypatch.setattr(bcn_module, "MAX_RETRIES", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params["query"]
        if "persona/1" in query:
            payload = {
                "results": {
                    "bindings": [
                        {
                            "appointmentUri": _uri_binding(
                                "http://datos.bcn.cl/recurso/persona/1/nombramiento/1"
                            ),
                            "cargoId": _str_binding("1"),
                            "fechaInicio": _str_binding("2018-03-11"),
                            "fechaFin": _str_binding("2022-03-11"),
                        }
                    ]
                }
            }
            return httpx.Response(200, content=json.dumps(payload).encode())
        return httpx.Response(500)

    def fake_build_async_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(bcn_module, "_build_async_client", fake_build_async_client)

    appointments = asyncio.run(
        fetch_person_appointments_parallel(
            [
                "http://datos.bcn.cl/recurso/persona/1",
                "http://datos.bcn.cl/recurso/persona/2",
            ]
        )
    )

    assert len(appointments["http://datos.bcn.cl/recurso/persona/1"]) == 1
    assert appointments["http://datos.bcn.cl/recurso/persona/2"] == []


def test_fetch_person_profiles_parallel_returns_empty_for_no_uris():
    assert asyncio.run(fetch_person_profiles_parallel([])) == {}


def test_fetch_person_appointments_parallel_returns_empty_for_no_uris():
    assert asyncio.run(fetch_person_appointments_parallel([])) == {}


@pytest.mark.parametrize(
    "binding, expected",
    [
        ({"x": {"value": "hi"}}, "hi"),
        ({"x": {"value": ""}}, None),
        ({"x": {}}, None),
        ({}, None),
    ],
)
def test_binding_value_handles_missing_and_empty(binding, expected):
    assert bcn_module._binding_value(binding, "x") == expected


def test_sparql_retries_on_429_then_succeeds(monkeypatch):
    """Sync path: a 429 followed by a 200 should retry and return data."""
    monkeypatch.setattr(bcn_module, "_bcn_backoff", lambda attempt: 0.0)

    call_log: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_log.append(len(call_log) + 1)
        if len(call_log) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(
            200,
            content=json.dumps(
                {"results": {"bindings": [{"x": {"value": "ok"}}]}}
            ).encode(),
        )

    transport = httpx.MockTransport(handler)
    client = BCNClient()
    client._client = httpx.Client(transport=transport)

    try:
        bindings = client._sparql("SELECT * WHERE { ?s ?p ?o }")
    finally:
        client.close()

    assert len(call_log) == 2
    assert bindings == [{"x": {"value": "ok"}}]


def test_sparql_raises_after_exhausting_retries(monkeypatch):
    """A persistent 429 should eventually raise CongresoAPIError."""
    monkeypatch.setattr(bcn_module, "_bcn_backoff", lambda attempt: 0.0)
    monkeypatch.setattr(bcn_module, "MAX_RETRIES", 3)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    transport = httpx.MockTransport(handler)
    client = BCNClient()
    client._client = httpx.Client(transport=transport)

    try:
        with pytest.raises(bcn_module.CongresoAPIError) as excinfo:
            client._sparql("SELECT * WHERE { ?s ?p ?o }")
    finally:
        client.close()

    assert excinfo.value.status_code == 429


def test_sparql_does_not_retry_on_400(monkeypatch):
    """A 4xx other than 429 should fail fast (malformed query)."""
    monkeypatch.setattr(bcn_module, "_bcn_backoff", lambda attempt: 0.0)

    call_log: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_log.append(len(call_log) + 1)
        return httpx.Response(400)

    transport = httpx.MockTransport(handler)
    client = BCNClient()
    client._client = httpx.Client(transport=transport)

    try:
        with pytest.raises(bcn_module.CongresoAPIError) as excinfo:
            client._sparql("MALFORMED")
    finally:
        client.close()

    assert excinfo.value.status_code == 400
    assert len(call_log) == 1  # no retry


def test_sparql_honors_retry_after_header(monkeypatch):
    """When the server sets Retry-After, we should use that as the wait."""
    waits: list[float] = []

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(bcn_module, "_bcn_backoff", lambda attempt: 99999.0)

    import time as time_mod

    monkeypatch.setattr(time_mod, "sleep", fake_sleep)

    state = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["count"] += 1
        if state["count"] == 1:
            return httpx.Response(429, headers={"retry-after": "2.5"})
        return httpx.Response(
            200, content=json.dumps({"results": {"bindings": []}}).encode()
        )

    transport = httpx.MockTransport(handler)
    client = BCNClient()
    client._client = httpx.Client(transport=transport)

    try:
        client._sparql("SELECT 1")
    finally:
        client.close()

    # Retry-After took precedence over the (huge) backoff fallback.
    assert waits == [2.5]


def test_afetch_sparql_retries_on_429_then_succeeds(monkeypatch):
    """Async path: 429 + 200 should retry transparently."""
    monkeypatch.setattr(bcn_module, "_bcn_backoff", lambda attempt: 0.0)

    state = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["count"] += 1
        if state["count"] == 1:
            return httpx.Response(429)
        return httpx.Response(
            200,
            content=json.dumps(
                {"results": {"bindings": [{"y": {"value": "done"}}]}}
            ).encode(),
        )

    def fake_build_async_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(bcn_module, "_build_async_client", fake_build_async_client)

    profiles = asyncio.run(
        fetch_person_profiles_parallel(["http://datos.bcn.cl/recurso/persona/1"])
    )

    assert state["count"] == 2
    profile = profiles["http://datos.bcn.cl/recurso/persona/1"]
    assert profile is not None


def test_afetch_sparql_persistent_429_returns_none_in_parallel(monkeypatch):
    """When async retries are exhausted, the parallel wrapper records ``None`` for the URI."""
    monkeypatch.setattr(bcn_module, "_bcn_backoff", lambda attempt: 0.0)
    monkeypatch.setattr(bcn_module, "MAX_RETRIES", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    def fake_build_async_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(bcn_module, "_build_async_client", fake_build_async_client)

    profiles = asyncio.run(
        fetch_person_profiles_parallel(["http://datos.bcn.cl/recurso/persona/1"])
    )

    assert profiles == {"http://datos.bcn.cl/recurso/persona/1": None}


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"retry-after": "5"}, 5.0),
        ({"retry-after": "0.5"}, 0.5),
        ({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, None),  # HTTP-date
        ({}, None),
        ({"retry-after": ""}, None),
    ],
)
def test_retry_after_seconds_parses_delta_seconds(headers, expected):
    response = httpx.Response(429, headers=headers)
    assert bcn_module._retry_after_seconds(response) == expected


def test_retry_after_seconds_caps_at_max():
    response = httpx.Response(
        429, headers={"retry-after": str(bcn_module.RETRY_AFTER_CAP_SECONDS + 600)}
    )
    assert (
        bcn_module._retry_after_seconds(response) == bcn_module.RETRY_AFTER_CAP_SECONDS
    )

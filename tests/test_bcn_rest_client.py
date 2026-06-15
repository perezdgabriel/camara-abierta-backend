from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.ingestors.clients.base import CongresoAPIError
from app.ingestors.clients.bcn_rest import (
    CAMARA_ID_DEPUTIES,
    CAMARA_ID_SENATE,
    BCNRestClient,
)

FIXTURE = Path(__file__).parent / "fixtures" / "bcn_parlamentarios_activos.xml"


@pytest.fixture
def fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def _client_with(handler) -> BCNRestClient:
    client = BCNRestClient()
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    return client


def test_get_active_parliamentarians_parses_full_roster(fixture_bytes):
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, content=fixture_bytes)

    with _client_with(handler) as client:
        records = client.get_active_parliamentarians()

    assert "ObtenerParlamentariosActivos" in captured["url"]

    assert len(records) == 205
    by_chamber: dict[int, int] = {}
    for r in records:
        by_chamber[r["camara_id"]] = by_chamber.get(r["camara_id"], 0) + 1
    assert by_chamber == {CAMARA_ID_DEPUTIES: 155, CAMARA_ID_SENATE: 50}


def test_get_active_parliamentarians_extracts_senator_fields(fixture_bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fixture_bytes)

    with _client_with(handler) as client:
        records = client.get_active_parliamentarians()

    sepulveda = next(
        r for r in records if r["nombre_completo"] == "Alejandra Sepúlveda Orbenes"
    )
    assert sepulveda["camara_id"] == CAMARA_ID_SENATE
    assert sepulveda["bcn_id"] == 2717
    assert sepulveda["bcn_uri"] == "http://datos.bcn.cl/recurso/persona/2717"
    assert sepulveda["id_en_camara_de_origen"] == 1341
    assert sepulveda["fecha_nacimiento"] == "1965-11-13"
    assert sepulveda["email"] == "asepulveda@senado.cl"
    assert sepulveda["id_wiki"] == "Alejandra_Sepúlveda_Orbenes"
    assert sepulveda["partido_acronimo"] == "IND"
    assert sepulveda["division_tipo"] == "Circunscripcion"
    # division_id for senators is the Roman-numeral position parsed from the
    # description (matches Circumscription.number in the DB, 1..16), NOT the
    # raw <id> element from BCN.
    assert sepulveda["division_id"] == 8
    assert sepulveda["division_descripcion"] == "Circunscripción VIII O'Higgins"
    assert sepulveda["region_nombre"].startswith("Región Del Libertador")


def test_get_active_parliamentarians_extracts_deputy_fields(fixture_bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fixture_bytes)

    with _client_with(handler) as client:
        records = client.get_active_parliamentarians()

    romero = next(
        r for r in records if r["nombre_completo"] == "Agustín Matías Romero Leiva"
    )
    assert romero["camara_id"] == CAMARA_ID_DEPUTIES
    assert romero["bcn_id"] == 5250
    assert romero["bcn_uri"] == "http://datos.bcn.cl/recurso/persona/5250"
    assert romero["id_en_camara_de_origen"] == 1165
    assert romero["division_tipo"] == "Distrito"
    assert romero["division_descripcion"].startswith("Distrito")
    assert romero["partido_acronimo"]


def test_get_active_parliamentarians_surfaces_5xx_as_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"<html>bad gateway</html>")

    with _client_with(handler) as client:
        with pytest.raises(CongresoAPIError) as excinfo:
            client.get_active_parliamentarians()

    assert excinfo.value.status_code == 502

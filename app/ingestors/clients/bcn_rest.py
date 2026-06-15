"""REST client for BCN's `ObtenerParlamentariosActivos` service.

Returns the active parliamentary roster (both chambers) as a single XML
payload. Distinct from :mod:`app.ingestors.clients.bcn` (SPARQL) — see
ADR-0012. This endpoint is the critical-path roster authority; the SPARQL
client is demoted to best-effort biographic enrichment.

Bridge IDs (``IdEnCamaraDeOrigen``): OpenData deputy ``Id`` for deputies,
senado.cl ``ID_PARLAMENTARIO`` for senators — same scheme already used to
construct ``Legislator.bcn_id`` (``camara:{id}`` / ``senado:{PARLID}``).
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from app.core.config import settings
from app.ingestors.clients.base import BaseCongresoClient, CongresoParseError

ACTIVE_PATH = (
    "catalogo/servicio/ServiciosWebHistoriaDeLaLey/ObtenerParlamentariosActivos"
)

CAMARA_ID_DEPUTIES = 288
CAMARA_ID_SENATE = 261
DISTRIC_TYPE = "Distrito"
CIRCUMSCRIPTION_TYPE = "Circunscripcion"
ROMAN_INT_MAP = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
    "XI": 11,
    "XII": 12,
    "XIII": 13,
    "XIV": 14,
    "XV": 15,
    "XVI": 16,
}


class BCNRestClient(BaseCongresoClient):
    BASE_URL = settings.ingestor_base_url_bcn

    def get_active_parliamentarians(self) -> list[dict]:
        root = self._get_xml(ACTIVE_PATH)
        return [self._parse_record(node) for node in root.findall("Parlamentario")]

    @staticmethod
    def _parse_record(node: ET.Element) -> dict:
        camara = node.find("Camara")
        partido = node.find("PartidoPoliticoActual")
        geo = node.find("RepresentacionGeografica")
        division = (
            geo.find("DivisionPoliticoAdministrativa") if geo is not None else None
        )
        region = geo.find("Region") if geo is not None else None

        try:
            bcn_id = int(node.attrib["id"])
        except (KeyError, ValueError) as exc:
            raise CongresoParseError(
                f"Parlamentario node missing numeric id attribute: {node.attrib}"
            ) from exc

        division_tipo = division.attrib.get("tipo", "") if division is not None else ""
        division_descripcion = _text(division, "descripcion")
        division_id_str = _text(division, "id")
        division_id = _get_division_id(
            division_tipo, division_id_str, division_descripcion
        )
        return {
            "bcn_id": bcn_id,
            "bcn_uri": node.attrib.get("uri", ""),
            "id_en_camara_de_origen": _int_or_none(_text(node, "IdEnCamaraDeOrigen")),
            "nombres": _text(node, "Nombres"),
            "apellido_paterno": _text(node, "ApellidoPaterno"),
            "apellido_materno": _text(node, "ApellidoMaterno"),
            "nombre_completo": _text(node, "NombreCompleto"),
            "fecha_nacimiento": _iso_date(_text(node, "FechaDeNacimiento")),
            "email": _text(node, "Email"),
            "id_wiki": _text(node, "IdWiki"),
            "camara_id": _int_or_none(_text(camara, "Id")),
            "camara_descripcion": _text(camara, "Descripcion"),
            "partido_id": _int_or_none(_text(partido, "Id")),
            "partido_nombre": _text(partido, "Descripcion"),
            "partido_acronimo": _text(partido, "Acronimo"),
            "division_tipo": division_tipo,
            "division_id": division_id,
            "division_descripcion": division_descripcion,
            "region_nombre": _text(region, "Descripcion"),
            "region_uri": _text(region, "Uri"),
        }


def _text(node: ET.Element | None, tag: str) -> str:
    if node is None:
        return ""
    child = node.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _int_or_none(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _iso_date(value: str) -> str | None:
    """Take a leading ``YYYY-MM-DD`` from a value that may have a time suffix."""
    if not value:
        return None
    return value[:10] if len(value) >= 10 else None


def _get_division_id(
    division_tipo: str, division_id_str: str, division_descripcion: str
) -> int | None:
    if division_tipo == CIRCUMSCRIPTION_TYPE:
        # Circumscription id comes in description field in the form of Roman number:
        # ie: Circunscripción IV Atacama
        roman = division_descripcion.split(" ")[1]
        return ROMAN_INT_MAP.get(roman)
    else:
        return _int_or_none(division_id_str)

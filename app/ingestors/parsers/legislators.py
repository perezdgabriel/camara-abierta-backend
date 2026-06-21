"""Parsers for legislator-roster upstream sources (ADR-0012, ADR-0015).

Each parser returns a "person seed" payload normalized to a single shape
that the write service consumes via :func:`upsert_legislator_with_terms`:

    {
        "source": "opendata_camara" | "senado_web" | "bcn_rest",
        "source_external_id": str,
        "first_name": str,
        "last_name": str,
        "full_name": str,
        "paternal_last_name": str,    # for normalized cross-chamber name match
        "maternal_last_name": str,
        "birth_date": str | None,
        "gender": str,
        # senate-only enrichment fields (empty for deputies):
        "email": str,
        "phone": str,
        "photo_url": str,
        "photo_thumbnail_url": str,
        "profile_url": str,
        # canonical cross-chamber identity (when known — BCN REST sets it):
        "bcn_uri": str | None,
        "bcn_wiki_url": str | None,
        # per-stint terms:
        "terms": [
            {
                "chamber_type": ChamberType,
                "chamber_external_id": str | None,
                "start_date": str,            # ISO YYYY-MM-DD
                "end_date": str | None,
                "party_name": str,
                "party_alias": str,
                "party_source": "opendata" | "senado_abbreviation" | None,
                "district_number": int | None,
                "circumscription_number": int | None,
            },
            ...
        ],
    }

The write service handles cross-chamber person merging by normalized name +
``PERIODOS`` overlap (see ADR-0015) and writes one :class:`Legislator` plus
one :class:`LegislatorTerm` per term entry.
"""

import re
from datetime import date
from typing import Any

from app.models.enums import ChamberType

SENADO_PROFILE_BASE = (
    "https://www.senado.cl/senadoras-y-senadores/listado-de-senadoras-y-senadores"
)

BCN_WIKI_BASE = "https://www.bcn.cl/historiapolitica/resenas_parlamentarias/wiki"

BCN_CARGO_DEPUTY = "1"
BCN_CARGO_SENATOR = "2"

BCN_REST_CAMARA_ID_DEPUTIES = 288
BCN_REST_CAMARA_ID_SENATE = 261

REST_ACRONYM_TO_PARTY_ACRONIM = {
    "F.R.E.V.S.": "FRVS",
    "P.D.C.": "DC",
    "Republicano": "PREP",
    "Frente Amplio": "FA",
    "Nacional Libertario": "PNL",
    "Independiente": "IND",
    "Demócratas": "DEM",
    "Evópoli": "EVOP",
    "Liberal": "PL",
}

# Chilean legislative period boundaries: parliamentarians take office on
# March 11 and end on March 10 of the year their term closes. PERIODOS only
# carries the year as a string, so we synthesize the boundary date.
#
# ``LegislatorTerm`` ranges are stored **inclusive** on both ends — all term
# queries (``_term_on``, ``_resolve_term_period``, vote-time party joins) use
# ``start_date <= d AND end_date >= d``. This differs from the *half-open*
# convention used for ``LegislativePeriod`` / ``Legislature`` /
# ``LegislativeSession`` (where ``end_date`` is the start of the next, see
# CONTEXT.md "Período Legislativo" + ADR-0016). Keep these two conventions
# distinct; do not mix them.
PERIOD_START_MONTH = 3
PERIOD_START_DAY = 11
PERIOD_END_MONTH = 3
PERIOD_END_DAY = 10


class LegislatorParser:
    @staticmethod
    def parse_opendata_deputy(raw: dict) -> dict | None:
        """Parse a deputy from OpenData ``retornarDiputados`` (historical list).

        One row per person with the full militancia history; each militancia
        becomes one term carrying ``camara:{Id}`` as the chamber bridge.
        Returns ``None`` when the deputy ID is missing (cannot construct the
        bridge and we'd be unable to resolve votes).
        """
        deputy_id = raw.get("id")
        if not deputy_id:
            return None

        first_name = (raw.get("first_name") or "").strip().title()
        second_name = (raw.get("second_name") or "").strip().title()
        last_father = (raw.get("last_name_father") or "").strip().title()
        last_mother = (raw.get("last_name_mother") or "").strip().title()
        name_parts = [
            part for part in [first_name, second_name, last_father, last_mother] if part
        ]
        full_name = " ".join(name_parts)

        bridge = f"camara:{deputy_id}"
        terms: list[dict[str, Any]] = []
        for militancia in raw.get("militancias") or []:
            start = militancia.get("start_date")
            if not start:
                continue
            end = militancia.get("end_date")
            # Skip malformed militancias where the closing date precedes the
            # opening date. OpenData has emitted these — e.g. deputy 1180
            # (Consuelo Veloso) carries a 2026-03-11 → 2026-03-10 row
            # alongside her real 2026-2030 militancia. Letting it through
            # collides on `(chamber, start_date)` in `_reconcile_terms` and
            # clobbers the valid term's end_date, dropping her from the
            # active set.
            if end and end < start:
                continue
            terms.append(
                {
                    "chamber_type": ChamberType.DEPUTIES,
                    "chamber_external_id": bridge,
                    "start_date": start,
                    "end_date": end,
                    "party_name": (militancia.get("party_name") or "").strip(),
                    "party_alias": (militancia.get("party_alias") or "").strip(),
                    "party_source": "opendata",
                    "district_number": None,
                    "circumscription_number": None,
                }
            )

        return {
            "source": "opendata_camara",
            "source_external_id": str(deputy_id),
            "first_name": first_name,
            "last_name": f"{last_father} {last_mother}".strip(),
            "full_name": full_name,
            "paternal_last_name": last_father,
            "maternal_last_name": last_mother,
            "birth_date": raw.get("birth_date"),
            "gender": raw.get("gender_code") or raw.get("gender") or "",
            "email": "",
            "phone": "",
            "photo_url": "",
            "photo_thumbnail_url": "",
            "profile_url": "",
            "bcn_uri": None,
            "bcn_wiki_url": None,
            "terms": terms,
        }

    @staticmethod
    def parse_senator(raw: dict) -> dict | None:
        """Parse a senator from senado.cl historical catalog (with ``PERIODOS``).

        Each ``PERIODO`` becomes one term, with chamber inferred from
        ``CAMARA`` and date boundaries synthesized from ``DESDE`` / ``HASTA``
        years. Senate stints carry ``senado:{ID_PARLAMENTARIO}`` as the
        chamber bridge; the deputy stints embedded in a senator's history
        leave the bridge ``None`` (the write-service merge fills it in from
        the matching OpenData deputy row). Party is only bound for the
        active senate term (``VIGENTE == 1``) — senado.cl does not expose
        historical party. Returns ``None`` when the senator ID is missing or
        ``PERIODOS`` is empty (stub record per ADR-0015).
        """
        parlid = raw.get("ID_PARLAMENTARIO")
        if not parlid:
            return None
        periodos = raw.get("PERIODOS") or []
        if not periodos:
            return None

        first_name = (raw.get("NOMBRE") or "").strip().title()
        last_father = (raw.get("APELLIDO_PATERNO") or "").strip().title()
        last_mother = (raw.get("APELLIDO_MATERNO") or "").strip().title()
        full_name = (raw.get("NOMBRE_COMPLETO") or "").strip() or " ".join(
            part for part in [first_name, last_father, last_mother] if part
        )
        slug = (raw.get("SLUG") or "").strip()

        current_party_abbreviation = (raw.get("PARTIDO") or "").strip()
        _party_name = REST_ACRONYM_TO_PARTY_ACRONIM.get(
            current_party_abbreviation, current_party_abbreviation
        )
        current_circumscription_number = _coerce_int(raw.get("CIRCUNSCRIPCION_ID"))
        current_region_name = (raw.get("REGION") or "").strip()

        senate_bridge = f"senado:{parlid}"
        terms: list[dict[str, Any]] = []
        for periodo in periodos:
            camara = (periodo.get("CAMARA") or "").strip().upper()
            if camara == "S":
                chamber_type = ChamberType.SENATE
                chamber_external_id: str | None = senate_bridge
            elif camara == "D":
                chamber_type = ChamberType.DEPUTIES
                chamber_external_id = None
            else:
                continue

            try:
                desde_year = int(periodo.get("DESDE"))
                hasta_year = int(periodo.get("HASTA"))
            except TypeError, ValueError:
                continue
            start_date = date(
                desde_year, PERIOD_START_MONTH, PERIOD_START_DAY
            ).isoformat()
            end_date = date(hasta_year, PERIOD_END_MONTH, PERIOD_END_DAY).isoformat()

            is_current_senate = (
                chamber_type == ChamberType.SENATE
                and int(periodo.get("VIGENTE") or 0) == 1
            )

            terms.append(
                {
                    "chamber_type": chamber_type,
                    "chamber_external_id": chamber_external_id,
                    "start_date": start_date,
                    "end_date": end_date,
                    "party_name": _party_name if is_current_senate else "",
                    "party_alias": "",
                    "party_source": "senado_abbreviation"
                    if is_current_senate
                    else None,
                    "district_number": None,
                    "circumscription_number": (
                        current_circumscription_number
                        if chamber_type == ChamberType.SENATE
                        else None
                    ),
                    "_region_name": current_region_name
                    if chamber_type == ChamberType.SENATE
                    else "",
                }
            )

        return {
            "source": "senado_web",
            "source_external_id": str(parlid),
            "first_name": first_name,
            "last_name": f"{last_father} {last_mother}".strip(),
            "full_name": full_name,
            "paternal_last_name": last_father,
            "maternal_last_name": last_mother,
            "birth_date": None,
            "gender": _gender_from_sexo(raw.get("SEXO"), raw.get("SEXO_ETIQUETA")),
            "email": (raw.get("EMAIL") or "").strip(),
            "phone": (raw.get("FONO") or "").strip(),
            "photo_url": (raw.get("IMAGEN_450") or raw.get("IMAGEN") or "").strip(),
            "photo_thumbnail_url": (raw.get("IMAGEN_120") or "").strip(),
            "profile_url": f"{SENADO_PROFILE_BASE}/{slug}" if slug else "",
            "bcn_uri": None,
            "bcn_wiki_url": None,
            "terms": terms,
        }

    @staticmethod
    def parse_bcn_rest_enrichment(raw: dict[str, Any]) -> dict[str, Any] | None:
        """Enrichment payload from a BCN REST active-roster row (ADR-0015).

        Returns ``bcn_uri`` + ``bcn_wiki_url`` keyed by the chamber bridge
        ``chamber_external_id`` so the write service can match it to an
        existing :class:`LegislatorTerm`. BCN REST is no longer the roster
        authority — historical OpenData + senado catalog drives identity —
        but it remains the only source for ``bcn_uri`` and ``bcn_wiki_url``.
        Returns ``None`` if the row lacks a bridge or both enrichment fields
        are empty.
        """
        bridge = raw.get("id_en_camara_de_origen")
        if bridge is None:
            return None
        camara_id = raw.get("camara_id")
        if camara_id == BCN_REST_CAMARA_ID_SENATE:
            chamber_external_id = f"senado:{bridge}"
        elif camara_id == BCN_REST_CAMARA_ID_DEPUTIES:
            chamber_external_id = f"camara:{bridge}"
        else:
            return None

        bcn_uri = (raw.get("bcn_uri") or "").strip() or None
        id_wiki = (raw.get("id_wiki") or "").strip()
        bcn_wiki_url = f"{BCN_WIKI_BASE}/{id_wiki}" if id_wiki else None
        if not bcn_uri and not bcn_wiki_url:
            return None
        return {
            "chamber_external_id": chamber_external_id,
            "bcn_uri": bcn_uri,
            "bcn_wiki_url": bcn_wiki_url,
        }

    @staticmethod
    def parse_bcn_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """BCN SPARQL biographic profile enrichment (keyed by ``bcn_uri``).

        Returns the subset that ``enrich_legislator_profile`` understands.
        Empty strings collapse to ``None`` so callers can rely on truthiness.
        """

        def _clean(value: object) -> str | None:
            text = (str(value).strip()) if value is not None else ""
            return text or None

        return {
            "bcn_uri": _clean(profile.get("personUri")),
            "bcn_wiki_url": _clean(profile.get("bcn_wiki_url")),
            "profession": _clean(profile.get("profession")),
            "twitter_handle": _clean(profile.get("twitter")),
            "gender": _normalize_bcn_gender(profile.get("gender")),
            "photo_url": _clean(profile.get("photo_url")),
            "photo_thumbnail_url": _clean(profile.get("photo_thumbnail_url")),
            "full_name": _clean(profile.get("full_name")),
        }

    @staticmethod
    def parse_bcn_appointment(
        appointment: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Normalize one BCN ``PositionPeriod`` appointment into a term payload.

        The write service applies this onto an existing :class:`LegislatorTerm`
        whose ``(chamber, start_date)`` matches, or opens a new term when
        none exists. The ``bcn_appointment_uri`` (URI from BCN's appointment
        graph) is the per-term natural key for SPARQL re-runs.
        """
        uri = (appointment.get("appointmentUri") or "").strip()
        if not uri:
            return None
        cargo = appointment.get("cargoId")
        if cargo == BCN_CARGO_SENATOR:
            chamber_type = ChamberType.SENATE
        elif cargo == BCN_CARGO_DEPUTY:
            chamber_type = ChamberType.DEPUTIES
        else:
            return None
        start = appointment.get("term_start")
        end = appointment.get("term_end")
        if not start or not end:
            return None
        return {
            "bcn_appointment_uri": uri,
            "chamber_type": chamber_type,
            "start_date": start,
            "end_date": end,
        }


def _normalize_bcn_gender(value: object) -> str | None:
    """Map BCN ``foaf:gender`` literals (``hombre`` / ``mujer``) to ``M`` / ``F``."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text.startswith("hombre"):
        return "M"
    if text.startswith("mujer"):
        return "F"
    return None


def _gender_from_sexo(sexo: object, etiqueta: object) -> str:
    """Map the senado SEXO code/label to a single char ("M"/"F")."""
    label = (str(etiqueta) if etiqueta is not None else "").strip().lower()
    if label.startswith("hombre"):
        return "M"
    if label.startswith("mujer"):
        return "F"
    code = str(sexo).strip() if sexo is not None else ""
    if code == "2":
        return "M"
    if code == "1":
        return "F"
    return ""


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError, TypeError:
        return None


def _parse_number(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    if match:
        try:
            return int(match.group())
        except ValueError:
            return None
    return None

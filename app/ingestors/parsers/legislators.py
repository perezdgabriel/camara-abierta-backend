import re
from typing import Any

from app.models.enums import ChamberType

SENADO_PROFILE_BASE = (
    "https://www.senado.cl/senadoras-y-senadores/listado-de-senadoras-y-senadores"
)

BCN_CARGO_DEPUTY = "1"
BCN_CARGO_SENATOR = "2"


class LegislatorParser:
    @staticmethod
    def parse_senator(raw: dict) -> dict:
        """Parse a senator from the senado.cl web-back JSON catalog (see ADR-0002).

        ``ID_PARLAMENTARIO`` equals the wspublico ``PARLID``, so the resulting
        ``bcn_id`` (``senado:{id}``) reconciles with any record previously created
        from the wspublico XML.
        """
        first_name = (raw.get("NOMBRE") or "").strip().title()
        last_name_father = (raw.get("APELLIDO_PATERNO") or "").strip().title()
        last_name_mother = (raw.get("APELLIDO_MATERNO") or "").strip().title()
        full_name = (
            raw.get("NOMBRE_COMPLETO") or ""
        ).strip() or f"{first_name} {last_name_father} {last_name_mother}".strip()
        slug = (raw.get("SLUG") or "").strip()
        return {
            "bcn_id": f"senado:{raw.get('ID_PARLAMENTARIO', '')}",
            "first_name": first_name,
            "last_name": f"{last_name_father} {last_name_mother}".strip(),
            "full_name": full_name,
            "chamber_type": ChamberType.SENATE,
            "is_active": True,
            "gender": _gender_from_sexo(raw.get("SEXO"), raw.get("SEXO_ETIQUETA")),
            "email": (raw.get("EMAIL") or "").strip(),
            "phone": (raw.get("FONO") or "").strip(),
            "photo_url": (raw.get("IMAGEN_450") or raw.get("IMAGEN") or "").strip(),
            "photo_thumbnail_url": (raw.get("IMAGEN_120") or "").strip(),
            "profile_url": f"{SENADO_PROFILE_BASE}/{slug}" if slug else "",
            "_party_name": (raw.get("PARTIDO") or "").strip(),
            "_circumscription": "",
            "_circumscription_number": _coerce_int(raw.get("CIRCUNSCRIPCION_ID")),
            "_region_name": (raw.get("REGION") or "").strip(),
        }

    @staticmethod
    def parse_opendata_deputy(raw: dict) -> dict:
        first_name = (raw.get("first_name") or "").strip().title()
        second_name = (raw.get("second_name") or "").strip().title()
        last_name_father = (raw.get("last_name_father") or "").strip().title()
        last_name_mother = (raw.get("last_name_mother") or "").strip().title()
        name_parts = [
            part
            for part in [first_name, second_name, last_name_father, last_name_mother]
            if part
        ]
        full_name = " ".join(name_parts)

        militancias = raw.get("militancias", [])
        current_party = ""
        current_party_alias = ""
        if militancias:
            active = [
                militancia
                for militancia in militancias
                if not militancia.get("end_date")
            ]
            source = active[-1] if active else militancias[-1]
            current_party = source.get("party_name", "") or source.get(
                "party_alias", ""
            )
            current_party_alias = source.get("party_alias", "")

        return {
            "bcn_id": f"camara:{raw.get('id', '')}",
            "first_name": first_name,
            "last_name": f"{last_name_father} {last_name_mother}".strip(),
            "full_name": full_name,
            "chamber_type": ChamberType.DEPUTIES,
            "is_active": True,
            "birth_date": raw.get("birth_date"),
            "gender": raw.get("gender_code") or raw.get("gender") or "",
            "_party_name": current_party.strip(),
            "_party_alias": current_party_alias.strip(),
            "_district_number": raw.get("district_number") or None,
            "_militancias": militancias,
        }

    @staticmethod
    def parse_bcn_roster_row(row: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize one BCN roster row into a roster entry keyed by ``bcn_id``.

        Returns ``None`` if the row lacks the chamber-specific bridge ID
        (``idSenado`` for senators, ``idCamara`` for deputies) — without it we
        cannot reconcile with vote records or chamber catalogs.
        """
        cargo = row.get("cargoId")
        chamber_type: ChamberType
        if cargo == BCN_CARGO_SENATOR:
            external_id = row.get("idSenado")
            if not external_id:
                return None
            bcn_id = f"senado:{external_id}"
            chamber_type = ChamberType.SENATE
        elif cargo == BCN_CARGO_DEPUTY:
            external_id = row.get("idCamara")
            if not external_id:
                return None
            bcn_id = f"camara:{external_id}"
            chamber_type = ChamberType.DEPUTIES
        else:
            return None

        person_uri = row.get("personUri") or ""
        appointment_uri = row.get("appointmentUri") or ""
        return {
            "bcn_id": bcn_id,
            "bcn_uri": person_uri,
            "external_id": str(external_id),
            "chamber_type": chamber_type,
            "full_name": (row.get("full_name") or "").strip(),
            "appointment_uri": appointment_uri,
            "term_start": row.get("term_start"),
            "term_end": row.get("term_end"),
        }

    @staticmethod
    def parse_bcn_profile(profile: dict[str, Any]) -> dict[str, Any]:
        """Normalize a BCN per-URI profile into enrichment fields.

        Returns the subset that :func:`enrich_legislator_profile` understands.
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
        """Normalize one BCN appointment row into a ``ParliamentaryAppointment`` payload.

        Returns ``None`` if the row lacks a usable appointment URI or chamber.
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
    """Map the senado SEXO code/label to a single char ("M"/"F").

    SEXO is "2" (Hombre) / "1" (Mujer); SEXO_ETIQUETA is "Hombre"/"Mujer".
    """
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

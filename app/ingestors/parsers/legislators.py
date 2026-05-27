import re

from app.models.enums import ChamberType

SENADO_PROFILE_BASE = (
    "https://www.senado.cl/senadoras-y-senadores/listado-de-senadoras-y-senadores"
)


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

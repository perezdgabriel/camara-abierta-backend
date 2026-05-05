import re


class LegislatorParser:
    @staticmethod
    def parse_senator(raw: dict) -> dict:
        first_name = (raw.get("first_name") or "").strip().title()
        last_name_father = (raw.get("last_name_father") or "").strip().title()
        last_name_mother = (raw.get("last_name_mother") or "").strip().title()
        full_name = f"{first_name} {last_name_father} {last_name_mother}".strip()
        return {
            "bcn_id": f"senado:{raw.get('parlid', '')}",
            "first_name": first_name,
            "last_name": f"{last_name_father} {last_name_mother}".strip(),
            "full_name": full_name,
            "chamber_type": "senator",
            "is_active": True,
            "email": (raw.get("email") or "").strip(),
            "phone": (raw.get("phone") or "").strip(),
            "_party_name": (raw.get("party") or "").strip(),
            "_circumscription": (raw.get("circumscription") or "").strip(),
            "_circumscription_number": _parse_number(raw.get("circumscription", "")),
            "_region_name": (raw.get("region") or "").strip(),
        }

    @staticmethod
    def parse_opendata_deputy(raw: dict) -> dict:
        first_name = (raw.get("first_name") or "").strip().title()
        second_name = (raw.get("second_name") or "").strip().title()
        last_name_father = (raw.get("last_name_father") or "").strip().title()
        last_name_mother = (raw.get("last_name_mother") or "").strip().title()
        name_parts = [part for part in [first_name, second_name, last_name_father, last_name_mother] if part]
        full_name = " ".join(name_parts)

        militancias = raw.get("militancias", [])
        current_party = ""
        if militancias:
            active = [militancia for militancia in militancias if not militancia.get("end_date")]
            source = active[-1] if active else militancias[-1]
            current_party = source.get("party_name", "") or source.get("party_alias", "")

        return {
            "bcn_id": f"camara:{raw.get('id', '')}",
            "first_name": first_name,
            "last_name": f"{last_name_father} {last_name_mother}".strip(),
            "full_name": full_name,
            "chamber_type": "deputy",
            "is_active": True,
            "birth_date": raw.get("birth_date"),
            "gender": raw.get("gender_code") or raw.get("gender") or "",
            "_party_name": current_party.strip(),
            "_militancias": militancias,
        }


def _parse_number(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    if match:
        try:
            return int(match.group())
        except ValueError:
            return None
    return None
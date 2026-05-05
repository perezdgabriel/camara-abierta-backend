COMMITTEE_TYPE_MAP = {
    "Permanente": "permanent",
    "permanente": "permanent",
    "Especial": "special",
    "especial": "special",
    "Mixta": "mixed",
    "mixta": "mixed",
    "Investigadora": "investigative",
    "investigadora": "investigative",
    "Especial Investigadora": "investigative",
}

ROLE_MAP = {
    "Presidente": "president",
    "presidente": "president",
    "Vicepresidente": "vice_president",
    "vicepresidente": "vice_president",
    "Integrante": "member",
    "integrante": "member",
    "Miembro": "member",
    "miembro": "member",
}


def _parse_role(value: str) -> str:
    return ROLE_MAP.get(value, "member")


class CommitteeParser:
    @staticmethod
    def parse_senate_committee(raw: dict) -> dict:
        return {
            "_source": "senado",
            "_external_id": f"senado:{raw['id']}",
            "name": raw.get("name", "").strip(),
            "committee_type": COMMITTEE_TYPE_MAP.get(raw.get("type", ""), "permanent"),
            "_chamber_type": "senate",
            "_email": raw.get("email", ""),
            "members": [
                {"bcn_id": f"senado:{member['parlid']}", "role": _parse_role(member.get("role", ""))}
                for member in raw.get("members", [])
            ],
        }

    @staticmethod
    def parse_opendata_committee(raw: dict) -> dict:
        return {
            "_source": "opendata",
            "_external_id": f"opendata:{raw.get('id', '')}",
            "name": raw.get("name", "").strip(),
            "committee_type": COMMITTEE_TYPE_MAP.get(raw.get("type", ""), "permanent"),
            "_chamber_type": "deputies",
            "_email": raw.get("email", ""),
            "members": [],
        }

    @staticmethod
    def parse_opendata_committee_detail(raw: dict) -> dict:
        members = [
            {
                "bcn_id": f"camara:{member['diputado_id']}",
                "role": "member",
                "start_date": member.get("start_date"),
                "end_date": member.get("end_date"),
            }
            for member in raw.get("members", [])
        ]
        president = raw.get("president", {})
        if president.get("id"):
            members.append({"bcn_id": f"camara:{president['id']}", "role": "president"})
        return {
            "_source": "opendata",
            "_external_id": f"opendata:{raw.get('id', '')}",
            "name": raw.get("name", "").strip(),
            "committee_type": COMMITTEE_TYPE_MAP.get(raw.get("type", ""), "permanent"),
            "_chamber_type": "deputies",
            "_email": raw.get("email", ""),
            "members": members,
        }
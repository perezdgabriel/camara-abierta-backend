SESSION_TYPE_MAP = {
    "Ordinaria": "ordinary",
    "ordinaria": "ordinary",
    "Extraordinaria": "extraordinary",
    "extraordinaria": "extraordinary",
}


class LegislatureParser:
    @staticmethod
    def parse_legislative_period(raw: dict) -> dict:
        number = None
        try:
            number = int(raw.get("id", 0))
        except (TypeError, ValueError):
            pass
        return {
            "_external_id": str(raw.get("id", "")),
            "number": number,
            "start_date": raw.get("start_date"),
            "end_date": raw.get("end_date"),
            "description": raw.get("name", ""),
        }

    @staticmethod
    def parse_legislature(raw: dict) -> dict:
        return {
            "_external_id": str(raw.get("id", "")),
            "number": raw.get("number", 0),
            "session_type": SESSION_TYPE_MAP.get(raw.get("type", ""), "ordinary"),
            "start_date": raw.get("start_date"),
            "end_date": raw.get("end_date"),
            "_chamber_type": "deputies",
        }

    @staticmethod
    def parse_session(raw: dict) -> dict:
        return {
            "_external_id": str(raw.get("id", "")),
            "number": raw.get("number", 0),
            "session_type": SESSION_TYPE_MAP.get(raw.get("type", ""), "ordinary"),
            "start_date": raw.get("date") or raw.get("start_date"),
            "end_date": raw.get("end_date"),
            "_chamber_type": "deputies",
        }
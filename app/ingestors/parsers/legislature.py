"""Parsers for Período Legislativo, Legislatura, and Sesión Legislativa payloads.

Three-tier vocabulary (CONTEXT.md + ADR-0016):
- ``parse_legislative_period`` → ``LegislativePeriod`` (4-year cycle).
- ``parse_legislature`` → ``Legislature`` (1-year cycle, kind = ordinaria/extraordinaria).
- ``parse_session`` → ``LegislativeSession`` (single meeting, kind = ordinaria/especial).
"""

LEGISLATURE_KIND_MAP = {
    "Ordinaria": "ordinaria",
    "ordinaria": "ordinaria",
    "Extraordinaria": "extraordinaria",
    "extraordinaria": "extraordinaria",
}

SESSION_KIND_MAP = {
    "Ordinaria": "ordinaria",
    "ordinaria": "ordinaria",
    "Especial": "especial",
    "especial": "especial",
    # Upstream historical rows occasionally carry "Extraordinaria" at the
    # session level; treat that as "especial" since post-2005 a session can
    # only be ordinary or special.
    "Extraordinaria": "especial",
    "extraordinaria": "especial",
}


class LegislatureParser:
    @staticmethod
    def parse_legislative_period(raw: dict) -> dict:
        number = None
        try:
            number = int(raw.get("id", 0))
        except TypeError, ValueError:
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
            "kind": LEGISLATURE_KIND_MAP.get(raw.get("type", ""), "ordinaria"),
            "start_date": raw.get("start_date"),
            "end_date": raw.get("end_date"),
        }

    @staticmethod
    def parse_session(raw: dict, *, legislature_number: int | None = None) -> dict:
        return {
            "_external_id": str(raw.get("id", "")),
            "number": raw.get("number", 0),
            "kind": SESSION_KIND_MAP.get(raw.get("type", ""), "ordinaria"),
            "start_date": raw.get("date") or raw.get("start_date"),
            "end_date": raw.get("end_date"),
            "_legislature_number": legislature_number,
            "_chamber_type": raw.get("chamber_type", "deputies"),
            "_committee_external_id": raw.get("committee_id"),
        }

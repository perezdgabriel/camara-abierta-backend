from app.models.enums import (
    BillOrigin,
    BillStatus,
    BillType,
    ChamberType,
    StageType,
    UrgencyType,
)


def make_initial_bill_payload() -> dict[str, object]:
    return {
        "bulletin_number": "100-06",
        "title": "Proyecto de integracion inicial",
        "summary": "Texto ciudadano inicial.",
        "bill_type": BillType.PROJECT,
        "origin": BillOrigin.EXECUTIVE,
        "status": BillStatus.PENDING,
        "entry_date": "2026-05-01",
        "_origin_chamber_type": ChamberType.SENATE,
        "topics": ["Transparencia", "Probidad"],
        "authors": [],
        "stages": [
            {
                "stage_type": StageType.FIRST_CONSTITUTIONAL_TRAMITE,
                "start_date": "2026-05-02",
                "_chamber_type": ChamberType.SENATE,
                "description": "Ingreso al Senado",
            }
        ],
        "documents": [],
        "_current_urgency_type": UrgencyType.SIMPLE,
    }


def make_updated_bill_payload() -> dict[str, object]:
    return {
        "bulletin_number": "100-06",
        "title": "Proyecto de integracion actualizado",
        "summary": "Texto ciudadano actualizado.",
        "bill_type": BillType.PROJECT,
        "origin": BillOrigin.EXECUTIVE,
        "status": BillStatus.APPROVED,
        "entry_date": "2026-05-01",
        "_origin_chamber_type": ChamberType.SENATE,
        "topics": ["Salud"],
        "authors": [],
        "stages": [
            {
                "stage_type": StageType.SECOND_CONSTITUTIONAL_TRAMITE,
                "start_date": "2026-05-10",
                "_chamber_type": ChamberType.DEPUTIES,
                "description": "Pasa a segundo tramite",
            }
        ],
        "documents": [],
        "_current_urgency_type": UrgencyType.IMMEDIATE,
    }


def make_secondary_bill_payload() -> dict[str, object]:
    return {
        "bulletin_number": "200-06",
        "title": "Proyecto secundario",
        "bill_type": BillType.PROJECT,
        "origin": BillOrigin.DEPUTIES,
        "status": BillStatus.APPROVED,
        "entry_date": "2026-05-03",
        "_origin_chamber_type": ChamberType.DEPUTIES,
        "topics": ["Economia"],
        "authors": [],
        "stages": [
            {
                "stage_type": StageType.FIRST_CONSTITUTIONAL_TRAMITE,
                "start_date": "2026-05-04",
                "_chamber_type": ChamberType.DEPUTIES,
                "description": "Ingreso a la Camara",
            }
        ],
        "documents": [],
        "_current_urgency_type": UrgencyType.SUM,
    }

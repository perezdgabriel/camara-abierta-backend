from app.models.enums import BillOrigin, BillStatus, ChamberType, StageType, UrgencyType

STATUS_MAP = {
    "En tramitacion": BillStatus.PENDING,
    "En tramitación": BillStatus.PENDING,
    "Tramitacion terminada": BillStatus.APPROVED,
    "Tramitación terminada": BillStatus.APPROVED,
    "Aprobado": BillStatus.APPROVED,
    "Rechazado": BillStatus.REJECTED,
    "Archivado": BillStatus.ARCHIVED,
    "Retirado": BillStatus.WITHDRAWN,
    "Inconstitucional": BillStatus.UNCONSTITUTIONAL,
    "Promulgado": BillStatus.ENACTED,
    "Publicado": BillStatus.PUBLISHED,
}

ORIGIN_MAP = {
    "Mensaje": BillOrigin.EXECUTIVE,
    "Mocion": BillOrigin.DEPUTIES,
    "Moción": BillOrigin.DEPUTIES,
    "Indicacion": BillOrigin.DEPUTIES,
    "Indicación": BillOrigin.DEPUTIES,
    "Urgencia": BillOrigin.EXECUTIVE,
    "Oficio": BillOrigin.EXECUTIVE,
}

CHAMBER_MAP = {
    "C.Diputados": ChamberType.DEPUTIES,
    "C. Diputados": ChamberType.DEPUTIES,
    "Camara de Diputados": ChamberType.DEPUTIES,
    "Cámara de Diputados": ChamberType.DEPUTIES,
    "Senado": ChamberType.SENATE,
}

STAGE_TYPE_MAP = {
    "Primer tramite constitucional": StageType.FIRST_CONSTITUTIONAL_TRAMITE,
    "Primer trámite constitucional": StageType.FIRST_CONSTITUTIONAL_TRAMITE,
    "Segundo tramite constitucional": StageType.SECOND_CONSTITUTIONAL_TRAMITE,
    "Segundo trámite constitucional": StageType.SECOND_CONSTITUTIONAL_TRAMITE,
    "Tercer tramite constitucional": StageType.THIRD_CONSTITUTIONAL_TRAMITE,
    "Tercer trámite constitucional": StageType.THIRD_CONSTITUTIONAL_TRAMITE,
    "Comision Mixta": StageType.MIXED_COMMISSION,
    "Comisión Mixta": StageType.MIXED_COMMISSION,
    "Tribunal Constitucional": StageType.CONSTITUTIONAL_TRIBUNAL,
    "Promulgacion": StageType.PROMULGATION,
    "Promulgación": StageType.PROMULGATION,
    "Publicacion": StageType.PUBLICATION,
    "Publicación": StageType.PUBLICATION,
}

URGENCY_MAP = {
    "Simple": UrgencyType.SIMPLE,
    "Suma": UrgencyType.SUM,
    "Discusion inmediata": UrgencyType.IMMEDIATE,
    "Discusión inmediata": UrgencyType.IMMEDIATE,
    "Sin urgencia": None,
    "Sin Urgencia": None,
}


class BillParser:
    @staticmethod
    def parse_bill(raw: dict) -> dict:
        origin_type = ORIGIN_MAP.get(raw.get("initiative", ""), BillOrigin.DEPUTIES)
        origin_chamber_type = CHAMBER_MAP.get(
            raw.get("origin_chamber", ""), ChamberType.DEPUTIES
        )
        urgency_type = URGENCY_MAP.get(raw.get("current_urgency", ""))
        status = STATUS_MAP.get(raw.get("status", ""), BillStatus.PENDING)

        authors = [
            {"name": author.get("legislator", "").strip()}
            for author in raw.get("authors", [])
            if author.get("legislator", "").strip()
        ]
        topics = [
            materia.strip()
            for materia in raw.get("materias", [])
            if materia and materia.strip()
        ]
        stages = BillParser._parse_stages(raw.get("tramitaciones", []))
        documents = BillParser._parse_documents(
            raw.get("informes", []),
            raw.get("comparados", []),
            raw.get("oficios", []),
        )
        return {
            "bulletin_number": raw.get("bulletin", ""),
            "title": (raw.get("title") or "").strip(),
            "entry_date": raw.get("entry_date"),
            "origin_type": origin_type,
            "_origin_chamber_type": origin_chamber_type,
            "status": status,
            "law_number": raw.get("law_number") or "",
            "publication_date": raw.get("publication_date"),
            "message_url": raw.get("message_url") or "",
            "_current_urgency_type": urgency_type,
            "authors": authors,
            "topics": topics,
            "stages": stages,
            "documents": documents,
            "_votaciones": raw.get("votaciones", []),
        }

    @staticmethod
    def _parse_stages(tramitaciones: list[dict]) -> list[dict]:
        return [
            {
                "stage_type": STAGE_TYPE_MAP.get(
                    tram.get("stage", ""), StageType.OTHER
                ),
                "start_date": tram.get("date"),
                "_chamber_type": CHAMBER_MAP.get(tram.get("chamber", "")),
                "description": tram.get("description", ""),
                "_session_ref": tram.get("session", ""),
            }
            for tram in tramitaciones
        ]

    @staticmethod
    def _parse_documents(
        informes: list[dict], comparados: list[dict], oficios: list[dict]
    ) -> list[dict]:
        documents = []
        for informe in informes:
            if informe.get("url"):
                documents.append(
                    {
                        "document_type": "report",
                        "title": informe.get("procedure", ""),
                        "document_url": informe.get("url"),
                        "document_date": informe.get("date"),
                        "_stage_ref": informe.get("stage", ""),
                    }
                )
        for comparado in comparados:
            if comparado.get("url"):
                documents.append(
                    {
                        "document_type": "comparison",
                        "title": comparado.get("text", ""),
                        "document_url": comparado.get("url"),
                        "document_date": None,
                        "_stage_ref": "",
                    }
                )
        for oficio in oficios:
            if oficio.get("url"):
                documents.append(
                    {
                        "document_type": "official_communication",
                        "title": oficio.get("description", ""),
                        "document_url": oficio.get("url"),
                        "document_date": oficio.get("date"),
                        "_stage_ref": oficio.get("stage", ""),
                    }
                )
        return documents

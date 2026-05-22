STATUS_MAP = {
    "En tramitacion": "pending",
    "En tramitación": "pending",
    "Tramitacion terminada": "approved",
    "Tramitación terminada": "approved",
    "Aprobado": "approved",
    "Rechazado": "rejected",
    "Archivado": "archived",
    "Retirado": "withdrawn",
    "Inconstitucional": "unconstitutional",
    "Promulgado": "enacted",
    "Publicado": "published",
}

ORIGIN_MAP = {
    "Mensaje": "executive",
    "Mocion": "deputies",
    "Moción": "deputies",
    "Indicacion": "deputies",
    "Indicación": "deputies",
    "Urgencia": "executive",
    "Oficio": "executive",
}

CHAMBER_MAP = {
    "C.Diputados": "deputies",
    "C. Diputados": "deputies",
    "Camara de Diputados": "deputies",
    "Cámara de Diputados": "deputies",
    "Senado": "senate",
}

STAGE_TYPE_MAP = {
    "Primer tramite constitucional": "Primer trámite constitucional",
    "Primer trámite constitucional": "Primer trámite constitucional",
    "Segundo tramite constitucional": "Segundo trámite constitucional",
    "Segundo trámite constitucional": "Segundo trámite constitucional",
    "Tercer tramite constitucional": "Tercer trámite constitucional",
    "Tercer trámite constitucional": "Tercer trámite constitucional",
    "Comision Mixta": "Comisión Mixta",
    "Comisión Mixta": "Comisión Mixta",
    "Tribunal Constitucional": "Tribunal Constitucional",
    "Promulgacion": "Promulgación",
    "Promulgación": "Promulgación",
    "Publicacion": "Publicación",
    "Publicación": "Publicación",
}

URGENCY_MAP = {
    "Simple": "simple",
    "Suma": "sum",
    "Discusion inmediata": "immediate",
    "Discusión inmediata": "immediate",
    "Sin urgencia": None,
    "Sin Urgencia": None,
}


class BillParser:
    @staticmethod
    def parse_bill(raw: dict) -> dict:
        origin_type = ORIGIN_MAP.get(raw.get("initiative", ""), "deputies")
        origin_chamber_type = CHAMBER_MAP.get(raw.get("origin_chamber", ""), "deputies")
        urgency_type = URGENCY_MAP.get(raw.get("current_urgency", ""))
        status = STATUS_MAP.get(raw.get("status", ""), "pending")

        authors = [
            {"name": author.get("legislator", "").strip()}
            for author in raw.get("authors", [])
            if author.get("legislator", "").strip()
        ]
        topics = [materia.strip() for materia in raw.get("materias", []) if materia and materia.strip()]
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
                "stage_type": STAGE_TYPE_MAP.get(tram.get("stage", ""), "other"),
                "start_date": tram.get("date"),
                "_chamber_type": CHAMBER_MAP.get(tram.get("chamber", "")),
                "description": tram.get("description", ""),
                "_session_ref": tram.get("session", ""),
            }
            for tram in tramitaciones
        ]

    @staticmethod
    def _parse_documents(informes: list[dict], comparados: list[dict], oficios: list[dict]) -> list[dict]:
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
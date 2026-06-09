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


# restsil bill-status codes (estado=) used for server-side filtering. The
# *authoritative* BillStatus on a row still comes from the wspublico detail
# fetch (``parse_bill`` → ``STATUS_MAP``); this dict is only consulted when
# we want to translate a restsil-discovery row's code into a BillStatus for
# logging / metrics. Codes "I" / "E" have no clean enum bucket — they collapse
# to REJECTED for reporting only.
RESTSIL_STATUS_MAP = {
    "T": BillStatus.PENDING,
    "V": BillStatus.ARCHIVED,
    "I": BillStatus.REJECTED,
    "N": BillStatus.UNCONSTITUTIONAL,
    "L": BillStatus.PUBLISHED,
    "R": BillStatus.REJECTED,
    "E": BillStatus.REJECTED,
}

# restsil origin-chamber codes — ``PROYORIGEN`` is a single letter.
RESTSIL_CHAMBER_MAP = {
    "S": ChamberType.SENATE,
    "D": ChamberType.DEPUTIES,
}

# restsil initiative codes — ``PROYINICIATIVA`` is the integer pair to the
# textual ``PROYDESCINICIATIVA`` ("Mensaje" / "Moción").
RESTSIL_INICIATIVA_MAP = {
    30: BillOrigin.EXECUTIVE,  # Mensaje
    31: BillOrigin.DEPUTIES,  # Moción
}


class BillParser:
    @staticmethod
    def parse_restsil_summary(row: dict) -> dict:
        """Normalize one ``buscarProyectosDeLey`` row for discovery dispatch.

        Returns only the fields the discovery loop in ``run_ingest_bills``
        needs to make a fan-out decision; the full detail still comes from
        wspublico ``tramitacion.php?boletin=X`` via ``parse_bill``.
        """
        bulletin = (row.get("PROYNUMEROBOLETIN") or "").strip()
        chamber_code = (row.get("PROYORIGEN") or "").strip()
        iniciativa = row.get("PROYINICIATIVA")
        return {
            "bulletin_number": bulletin,
            "entry_date": BillParser._restsil_date(row.get("PROYFECHAINGRESO")),
            "origin_chamber_type": RESTSIL_CHAMBER_MAP.get(chamber_code),
            "origin_type": RESTSIL_INICIATIVA_MAP.get(
                int(iniciativa) if iniciativa is not None else -1
            ),
            "summary_title": (row.get("PROYSUMA") or "").strip(),
            "stage_label": (row.get("ETAPA") or "").strip() or None,
            "substage_label": (row.get("SUBETAPA") or "").strip() or None,
            "urgency_label": (row.get("PROYURGENCIA") or "").strip() or None,
            "authors_text": (row.get("AUTORES") or "").strip(),
            "refundidos": (row.get("REFUNDIDOS") or "").strip() or None,
            "proy_id": row.get("PROYID") or row.get("ID_PROYECTO"),
        }

    @staticmethod
    def _restsil_date(value: str | None) -> str | None:
        if not value:
            return None
        import re

        match = re.match(r"(\d{2})/(\d{2})/(\d{4})", value.strip())
        if match:
            return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
        return None

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
        tramitaciones = raw.get("tramitaciones", [])
        topics = [
            materia.strip()
            for materia in raw.get("materias", [])
            if materia and materia.strip()
        ]
        stages = BillParser._parse_stages(tramitaciones)
        events = BillParser._parse_events(tramitaciones)
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
            "events": events,
            "documents": documents,
            "_votaciones": raw.get("votaciones", []),
        }

    @staticmethod
    def parse_opendata_enrichment(raw: dict) -> dict:
        sponsoring_ministries = [
            {
                "source_id": ministry.get("id"),
                "name": (ministry.get("name") or "").strip() or None,
            }
            for ministry in raw.get("sponsoring_ministries", [])
            if ministry.get("id") is not None or (ministry.get("name") or "").strip()
        ]
        return {
            "sponsoring_ministries": sponsoring_ministries,
            "_camara_votaciones": raw.get("chamber_votes", []),
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
    def _parse_events(tramitaciones: list[dict]) -> list[dict]:
        events: list[dict] = []
        for tram in tramitaciones:
            event_date = tram.get("date")
            raw_description = (tram.get("description") or "").strip()
            raw_stage = (tram.get("stage") or "").strip()
            title = raw_description or raw_stage
            if not event_date or not title:
                continue

            description = raw_stage if raw_stage and raw_stage != title else None
            events.append(
                {
                    "event_date": event_date,
                    "title": title,
                    "description": description,
                    "_chamber_type": CHAMBER_MAP.get(tram.get("chamber", "")),
                }
            )
        return events

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

import re

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


def _clean_event_title(raw: str) -> str:
    """Normalize garbled upstream tramitación titles.

    RESTSIL composes some titles from a template plus a committee name and
    ships them broken, e.g.:

      "Primer informe de comisiónde Trabajo  de Trabajo"      (glued + duplicated)
      "Primer informe de comisiónMedio Ambiente  Medio Ambiente"
      "Cuenta de proyecto. Pasa a Comisión. de Seguridad Pública"

    Rules are deliberately narrow so clean titles pass through unchanged.
    """
    title = raw.strip()
    # Committee name glued directly onto "comisión" without a separator.
    title = re.sub(r"(?i)(comisión)(?=[a-záéíóúñA-ZÁÉÍÓÚÑ])", r"\1 ", title)
    # Stray period between "Comisión" and its "de <committee>" complement.
    title = re.sub(r"(?i)(comisión)\.\s+(de\s)", r"\1 \2", title)
    # Duplicated committee suffix after a run of 2+ spaces:
    # "<head that already ends with X>  (de )?X" -> keep only the head.
    split = re.search(r"\s{2,}", title)
    if split:
        head = title[: split.start()].rstrip()
        tail = title[split.end() :].strip()

        def norm(s: str) -> str:
            return re.sub(r"\s+", " ", s).lower()

        h, t = norm(head), norm(tail)
        bare_t = t[3:] if t.startswith("de ") else t
        if bare_t and (h.endswith(t) or h.endswith(f"de {bare_t}") or h.endswith(bare_t)):
            title = head
    return re.sub(r"\s+", " ", title).strip()


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
            "stages": stages,
            "events": events,
            "documents": documents,
            "_votaciones": raw.get("votaciones", []),
        }

    @staticmethod
    def parse_restsil_detail(
        raw: dict, *, bulletin: str, authors_text: str | None = None
    ) -> dict:
        """Normalize ``proyectos/tramitacionProyecto/{proy_id}`` into the
        DB-shape dict that :meth:`parse_bill` produces.

        Wired in when ``settings.ingestor_bill_detail_source == "restsil"``
        (ADR-0020). The contract matches :meth:`parse_bill` so
        ``upsert_bill`` and all ``_reconcile_*`` writers are untouched.

        Three sections of the upstream payload are consumed:

        - ``infoProyecto`` — metadata (suma, iniciativa, origen, urgencia,
          estado-as-etapa, subetapa, ley nro, fecha publicación).
        - ``etapasProyecto`` — one stage per upstream etapa; the first
          ``link_mensaje`` populates ``message_url``.
        - ``tramitacionProyecto`` — one event per row; per-row
          ``LINK_INFORME / LINK_COMPARADO / LINK_OFICIO`` populate
          ``BillDocument``. ``LINK_MENSAJE`` and ``LINK_INDICACION`` are
          intentionally dropped (mensaje URL is captured at the bill level;
          indicaciones don't map to any current ``document_type``).

        Two sources are intentionally **not** populated here (see ADR-0020):

        - ``BillUrgency`` history — ``current_urgency`` still flows to
          ``Bill.current_urgency``, but per-event urgency rows are dropped.
        - ``_votaciones`` — owned end-to-end by the dedicated Senate / Chamber
          vote tasks per ADR-0013.

        Bill topics (``Bill.topics``) are not part of this contract at all —
        per ADR-0021 they're LLM-curated exclusively, via
        ``apply_bill_topic_classification``, never ingested from upstream.

        Authors come from the discovery feed (``AUTORES`` slash-separated
        canonical names) and are passed in via ``authors_text``.
        """
        info = raw.get("infoProyecto") or {}
        etapas = raw.get("etapasProyecto") or []
        tramitaciones = raw.get("tramitacionProyecto") or []

        initiative = (info.get("Iniciativa") or "").strip()
        origin_type = ORIGIN_MAP.get(initiative, BillOrigin.DEPUTIES)
        origin_chamber_label = (info.get("Origen") or "").strip()
        origin_chamber_type = CHAMBER_MAP.get(
            origin_chamber_label, ChamberType.DEPUTIES
        )

        urgency_label = (info.get("Urgencia") or "").strip()
        urgency_type = URGENCY_MAP.get(urgency_label)

        etapa_label = (info.get("EstadoProyecto") or "").strip()
        # Restsil's "EstadoProyecto" is the etapa, not a discrete status
        # field. Terminal labels ("Tramitación terminada", "Archivado",
        # "Inconstitucional", "Publicado", "Rechazado") map cleanly to
        # BillStatus; anything else (in-progress trámites) is PENDING.
        status = STATUS_MAP.get(etapa_label, BillStatus.PENDING)
        if status == BillStatus.PENDING and info.get("leynro"):
            # leynro is only assigned once a law number is published.
            status = BillStatus.PUBLISHED

        message_url = ""
        for etapa in etapas:
            link = (etapa.get("link_mensaje") or "").strip()
            if link:
                message_url = link
                break

        # First etapa's fechaInicio is the bill's entry date in slash form.
        entry_date: str | None = None
        if etapas:
            entry_date = BillParser._restsil_date(
                (etapas[0].get("fechaInicio") or "").strip() or None
            )

        publication_date = BillParser._restsil_date(
            (info.get("DiarioOficial") or "").strip() or None
        )

        authors = BillParser._parse_authors_text(authors_text)

        stages = [
            {
                "stage_type": STAGE_TYPE_MAP.get(
                    (etapa.get("etapa") or "").strip(), StageType.OTHER
                ),
                "start_date": BillParser._restsil_date(
                    (etapa.get("fechaInicio") or "").strip() or None
                ),
                "_chamber_type": CHAMBER_MAP.get(
                    (etapa.get("camDelTramite") or "").strip()
                ),
                "description": "",
                "_session_ref": str(etapa.get("sxetid") or ""),
            }
            for etapa in etapas
        ]

        events: list[dict] = []
        documents: list[dict] = []
        for row in tramitaciones:
            event_date = BillParser._restsil_date(
                (row.get("TRAMFECHA") or "").strip() or None
            )
            title = _clean_event_title(
                (row.get("TEXTODESCRIPTIVOTRAMITACION") or "").strip()
                or (row.get("SUBEDESCRIPCION") or "").strip()
            )
            stage_label = (row.get("ETAPDESCRIPCION") or "").strip()
            chamber_label = (row.get("CAMDELTRAMITE") or "").strip()
            chamber_type = CHAMBER_MAP.get(chamber_label)

            if event_date and title:
                events.append(
                    {
                        "event_date": event_date,
                        "title": title,
                        "description": stage_label
                        if stage_label and stage_label != title
                        else None,
                        "_chamber_type": chamber_type,
                    }
                )

            link_informe = (row.get("LINK_INFORME") or "").strip()
            link_comparado = (row.get("LINK_COMPARADO") or "").strip()
            link_oficio = (row.get("LINK_OFICIO") or "").strip()
            row_subdesc = (row.get("SUBEDESCRIPCION") or "").strip()
            row_tramdesc = (row.get("TRAMDESCRIPCION") or "").strip()

            if link_informe:
                documents.append(
                    {
                        "document_type": "report",
                        "title": row_subdesc,
                        "document_url": link_informe,
                        "document_date": event_date,
                        "_stage_ref": stage_label,
                    }
                )
            if link_comparado:
                documents.append(
                    {
                        "document_type": "comparison",
                        "title": row_tramdesc or row_subdesc,
                        "document_url": link_comparado,
                        "document_date": event_date,
                        "_stage_ref": stage_label,
                    }
                )
            if link_oficio:
                documents.append(
                    {
                        "document_type": "official_communication",
                        "title": row_tramdesc or row_subdesc,
                        "document_url": link_oficio,
                        "document_date": event_date,
                        "_stage_ref": stage_label,
                    }
                )

        return {
            "bulletin_number": bulletin,
            "title": (info.get("Suma") or "").strip(),
            "entry_date": entry_date,
            "origin_type": origin_type,
            "_origin_chamber_type": origin_chamber_type,
            "status": status,
            "law_number": (info.get("leynro") or ""),
            "publication_date": publication_date,
            "message_url": message_url,
            "_current_urgency_type": urgency_type,
            "authors": authors,
            "stages": stages,
            "events": events,
            "documents": documents,
            "_votaciones": [],
        }

    @staticmethod
    def _parse_authors_text(authors_text: str | None) -> list[dict]:
        """Split the ``AUTORES`` string from the restsil discovery feed.

        Upstream format is ``Apellido_paterno Apellido_materno, Nombres``
        names joined by ``/``. The canonical-key matcher in
        ``_reconcile_authorships`` already normalises this form (per the
        BillAuthorship section of ``CONTEXT.md``), so we just split, strip,
        and drop empties.
        """
        if not authors_text:
            return []
        return [
            {"name": name} for raw in authors_text.split("/") if (name := raw.strip())
        ]

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
            title = _clean_event_title(raw_description or raw_stage)
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

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.models.base import global_sync_version_seq
from app.models.core import Circumscription, Commune, District, Province, Region, Topic
from app.models.diario_oficial import OfficialGazetteNorm, Regulation, RegulationStage
from app.models.enums import (
    BillOrigin,
    BillStatus,
    BillType,
    ChamberType,
    CommitteeType,
    StageType,
    UrgencyType,
    VoteChoice,
    VotingResult,
    VotingType,
)
from app.models.legislature import (
    Chamber,
    Committee,
    CommitteeMembership,
    LegislativePeriod,
    LegislativeSession,
    Legislator,
    LegislatorTerm,
    PoliticalParty,
)
from app.models.proyecto import (
    Bill,
    BillAuthorship,
    BillDocument,
    BillStage,
    BillUrgency,
)
from app.models.votacion import Vote, VotingSession

UNKNOWN_START_DATE = date(1900, 1, 1)

DISTRICT_REGION_MAP: dict[int, int] = {
    1: 1,
    2: 1,
    3: 2,
    4: 3,
    5: 4,
    6: 4,
    7: 5,
    8: 5,
    9: 5,
    10: 5,
    11: 5,
    12: 13,
    13: 13,
    14: 13,
    15: 13,
    16: 13,
    17: 13,
    18: 13,
    19: 13,
    20: 6,
    21: 6,
    22: 7,
    23: 7,
    24: 16,
    25: 8,
    26: 8,
    27: 9,
    28: 14,
}


def _next_sync_value(db: Session) -> int:
    return db.execute(select(global_sync_version_seq.next_value())).scalar_one()


def _touch_syncable(db: Session, obj: Any) -> None:
    obj.updated_at = datetime.now(timezone.utc)
    obj.sync_version = _next_sync_value(db)


def _normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:100] or "sin-tema"


def _parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        match = re.match(r"(\d{2})/(\d{2})/(\d{4})", text)
        if match:
            return date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    return None


def _parse_datetime(value: str | datetime | date | None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    parsed_date = _parse_date(value)
    if parsed_date is not None:
        return datetime.combine(parsed_date, datetime.min.time(), tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _coerce_enum(enum_type: type[Any], value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, enum_type):
        return value
    raw_value = value.value if hasattr(value, "value") else value
    try:
        return enum_type(str(raw_value).strip().lower())
    except ValueError:
        return default


def _normalized_chamber_for_table(value: str | ChamberType | None) -> ChamberType:
    normalized = _coerce_enum(ChamberType, value)
    if normalized is not None:
        return normalized

    lowered = str(value or "").strip().lower()
    if lowered in {"senator", "senate", "senado"}:
        return ChamberType.SENATE
    return ChamberType.DEPUTIES


def _get_or_create_chamber(
    db: Session, chamber_type: str | ChamberType | None
) -> Chamber:
    normalized = _normalized_chamber_for_table(chamber_type)
    chamber = db.execute(
        select(Chamber).where(Chamber.chamber_type == normalized)
    ).scalar_one_or_none()
    if chamber is not None:
        return chamber

    chamber = Chamber(
        chamber_type=normalized,
        name="Senado de la Republica"
        if normalized == ChamberType.SENATE
        else "Camara de Diputadas y Diputados",
        total_seats=50 if normalized == ChamberType.SENATE else 155,
    )
    db.add(chamber)
    db.flush()
    return chamber


def _get_or_create_party(db: Session, party_name: str | None) -> PoliticalParty | None:
    name = (party_name or "").strip()
    if not name:
        return None

    party = db.execute(
        select(PoliticalParty).where(func.lower(PoliticalParty.name) == name.lower())
    ).scalar_one_or_none()
    if party is not None:
        return party

    abbreviation = re.sub(r"\s+", " ", name).strip()[:20]
    party = PoliticalParty(
        name=name[:200], abbreviation=abbreviation or name[:20], is_active=True
    )
    db.add(party)
    try:
        db.flush()
        return party
    except Exception:
        db.rollback()
        existing = db.execute(
            select(PoliticalParty).where(
                func.lower(PoliticalParty.name) == name.lower()
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise


def _get_or_create_circumscription(
    db: Session, number: int | None, name: str | None
) -> Circumscription | None:
    if not number:
        return None
    circumscription = db.execute(
        select(Circumscription).where(Circumscription.number == number)
    ).scalar_one_or_none()
    if circumscription is not None:
        return circumscription
    circumscription = Circumscription(
        number=number, name=(name or f"Circunscripcion {number}")[:200]
    )
    db.add(circumscription)
    db.flush()
    return circumscription


def _reconcile_topics(db: Session, bill: Bill, topic_names: list[str]) -> bool:
    desired_topics: list[Topic] = []
    changed = False
    for topic_name in topic_names:
        name = topic_name.strip()
        if not name:
            continue
        slug = _normalize_slug(name)
        topic = db.execute(select(Topic).where(Topic.slug == slug)).scalar_one_or_none()
        if topic is None:
            topic = Topic(name=name[:100], slug=slug)
            db.add(topic)
            db.flush()
            changed = True
        elif topic.name != name[:100]:
            topic.name = name[:100]
            _touch_syncable(db, topic)
            changed = True
        desired_topics.append(topic)

    current_ids = {topic.id for topic in bill.topics}
    desired_ids = {topic.id for topic in desired_topics}
    if current_ids != desired_ids:
        bill.topics = desired_topics
        changed = True
    return changed


def _reconcile_authorships(
    db: Session, bill: Bill, authors: list[dict[str, Any]]
) -> bool:
    desired_legislator_ids: set[int] = set()
    for author in authors:
        name = (author.get("name") or "").strip()
        if not name:
            continue
        legislator_id = db.execute(
            select(Legislator.id).where(
                func.lower(Legislator.full_name) == name.lower()
            )
        ).scalar_one_or_none()
        if legislator_id is not None:
            desired_legislator_ids.add(legislator_id)

    current_by_legislator = {auth.legislator_id: auth for auth in bill.authorships}
    changed = False
    for legislator_id, existing_authorship in list(current_by_legislator.items()):
        if legislator_id not in desired_legislator_ids:
            db.delete(existing_authorship)
            changed = True

    for legislator_id in desired_legislator_ids:
        authorship = current_by_legislator.get(legislator_id)
        if authorship is None:
            db.add(
                BillAuthorship(
                    bill_id=bill.id, legislator_id=legislator_id, author_type="author"
                )
            )
            changed = True
        elif authorship.author_type != "author":
            authorship.author_type = "author"
            _touch_syncable(db, authorship)
            changed = True
    return changed


def _stage_key(
    stage_type: StageType,
    start_date_value: date | None,
    chamber_id: int | None,
    description: str,
) -> tuple[Any, ...]:
    return (stage_type, start_date_value, chamber_id, description.strip())


def _document_key(
    document_type: str, title: str, document_url: str, document_date: date | None
) -> tuple[Any, ...]:
    return (document_type, title.strip(), document_url.strip(), document_date)


def _reconcile_stages(
    db: Session, bill: Bill, stages: list[dict[str, Any]]
) -> tuple[bool, int | None]:
    current_by_key = {
        _stage_key(
            stage.stage_type,
            stage.start_date,
            stage.chamber_id,
            stage.description or "",
        ): stage
        for stage in bill.stages
    }
    desired_keys: set[tuple[Any, ...]] = set()
    changed = False
    current_chamber_id: int | None = None

    for index, payload in enumerate(stages):
        start_date_value = _parse_date(payload.get("start_date"))
        if start_date_value is None:
            continue
        chamber = None
        if payload.get("_chamber_type"):
            chamber = _get_or_create_chamber(db, payload["_chamber_type"])
        description = (payload.get("description") or "").strip()
        stage_type = _coerce_enum(StageType, payload.get("stage_type"), StageType.OTHER)
        key = _stage_key(
            stage_type, start_date_value, chamber.id if chamber else None, description
        )
        desired_keys.add(key)
        is_current = index == len(stages) - 1
        current_chamber_id = (
            chamber.id if is_current and chamber is not None else current_chamber_id
        )

        stage = current_by_key.get(key)
        if stage is None:
            db.add(
                BillStage(
                    bill_id=bill.id,
                    stage_type=stage_type,
                    chamber_id=chamber.id if chamber else None,
                    start_date=start_date_value,
                    description=description or None,
                    is_current=is_current,
                )
            )
            changed = True
            continue

        field_changed = False
        if stage.is_current != is_current:
            stage.is_current = is_current
            field_changed = True
        if (stage.description or "") != description:
            stage.description = description or None
            field_changed = True
        if stage.chamber_id != (chamber.id if chamber else None):
            stage.chamber_id = chamber.id if chamber else None
            field_changed = True
        if field_changed:
            _touch_syncable(db, stage)
            changed = True

    for key, stage in list(current_by_key.items()):
        if key not in desired_keys:
            db.delete(stage)
            changed = True
    return changed, current_chamber_id


def _reconcile_documents(
    db: Session, bill: Bill, documents: list[dict[str, Any]]
) -> bool:
    current_by_key = {
        _document_key(
            doc.document_type, doc.title, doc.document_url or "", doc.document_date
        ): doc
        for doc in bill.documents
    }
    desired_keys: set[tuple[Any, ...]] = set()
    changed = False

    for payload in documents:
        document_type = (payload.get("document_type") or "other")[:20]
        title = (payload.get("title") or "").strip()[:500]
        document_url = (payload.get("document_url") or "").strip()[:500]
        document_date = _parse_date(payload.get("document_date"))
        key = _document_key(document_type, title, document_url, document_date)
        desired_keys.add(key)
        document = current_by_key.get(key)
        if document is None:
            db.add(
                BillDocument(
                    bill_id=bill.id,
                    document_type=document_type,
                    title=title,
                    document_url=document_url or None,
                    document_date=document_date,
                )
            )
            changed = True

    for key, document in list(current_by_key.items()):
        if key not in desired_keys:
            db.delete(document)
            changed = True
    return changed


def _reconcile_urgencies(
    db: Session,
    bill: Bill,
    urgency_type: str | UrgencyType | None,
    chamber_id: int | None,
    entry_date_value: date | None,
) -> bool:
    changed = False
    desired_key = None
    normalized_urgency = _coerce_enum(UrgencyType, urgency_type)
    if normalized_urgency and entry_date_value is not None and chamber_id is not None:
        desired_key = (normalized_urgency, chamber_id, entry_date_value)

    matched = None
    for urgency in bill.urgencies:
        key = (urgency.urgency_type, urgency.chamber_id, urgency.entry_date)
        should_be_active = key == desired_key
        if should_be_active:
            matched = urgency
        if urgency.is_active != should_be_active:
            urgency.is_active = should_be_active
            _touch_syncable(db, urgency)
            changed = True

    if desired_key and matched is None:
        db.add(
            BillUrgency(
                bill_id=bill.id,
                urgency_type=normalized_urgency,
                chamber_id=chamber_id,
                entry_date=entry_date_value,
                is_active=True,
            )
        )
        changed = True
    return changed


def _reconcile_terms(
    db: Session, legislator: Legislator, militancias: list[dict[str, Any]]
) -> bool:
    chamber = _get_or_create_chamber(db, legislator.chamber_type)
    desired: dict[tuple[Any, ...], dict[str, Any]] = {}
    for militancia in militancias:
        start_date_value = _parse_date(militancia.get("start_date"))
        if start_date_value is None:
            continue
        party = _get_or_create_party(
            db,
            militancia.get("party_name") or militancia.get("party_alias"),
        )
        period = (
            db.execute(
                select(LegislativePeriod)
                .where(LegislativePeriod.start_date <= start_date_value)
                .order_by(LegislativePeriod.start_date.desc())
            )
            .scalars()
            .first()
        )
        if period is None:
            continue
        desired[(start_date_value, chamber.id)] = {
            "period_id": period.id,
            "party_id": party.id if party else None,
            "end_date": _parse_date(militancia.get("end_date")),
        }

    current_by_key = {
        (term.start_date, term.chamber_id): term for term in legislator.terms
    }
    changed = False
    for key, existing_term in list(current_by_key.items()):
        if key not in desired:
            db.delete(existing_term)
            changed = True

    for key, payload in desired.items():
        term = current_by_key.get(key)
        if term is None:
            db.add(
                LegislatorTerm(
                    legislator_id=legislator.id,
                    period_id=payload["period_id"],
                    chamber_id=chamber.id,
                    party_id=payload["party_id"],
                    start_date=key[0],
                    end_date=payload["end_date"],
                )
            )
            changed = True
            continue
        field_changed = False
        if term.period_id != payload["period_id"]:
            term.period_id = payload["period_id"]
            field_changed = True
        if term.party_id != payload["party_id"]:
            term.party_id = payload["party_id"]
            field_changed = True
        if term.end_date != payload["end_date"]:
            term.end_date = payload["end_date"]
            field_changed = True
        if field_changed:
            _touch_syncable(db, term)
            changed = True
    return changed


def _reconcile_committee_memberships(
    db: Session, committee: Committee, members: list[dict[str, Any]]
) -> bool:
    desired: dict[tuple[Any, ...], dict[str, Any]] = {}
    for member in members:
        bcn_id = member.get("bcn_id")
        if not bcn_id:
            continue
        legislator = db.execute(
            select(Legislator).where(Legislator.bcn_id == bcn_id)
        ).scalar_one_or_none()
        if legislator is None:
            continue
        start_date_value = _parse_date(member.get("start_date")) or UNKNOWN_START_DATE
        key = (legislator.id, member.get("role") or "member", start_date_value)
        desired[key] = {"end_date": _parse_date(member.get("end_date"))}

    current_by_key = {
        (membership.legislator_id, membership.role, membership.start_date): membership
        for membership in committee.memberships
    }
    changed = False

    for key, existing_membership in list(current_by_key.items()):
        if key not in desired:
            db.delete(existing_membership)
            changed = True

    for key, payload in desired.items():
        membership = current_by_key.get(key)
        if membership is None:
            db.add(
                CommitteeMembership(
                    committee_id=committee.id,
                    legislator_id=key[0],
                    role=key[1],
                    start_date=key[2],
                    end_date=payload["end_date"],
                )
            )
            changed = True
            continue
        if membership.end_date != payload["end_date"]:
            membership.end_date = payload["end_date"]
            _touch_syncable(db, membership)
            changed = True
    return changed


def _reconcile_votes(
    db: Session, voting_session: VotingSession, individual_votes: list[dict[str, Any]]
) -> bool:
    desired: dict[int, VoteChoice] = {}
    for payload in individual_votes:
        legislator_name = (
            payload.get("_legislator_name") or payload.get("legislator_name") or ""
        ).strip()
        if not legislator_name:
            continue
        legislator_id = db.execute(
            select(Legislator.id).where(
                func.lower(Legislator.full_name) == legislator_name.lower()
            )
        ).scalar_one_or_none()
        if legislator_id is None:
            continue
        desired[legislator_id] = _coerce_enum(
            VoteChoice, payload.get("vote"), VoteChoice.ABSENT
        )

    current_by_legislator = {vote.legislator_id: vote for vote in voting_session.votes}
    changed = False

    for legislator_id, existing_vote in list(current_by_legislator.items()):
        if legislator_id not in desired:
            db.delete(existing_vote)
            changed = True

    for legislator_id, vote_value in desired.items():
        vote = current_by_legislator.get(legislator_id)
        if vote is None:
            db.add(
                Vote(
                    voting_session_id=voting_session.id,
                    legislator_id=legislator_id,
                    vote=vote_value,
                )
            )
            changed = True
        elif vote.vote != vote_value:
            vote.vote = vote_value
            _touch_syncable(db, vote)
            changed = True
    return changed


def upsert_norma(
    db: Session,
    *,
    cve: str,
    date_value: str | date,
    title: str,
    pdf_url: str | None,
    edition: str | None,
    branch: str | None,
    ministry: str | None,
    organ: str | None,
    highlight: dict[str, Any] | None,
) -> OfficialGazetteNorm:
    payload = highlight or {}
    values = {
        "date": _parse_date(date_value) or date.today(),
        "edition": edition,
        "branch": branch,
        "ministry": ministry,
        "organ": organ,
        "title": title,
        "pdf_url": pdf_url,
        "cve": cve,
        "explanation": payload.get("resumen_ejecutivo") or "",
        "titulo_amigable": payload.get("titulo_amigable"),
        "resumen_ejecutivo": payload.get("resumen_ejecutivo"),
        "puntos_clave": payload.get("puntos_clave") or [],
        "beneficiarios": payload.get("beneficiarios"),
        "categoria_ia": payload.get("categoria"),
        "importancia_ciudadana": payload.get("importancia_ciudadana"),
    }
    insert_stmt = pg_insert(OfficialGazetteNorm).values(**values)
    norma_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["cve"],
            set_={
                **values,
                "updated_at": func.now(),
                "sync_version": global_sync_version_seq.next_value(),
            },
        ).returning(OfficialGazetteNorm.id)
    ).scalar_one()
    norma = db.get(OfficialGazetteNorm, norma_id)
    if norma is None:
        raise RuntimeError(f"Failed to load official gazette norm id={norma_id}")
    return norma


def compute_reglamento_fingerprint(data: dict[str, Any]) -> str:
    payload = {
        "subsecretaria": data.get("subsecretaria"),
        "materia": data.get("materia"),
        "fecha_ingreso": str(_parse_date(data.get("fecha_ingreso")) or ""),
        "estado": data.get("estado"),
        "reingresado": bool(data.get("reingresado", False)),
        "etapas": data.get("etapas") or [],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upsert_reglamento(db: Session, data: dict[str, Any]) -> Regulation:
    fingerprint = data.get("content_fingerprint") or compute_reglamento_fingerprint(
        data
    )
    existing = db.execute(
        select(Regulation)
        .options(selectinload(Regulation.etapas))
        .with_for_update()
        .where(Regulation.numero == data["numero"])
        .where(Regulation.anio == data["anio"])
        .where(Regulation.ministerio == data["ministerio"])
        .where(Regulation.categoria == data["categoria"])
    ).scalar_one_or_none()

    if existing is None:
        reglamento = Regulation(
            numero=data["numero"],
            anio=data["anio"],
            ministerio=data["ministerio"],
            subsecretaria=data.get("subsecretaria"),
            materia=data.get("materia"),
            fecha_ingreso=_parse_date(data.get("fecha_ingreso")),
            estado=data.get("estado"),
            categoria=data["categoria"],
            reingresado=bool(data.get("reingresado", False)),
            content_fingerprint=fingerprint,
        )
        db.add(reglamento)
        db.flush()
    else:
        reglamento = existing
        if existing.content_fingerprint == fingerprint:
            return reglamento
        reglamento.subsecretaria = data.get("subsecretaria")
        reglamento.materia = data.get("materia")
        reglamento.fecha_ingreso = _parse_date(data.get("fecha_ingreso"))
        reglamento.estado = data.get("estado")
        reglamento.reingresado = bool(data.get("reingresado", False))
        reglamento.content_fingerprint = fingerprint
        _touch_syncable(db, reglamento)
        db.execute(
            delete(RegulationStage).where(
                RegulationStage.reglamento_id == reglamento.id
            )
        )

    for etapa in data.get("etapas") or []:
        db.add(
            RegulationStage(
                reglamento_id=reglamento.id,
                etapa=etapa.get("etapa"),
                fecha=_parse_date(etapa.get("fecha")),
                accion=etapa.get("accion"),
                sector=etapa.get("sector"),
                observaciones=etapa.get("observaciones"),
                documento=etapa.get("documento"),
                documento_url=etapa.get("documento_url"),
            )
        )
    db.flush()
    return reglamento


TERMINAL_STATUSES = frozenset(
    {
        BillStatus.PUBLISHED,
        BillStatus.ENACTED,
        BillStatus.REJECTED,
        BillStatus.ARCHIVED,
        BillStatus.WITHDRAWN,
    }
)


def upsert_bill(db: Session, data: dict[str, Any]) -> tuple[Bill, dict[str, Any]]:
    existing = db.execute(
        select(Bill.id, Bill.status).where(
            Bill.bulletin_number == data["bulletin_number"]
        )
    ).first()
    is_new = existing is None
    old_status = existing.status if existing is not None else None
    new_status = _coerce_enum(BillStatus, data.get("status"), BillStatus.PENDING)
    already_terminal = existing is not None and existing.status in TERMINAL_STATUSES

    origin_chamber = _get_or_create_chamber(
        db, data.get("_origin_chamber_type") or data.get("origin_chamber_type")
    )
    entry_date_value = _parse_date(data.get("entry_date")) or date.today()
    bill_type = _coerce_enum(BillType, data.get("bill_type"), BillType.PROJECT)
    origin = _coerce_enum(
        BillOrigin,
        data.get("origin_type") or data.get("origin"),
        BillOrigin.DEPUTIES,
    )

    insert_stmt = pg_insert(Bill).values(
        bulletin_number=data["bulletin_number"],
        title=(data.get("title") or "")[:500],
        summary=(data.get("summary") or None),
        bill_type=bill_type,
        origin=origin,
        status=new_status,
        entry_date=entry_date_value,
        publication_date=_parse_date(data.get("publication_date")),
        law_number=(data.get("law_number") or "")[:50] or None,
        full_text_url=(data.get("message_url") or data.get("full_text_url") or "")[:500]
        or None,
        origin_chamber_id=origin_chamber.id,
    )
    bill_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["bulletin_number"],
            set_={
                "title": insert_stmt.excluded.title,
                "summary": insert_stmt.excluded.summary,
                "bill_type": insert_stmt.excluded.bill_type,
                "origin": insert_stmt.excluded.origin,
                "status": insert_stmt.excluded.status,
                "entry_date": insert_stmt.excluded.entry_date,
                "publication_date": insert_stmt.excluded.publication_date,
                "law_number": insert_stmt.excluded.law_number,
                "full_text_url": insert_stmt.excluded.full_text_url,
                "origin_chamber_id": insert_stmt.excluded.origin_chamber_id,
                "updated_at": func.now(),
                "sync_version": global_sync_version_seq.next_value(),
            },
        ).returning(Bill.id)
    ).scalar_one()

    if already_terminal:
        db.flush()
        bill = db.execute(select(Bill).where(Bill.id == bill_id)).scalar_one()
        status_changed = old_status != new_status
        return bill, {
            "is_new": False,
            "status_changed": status_changed,
            "stage_changed": False,
            "old_status": old_status,
            "new_status": new_status,
        }

    bill = db.execute(
        select(Bill)
        .options(
            selectinload(Bill.topics),
            selectinload(Bill.authorships),
            selectinload(Bill.stages),
            selectinload(Bill.documents),
            selectinload(Bill.urgencies),
        )
        .where(Bill.id == bill_id)
    ).scalar_one()

    changed = False
    changed |= _reconcile_topics(db, bill, data.get("topics") or [])
    changed |= _reconcile_authorships(db, bill, data.get("authors") or [])
    stages_changed, current_chamber_id = _reconcile_stages(
        db, bill, data.get("stages") or []
    )
    changed |= stages_changed
    changed |= _reconcile_documents(db, bill, data.get("documents") or [])
    changed |= _reconcile_urgencies(
        db,
        bill,
        data.get("_current_urgency_type") or data.get("current_urgency_type"),
        current_chamber_id or origin_chamber.id,
        entry_date_value,
    )
    if bill.current_chamber_id != current_chamber_id:
        bill.current_chamber_id = current_chamber_id
        changed = True
    if changed:
        _touch_syncable(db, bill)

    db.flush()

    status_changed = (not is_new) and old_status != new_status

    return bill, {
        "is_new": is_new,
        "status_changed": status_changed,
        "stage_changed": stages_changed,
        "old_status": old_status,
        "new_status": new_status,
    }


def update_bill_full_text(
    db: Session, bill_id: int, full_text: str | None
) -> Bill | None:
    bill = db.execute(
        select(Bill).where(Bill.id == bill_id).with_for_update()
    ).scalar_one_or_none()
    if bill is None:
        return None

    normalized = (full_text or "").strip() or None
    if bill.full_text != normalized:
        bill.full_text = normalized
        _touch_syncable(db, bill)
        db.flush()
    return bill


def update_bill_ai_summary(
    db: Session, bill_id: int, ai_summary: str | None
) -> Bill | None:
    bill = db.execute(
        select(Bill).where(Bill.id == bill_id).with_for_update()
    ).scalar_one_or_none()
    if bill is None:
        return None

    normalized = (ai_summary or "").strip() or None
    should_update = bill.ai_summary != normalized or (
        normalized is not None and bill.ai_summary_updated_at is None
    )
    if should_update:
        bill.ai_summary = normalized
        bill.ai_summary_updated_at = datetime.now(timezone.utc) if normalized else None
        _touch_syncable(db, bill)
        db.flush()
    return bill


def upsert_legislator(db: Session, data: dict[str, Any]) -> Legislator:
    party = _get_or_create_party(db, data.get("_party_name"))
    district = None
    if data.get("_district_number"):
        district = db.execute(
            select(District).where(District.number == data["_district_number"])
        ).scalar_one_or_none()
    circumscription = _get_or_create_circumscription(
        db,
        data.get("_circumscription_number"),
        data.get("_circumscription"),
    )

    insert_stmt = pg_insert(Legislator).values(
        bcn_id=data["bcn_id"],
        first_name=(data.get("first_name") or "")[:100],
        last_name=(data.get("last_name") or "")[:100],
        full_name=(data.get("full_name") or "")[:200],
        gender=(data.get("gender") or "")[:1] or None,
        birth_date=_parse_date(data.get("birth_date")),
        email=(data.get("email") or "")[:255] or None,
        phone=(data.get("phone") or "")[:50] or None,
        chamber_type=_coerce_enum(
            ChamberType,
            data.get("chamber_type"),
            ChamberType.DEPUTIES,
        ),
        party_id=party.id if party else None,
        district_id=district.id if district else None,
        circumscription_id=circumscription.id if circumscription else None,
        is_active=bool(data.get("is_active", True)),
    )
    legislator_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["bcn_id"],
            set_={
                "first_name": insert_stmt.excluded.first_name,
                "last_name": insert_stmt.excluded.last_name,
                "full_name": insert_stmt.excluded.full_name,
                "gender": insert_stmt.excluded.gender,
                "birth_date": insert_stmt.excluded.birth_date,
                "email": insert_stmt.excluded.email,
                "phone": insert_stmt.excluded.phone,
                "chamber_type": insert_stmt.excluded.chamber_type,
                "party_id": insert_stmt.excluded.party_id,
                "district_id": insert_stmt.excluded.district_id,
                "circumscription_id": insert_stmt.excluded.circumscription_id,
                "is_active": insert_stmt.excluded.is_active,
                "updated_at": func.now(),
                "sync_version": global_sync_version_seq.next_value(),
            },
        ).returning(Legislator.id)
    ).scalar_one()

    legislator = db.execute(
        select(Legislator)
        .options(selectinload(Legislator.terms))
        .where(Legislator.id == legislator_id)
    ).scalar_one()
    if _reconcile_terms(db, legislator, data.get("_militancias") or []):
        _touch_syncable(db, legislator)
    db.flush()
    return legislator


def upsert_committee(db: Session, data: dict[str, Any]) -> Committee:
    chamber = _get_or_create_chamber(
        db, data.get("_chamber_type") or data.get("chamber_type")
    )
    name = (data.get("name") or "").strip()[:300]
    committee = db.execute(
        select(Committee)
        .options(selectinload(Committee.memberships))
        .where(Committee.name == name)
        .where(Committee.chamber_id == chamber.id)
    ).scalar_one_or_none()
    if committee is None:
        committee = Committee(
            name=name,
            chamber_id=chamber.id,
            committee_type=_coerce_enum(
                CommitteeType,
                data.get("committee_type"),
                CommitteeType.PERMANENT,
            ),
            is_active=True,
        )
        db.add(committee)
        db.flush()
        changed = True
    else:
        changed = False
        committee_type = _coerce_enum(
            CommitteeType,
            data.get("committee_type"),
            CommitteeType.PERMANENT,
        )
        if committee.committee_type != committee_type:
            committee.committee_type = committee_type
            changed = True
        if not committee.is_active:
            committee.is_active = True
            changed = True
        if changed:
            _touch_syncable(db, committee)

    memberships_changed = _reconcile_committee_memberships(
        db, committee, data.get("members") or []
    )
    if memberships_changed:
        _touch_syncable(db, committee)
    db.flush()
    return committee


def upsert_voting_session(
    db: Session, data: dict[str, Any], bill_bulletin: str | None = None
) -> VotingSession:
    chamber = _get_or_create_chamber(
        db, data.get("_chamber_type") or data.get("chamber_type")
    )
    bill_id = None
    bulletin = bill_bulletin or data.get("bill_bulletin") or data.get("_bill_bulletin")
    if bulletin:
        bill_id = db.execute(
            select(Bill.id).where(Bill.bulletin_number == bulletin)
        ).scalar_one_or_none()

    insert_stmt = pg_insert(VotingSession).values(
        bcn_id=(data.get("bcn_id") or "")[:100],
        chamber_id=chamber.id,
        bill_id=bill_id,
        voting_type=_coerce_enum(
            VotingType, data.get("voting_type"), VotingType.GENERAL
        ),
        subject=(data.get("subject") or "")[:2000],
        voting_date=_parse_datetime(data.get("voting_date")),
        result=_coerce_enum(VotingResult, data.get("result")),
        votes_for=int(data.get("votes_for", 0) or 0),
        votes_against=int(data.get("votes_against", 0) or 0),
        abstentions=int(data.get("abstentions", 0) or 0),
        absences=int(data.get("absences", 0) or 0),
        quorum_type=(data.get("quorum") or data.get("quorum_type") or "")[:100] or None,
    )
    voting_session_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["bcn_id"],
            set_={
                "chamber_id": insert_stmt.excluded.chamber_id,
                "bill_id": insert_stmt.excluded.bill_id,
                "voting_type": insert_stmt.excluded.voting_type,
                "subject": insert_stmt.excluded.subject,
                "voting_date": insert_stmt.excluded.voting_date,
                "result": insert_stmt.excluded.result,
                "votes_for": insert_stmt.excluded.votes_for,
                "votes_against": insert_stmt.excluded.votes_against,
                "abstentions": insert_stmt.excluded.abstentions,
                "absences": insert_stmt.excluded.absences,
                "quorum_type": insert_stmt.excluded.quorum_type,
                "updated_at": func.now(),
                "sync_version": global_sync_version_seq.next_value(),
            },
        ).returning(VotingSession.id)
    ).scalar_one()

    voting_session = db.execute(
        select(VotingSession)
        .options(selectinload(VotingSession.votes))
        .where(VotingSession.id == voting_session_id)
    ).scalar_one()
    if _reconcile_votes(db, voting_session, data.get("individual_votes") or []):
        _touch_syncable(db, voting_session)
    db.flush()
    return voting_session


def upsert_period(db: Session, data: dict[str, Any]) -> LegislativePeriod:
    insert_stmt = pg_insert(LegislativePeriod).values(
        number=int(data["number"]),
        start_date=_parse_date(data.get("start_date")) or date.today(),
        end_date=_parse_date(data.get("end_date"))
        or _parse_date(data.get("start_date"))
        or date.today(),
        description=(data.get("description") or "")[:200] or None,
    )
    period_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["number"],
            set_={
                "start_date": insert_stmt.excluded.start_date,
                "end_date": insert_stmt.excluded.end_date,
                "description": insert_stmt.excluded.description,
                "updated_at": func.now(),
                "sync_version": global_sync_version_seq.next_value(),
            },
        ).returning(LegislativePeriod.id)
    ).scalar_one()
    period = db.get(LegislativePeriod, period_id)
    if period is None:
        raise RuntimeError(f"Failed to load legislative period id={period_id}")
    return period


def upsert_session(db: Session, data: dict[str, Any]) -> LegislativeSession:
    chamber = _get_or_create_chamber(db, data.get("_chamber_type") or "deputies")
    start_date_value = _parse_date(data.get("start_date")) or date.today()
    period = (
        db.execute(
            select(LegislativePeriod)
            .where(LegislativePeriod.start_date <= start_date_value)
            .order_by(LegislativePeriod.start_date.desc())
        )
        .scalars()
        .first()
    )
    if period is None:
        raise ValueError(
            f"No legislative period found for session start_date={start_date_value}"
        )

    session = db.execute(
        select(LegislativeSession)
        .where(LegislativeSession.period_id == period.id)
        .where(LegislativeSession.number == int(data["number"]))
        .where(
            LegislativeSession.session_type
            == (data.get("session_type") or "ordinary")[:30]
        )
        .where(LegislativeSession.chamber_id == chamber.id)
    ).scalar_one_or_none()
    if session is None:
        session = LegislativeSession(
            number=int(data["number"]),
            session_type=(data.get("session_type") or "ordinary")[:30],
            period_id=period.id,
            chamber_id=chamber.id,
            start_date=start_date_value,
            end_date=_parse_date(data.get("end_date")),
        )
        db.add(session)
    else:
        changed = False
        if session.start_date != start_date_value:
            session.start_date = start_date_value
            changed = True
        end_date_value = _parse_date(data.get("end_date"))
        if session.end_date != end_date_value:
            session.end_date = end_date_value
            changed = True
        if changed:
            _touch_syncable(db, session)
    db.flush()
    return session


def upsert_region(db: Session, data: dict[str, Any]) -> Region:
    insert_stmt = pg_insert(Region).values(
        number=int(data["number"]),
        name=(data.get("name") or "")[:100],
        capital=(
            (data.get("provinces") or [{}])[0].get("name") or data.get("name") or ""
        )[:100],
    )
    region_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["number"],
            set_={
                "name": insert_stmt.excluded.name,
                "capital": insert_stmt.excluded.capital,
                "updated_at": func.now(),
                "sync_version": global_sync_version_seq.next_value(),
            },
        ).returning(Region.id)
    ).scalar_one()
    region = db.get(Region, region_id)
    if region is None:
        raise RuntimeError(f"Failed to load region id={region_id}")
    changed = False

    for province_payload in data.get("provinces") or []:
        province_number = province_payload.get("number")
        if not province_number:
            continue
        province_insert = pg_insert(Province).values(
            number=int(province_number),
            name=(province_payload.get("name") or "")[:200],
            region_id=region.id,
        )
        province_id = db.execute(
            province_insert.on_conflict_do_update(
                index_elements=["number"],
                set_={
                    "name": province_insert.excluded.name,
                    "region_id": province_insert.excluded.region_id,
                    "updated_at": func.now(),
                    "sync_version": global_sync_version_seq.next_value(),
                },
            ).returning(Province.id)
        ).scalar_one()
        for commune_payload in province_payload.get("communes") or []:
            commune_number = commune_payload.get("number")
            if not commune_number:
                continue
            commune_insert = pg_insert(Commune).values(
                number=int(commune_number),
                name=(commune_payload.get("name") or "")[:200],
                province_id=province_id,
                region_id=region.id,
            )
            db.execute(
                commune_insert.on_conflict_do_update(
                    index_elements=["number"],
                    set_={
                        "name": commune_insert.excluded.name,
                        "province_id": commune_insert.excluded.province_id,
                        "region_id": commune_insert.excluded.region_id,
                        "updated_at": func.now(),
                        "sync_version": global_sync_version_seq.next_value(),
                    },
                )
            )
            changed = True

    if changed:
        _touch_syncable(db, region)
    db.flush()
    return region


def upsert_district(db: Session, data: dict[str, Any]) -> District:
    number = int(data["number"])
    region_number = DISTRICT_REGION_MAP.get(number, 13)
    region = db.execute(
        select(Region).where(Region.number == region_number)
    ).scalar_one_or_none()
    if region is None:
        region = Region(
            number=region_number, name=f"Region {region_number}", capital=""
        )
        db.add(region)
        db.flush()

    commune_names = [
        commune.get("name")
        for commune in (data.get("communes") or [])
        if commune.get("name")
    ]
    district_name = (
        ", ".join(commune_names[:5]) if commune_names else f"Distrito {number}"
    )
    insert_stmt = pg_insert(District).values(
        number=number, name=district_name[:200], region_id=region.id
    )
    district_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["number"],
            set_={
                "name": insert_stmt.excluded.name,
                "region_id": insert_stmt.excluded.region_id,
                "updated_at": func.now(),
                "sync_version": global_sync_version_seq.next_value(),
            },
        ).returning(District.id)
    ).scalar_one()
    district = db.get(District, district_id)
    if district is None:
        raise RuntimeError(f"Failed to load district id={district_id}")
    linked = False
    for commune_payload in data.get("communes") or []:
        commune_number = commune_payload.get("number")
        if not commune_number:
            continue
        commune_insert = pg_insert(Commune).values(
            number=int(commune_number),
            name=(commune_payload.get("name") or "")[:200],
            region_id=region.id,
            district_id=district.id,
        )
        db.execute(
            commune_insert.on_conflict_do_update(
                index_elements=["number"],
                set_={
                    "name": commune_insert.excluded.name,
                    "region_id": commune_insert.excluded.region_id,
                    "district_id": commune_insert.excluded.district_id,
                    "updated_at": func.now(),
                    "sync_version": global_sync_version_seq.next_value(),
                },
            )
        )
        linked = True
    if linked:
        _touch_syncable(db, district)
    db.flush()
    return district

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.geography.dataset import GeographyDataset
from app.models.base import global_sync_version_seq
from app.models.core import Circumscription, Commune, District, Province, Region, Topic
from app.models.diario_oficial import OfficialGazetteNorm, Regulation, RegulationStage
from app.models.enums import (
    BillOrigin,
    BillStatus,
    BillType,
    Bloc,
    ChamberType,
    CommitteeType,
    StageType,
    UrgencyType,
    VoteChoice,
    VotingResult,
    VotingType,
)
from app.models.legislature import (
    BlocAffiliation,
    Chamber,
    Committee,
    CommitteeMembership,
    LegislativePeriod,
    LegislativeSession,
    Legislator,
    LegislatorTerm,
    ParliamentaryAppointment,
    PoliticalParty,
)
from app.models.proyecto import (
    Bill,
    BillAuthorship,
    BillDocument,
    BillEvent,
    BillSponsoringMinistry,
    BillStage,
    BillUrgency,
)
from app.models.votacion import Vote, VotingSession

logger = logging.getLogger(__name__)

UNKNOWN_START_DATE = date(1900, 1, 1)

# Obvious sentinel for ``_parse_datetime`` when an upstream value cannot be
# parsed. We can't use ``None`` because ``voting_date`` is NOT NULL on the
# voting_sessions table, and we don't want to silently stamp ``now()`` (which
# makes corrupted rows look like fresh activity). 0001-01-01 is impossible
# real data and sorts to the bottom of every chronological view.
_DATETIME_PARSE_SENTINEL = datetime(1, 1, 1)


def _next_sync_value(db: Session) -> int:
    return db.execute(select(global_sync_version_seq.next_value())).scalar_one()


def _touch_syncable(db: Session, obj: Any) -> None:
    obj.updated_at = datetime.now(timezone.utc)
    obj.sync_version = _next_sync_value(db)


def _normalize_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:100] or "sin-tema"


def _normalize_person_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_accents = "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )
    normalized = re.sub(r"[^a-z0-9\s]", " ", without_accents.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _set_syncable_attrs(db: Session, obj: Any, **attrs: Any) -> bool:
    changed = False
    for key, value in attrs.items():
        if getattr(obj, key) != value:
            setattr(obj, key, value)
            changed = True
    return changed


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
    """Coerce upstream ``Fecha`` values into a **naive Chile wall-clock**
    ``datetime``.

    Voting-session timestamps are stored on a ``TIMESTAMP WITHOUT TIME ZONE``
    column and rendered verbatim everywhere (API, admin, UI). Upstream
    sources always express vote times in Chile local time without a tz
    marker, so we never apply timezone arithmetic — anything aware that
    happens to arrive has its tz stripped, treating its wall-clock as
    Chile-local.
    """
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is not None else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                parsed = None
            if parsed is not None:
                return (
                    parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
                )
    parsed_date = _parse_date(value)
    if parsed_date is not None:
        return datetime.combine(parsed_date, datetime.min.time())
    logger.warning(
        "_parse_datetime: unparseable upstream value %r — using sentinel %s",
        value,
        _DATETIME_PARSE_SENTINEL.isoformat(),
    )
    return _DATETIME_PARSE_SENTINEL


def _parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


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


_INDEPENDENT_LABELS = {"independiente", "independientes", "ind", "independ"}
_PARTIES_COLORS = {
    "UDI": "#1d4ed8",
    "PNL": "#eab308",
    "PREP": "#0f172a",
    "PRO": "#be185d",
    "PC": "#b91c1c",
    "FA": "#ea580c",
    "PS": "#dc2626",
    "PDG": "#f97316",
    "PCS": "#e11d48",
    "DC": "#15803d",
    "RN": "#1e40af",
    "RD": "#f43f5e",
    "PPD": "#f59e0b",
    "COMUNES": "#9333ea",
    "PAH": "#d97706",
    "PH": "#fb923c",
    "FRVS": "#65a30d",
    "EVOP": "#0891b2",
    "PSC": "#2563eb",
    "PCC": "#312e81",
    "DEM": "#6366f1",
    "PL": "#db2777",
    "PR": "#991b1b",
    "PRI": "#84cc16",
}


def _upsert_party_from_opendata(
    db: Session, name: str | None, alias: str | None
) -> PoliticalParty | None:
    name = (name or "").strip()
    if not name:
        return None
    abbreviation = (alias or "").strip()[:20] or name[:20]
    if (
        abbreviation.lower() in _INDEPENDENT_LABELS
        or name.lower() in _INDEPENDENT_LABELS
    ):
        return None

    party = db.execute(
        select(PoliticalParty).where(func.lower(PoliticalParty.name) == name.lower())
    ).scalar_one_or_none()
    color = _PARTIES_COLORS.get(abbreviation, "#888888")
    if party is None:
        party = PoliticalParty(
            name=name[:200], abbreviation=abbreviation, is_active=True, color=color
        )
        db.add(party)
    else:
        party.abbreviation = abbreviation
    db.flush()
    return party


def _resolve_party_from_senado(
    db: Session, raw_abbreviation: str | None
) -> PoliticalParty | None:
    raw = (raw_abbreviation or "").strip()
    if not raw:
        return None
    normalized = raw.replace(".", "").upper()
    if normalized.lower() in _INDEPENDENT_LABELS:
        return None
    return db.execute(
        select(PoliticalParty).where(
            func.upper(PoliticalParty.abbreviation) == normalized
        )
    ).scalar_one_or_none()


def _get_or_create_circumscription(
    db: Session, number: int | None, name: str | None
) -> Circumscription | None:
    if not number:
        return None
    circumscription = db.execute(
        select(Circumscription).where(Circumscription.number == number)
    ).scalar_one_or_none()
    if circumscription is None:
        circumscription = Circumscription(
            number=number, name=(name or f"Circunscripcion {number}")[:200]
        )
        db.add(circumscription)
        db.flush()
    return circumscription


def _upsert_topic_record(db: Session, name: str) -> tuple[Topic, bool]:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Topic name is required")

    slug = _normalize_slug(normalized_name)
    topic = db.execute(select(Topic).where(Topic.slug == slug)).scalar_one_or_none()
    if topic is None:
        topic = db.execute(
            select(Topic).where(func.lower(Topic.name) == normalized_name.lower())
        ).scalar_one_or_none()

    if topic is None:
        topic = Topic(name=normalized_name[:100], slug=slug)
        db.add(topic)
        db.flush()
        return topic, True

    changed = False
    if topic.slug != slug:
        topic.slug = slug
        changed = True
    if topic.name != normalized_name[:100]:
        topic.name = normalized_name[:100]
        changed = True
    if changed:
        _touch_syncable(db, topic)
        db.flush()
    return topic, changed


def _reconcile_topics(db: Session, bill: Bill, topic_names: list[str]) -> bool:
    desired_topics: list[Topic] = []
    changed = False
    for topic_name in topic_names:
        if not topic_name or not topic_name.strip():
            continue
        topic, topic_changed = _upsert_topic_record(db, topic_name)
        changed |= topic_changed
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


def _event_key(
    event_date_value: date | None,
    title: str,
    description: str,
    chamber_id: int | None,
) -> tuple[Any, ...]:
    return (event_date_value, title.strip(), description.strip(), chamber_id)


def _document_key(
    document_type: str, title: str, document_url: str, document_date: date | None
) -> tuple[Any, ...]:
    return (document_type, title.strip(), document_url.strip(), document_date)


def _sponsoring_ministry_key(
    source_id: int | None, name: str | None
) -> tuple[Any, ...]:
    return (source_id, (name or "").strip().lower())


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


def _reconcile_events(db: Session, bill: Bill, events: list[dict[str, Any]]) -> bool:
    current_by_key = {
        _event_key(
            event.event_date,
            event.title,
            event.description or "",
            event.chamber_id,
        ): event
        for event in bill.events
    }
    desired_keys: set[tuple[Any, ...]] = set()
    changed = False

    for payload in events:
        event_date_value = _parse_date(payload.get("event_date"))
        title = (payload.get("title") or "").strip()[:500]
        description = (payload.get("description") or "").strip()
        if event_date_value is None or not title:
            continue

        chamber = None
        if payload.get("_chamber_type"):
            chamber = _get_or_create_chamber(db, payload["_chamber_type"])

        key = _event_key(
            event_date_value,
            title,
            description,
            chamber.id if chamber else None,
        )
        desired_keys.add(key)
        if key in current_by_key:
            continue

        db.add(
            BillEvent(
                bill_id=bill.id,
                chamber_id=chamber.id if chamber else None,
                event_date=event_date_value,
                title=title,
                description=description or None,
            )
        )
        changed = True

    for key, event in list(current_by_key.items()):
        if key not in desired_keys:
            db.delete(event)
            changed = True

    return changed


def _reconcile_sponsoring_ministries(
    db: Session, bill: Bill, ministries: list[dict[str, Any]]
) -> bool:
    current_by_key = {
        _sponsoring_ministry_key(ministry.source_id, ministry.name): ministry
        for ministry in bill.sponsoring_ministries
    }
    desired_keys: set[tuple[Any, ...]] = set()
    changed = False

    for payload in ministries:
        source_id = _parse_int(payload.get("source_id"))
        name = (payload.get("name") or "").strip()[:200]
        if source_id is None and not name:
            continue

        key = _sponsoring_ministry_key(source_id, name)
        desired_keys.add(key)
        ministry = current_by_key.get(key)
        if ministry is None:
            db.add(
                BillSponsoringMinistry(
                    bill_id=bill.id,
                    source_id=source_id,
                    name=name or None,
                )
            )
            changed = True
            continue

        field_changed = False
        if ministry.source_id != source_id:
            ministry.source_id = source_id
            field_changed = True
        if (ministry.name or "") != name:
            ministry.name = name or None
            field_changed = True
        if field_changed:
            _touch_syncable(db, ministry)
            changed = True

    for key, ministry in list(current_by_key.items()):
        if key not in desired_keys:
            db.delete(ministry)
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
        party = _upsert_party_from_opendata(
            db,
            militancia.get("party_name"),
            militancia.get("party_alias"),
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
    db: Session,
    voting_session: VotingSession,
    individual_votes: list[dict[str, Any]],
    chamber_type: ChamberType | None = None,
) -> bool:
    desired: dict[int, VoteChoice] = {}
    for payload in individual_votes:
        legislator_id = _resolve_vote_legislator(db, payload, chamber_type)
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


def _find_legislator_by_name(
    db: Session, full_name: str, chamber_type: ChamberType | None = None
) -> Legislator | None:
    normalized_name = full_name.strip()
    if not normalized_name:
        return None

    if chamber_type == ChamberType.SENATE:
        legislator = _find_legislator_by_senado_vote_name(db, normalized_name)
        if legislator is not None:
            return legislator

    stmt = select(Legislator).where(
        func.lower(Legislator.full_name) == normalized_name.lower()
    )
    if chamber_type is not None:
        stmt = stmt.where(Legislator.chamber_type == chamber_type)
    stmt = stmt.limit(1)
    return db.execute(stmt).scalar_one_or_none()


def _parse_senado_vote_display_name(display_name: str) -> dict[str, str] | None:
    normalized_display = (display_name or "").strip()
    if not normalized_display or "," not in normalized_display:
        return None

    surname_part, first_name_part = [
        part.strip() for part in normalized_display.split(",", maxsplit=1)
    ]
    if not surname_part or not first_name_part:
        return None

    surname_tokens = surname_part.split()
    if not surname_tokens:
        return None

    maternal_initial = ""
    paternal_tokens = surname_tokens
    trailing_token = surname_tokens[-1].rstrip(".")
    if re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", trailing_token or ""):
        maternal_initial = trailing_token.upper()
        paternal_tokens = surname_tokens[:-1]

    paternal_last_name = " ".join(paternal_tokens).strip()
    first_name = first_name_part.strip()
    if not paternal_last_name or not first_name:
        return None

    return {
        "first_name": first_name,
        "paternal_last_name": paternal_last_name,
        "maternal_initial": maternal_initial,
    }


def _senado_vote_name_matches_legislator(
    display_name: str,
    legislator: Legislator,
) -> bool:
    parsed = _parse_senado_vote_display_name(display_name)
    if parsed is None:
        return False

    stored_first_name = _normalize_person_name(legislator.first_name or "")
    parsed_first_name = _normalize_person_name(parsed["first_name"])
    if stored_first_name != parsed_first_name:
        stored_first_token = stored_first_name.split()[0] if stored_first_name else ""
        parsed_first_token = parsed_first_name.split()[0] if parsed_first_name else ""
        if not stored_first_token or stored_first_token != parsed_first_token:
            return False

    stored_last_name = _normalize_person_name(legislator.last_name or "")
    paternal_last_name = _normalize_person_name(parsed["paternal_last_name"])
    if not stored_last_name.startswith(paternal_last_name):
        return False

    maternal_initial = _normalize_person_name(parsed["maternal_initial"])
    if not maternal_initial:
        return True

    remaining_last_name = stored_last_name[len(paternal_last_name) :].strip()
    return bool(remaining_last_name) and remaining_last_name[0] == maternal_initial


def _find_legislator_by_senado_vote_name(
    db: Session, display_name: str
) -> Legislator | None:
    parsed = _parse_senado_vote_display_name(display_name)
    if parsed is None:
        return None

    candidates = (
        db.execute(
            select(Legislator)
            .where(Legislator.chamber_type == ChamberType.SENATE)
            .order_by(Legislator.is_active.desc(), Legislator.id.asc())
        )
        .scalars()
        .all()
    )
    for legislator in candidates:
        if _senado_vote_name_matches_legislator(display_name, legislator):
            return legislator
    return None


def _resolve_vote_legislator(
    db: Session, payload: dict[str, Any], chamber_type: ChamberType | None = None
) -> int | None:
    external_id = (payload.get("legislator_external_id") or "").strip() or None
    full_name = (
        payload.get("_legislator_name") or payload.get("legislator_name") or ""
    ).strip()
    first_name = (payload.get("legislator_first_name") or "").strip()
    last_name = (payload.get("legislator_last_name") or "").strip()

    if external_id:
        legislator = db.execute(
            select(Legislator).where(Legislator.bcn_id == external_id).limit(1)
        ).scalar_one_or_none()
        if legislator is not None:
            return legislator.id

        legislator = _find_legislator_by_name(db, full_name, chamber_type)
        if legislator is not None and (
            legislator.bcn_id is None or legislator.bcn_id == external_id
        ):
            if legislator.bcn_id != external_id:
                legislator.bcn_id = external_id
                _touch_syncable(db, legislator)
                db.flush()
            return legislator.id

        chamber_label = "Senador" if chamber_type == ChamberType.SENATE else "Diputado"
        external_ref = external_id.rsplit(":", 1)[-1]
        normalized_first_name = first_name or chamber_label
        normalized_last_name = last_name or external_ref or "Desconocido"
        normalized_full_name = (
            full_name
            or " ".join(
                part for part in [normalized_first_name, normalized_last_name] if part
            ).strip()
        )
        legislator = Legislator(
            bcn_id=external_id,
            first_name=normalized_first_name[:100],
            last_name=normalized_last_name[:100],
            full_name=normalized_full_name[:200],
            chamber_type=chamber_type or ChamberType.DEPUTIES,
            is_active=False,
        )
        db.add(legislator)
        db.flush()
        return legislator.id

    legislator = _find_legislator_by_name(db, full_name, chamber_type)
    return legislator.id if legislator is not None else None


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


def _reconcile_orphan_voting_sessions(db: Session, bill: Bill) -> None:
    """Link previously orphaned ``VotingSession`` rows to this bill (ADR-0010).

    The chamber-votes bulk task may save a vote before its bill has been
    ingested; in that case ``bill_id`` is null and the upstream bulletin
    is stashed in ``bill_bulletin_number``. When the bill finally arrives
    we deterministically attach those rows here.
    """
    db.execute(
        update(VotingSession)
        .where(
            VotingSession.bill_id.is_(None),
            VotingSession.bill_bulletin_number == bill.bulletin_number,
        )
        .values(bill_id=bill.id)
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
        db.expire_all()
        bill = db.execute(
            select(Bill)
            .execution_options(populate_existing=True)
            .options(selectinload(Bill.sponsoring_ministries))
            .where(Bill.id == bill_id)
        ).scalar_one()
        if _reconcile_sponsoring_ministries(
            db, bill, data.get("sponsoring_ministries") or []
        ):
            _touch_syncable(db, bill)
            db.flush()
        _reconcile_orphan_voting_sessions(db, bill)
        status_changed = old_status != new_status
        return bill, {
            "is_new": False,
            "status_changed": status_changed,
            "stage_changed": False,
            "old_status": old_status,
            "new_status": new_status,
        }

    db.expire_all()
    bill = db.execute(
        select(Bill)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Bill.topics),
            selectinload(Bill.authorships),
            selectinload(Bill.stages),
            selectinload(Bill.events),
            selectinload(Bill.documents),
            selectinload(Bill.sponsoring_ministries),
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
    changed |= _reconcile_events(db, bill, data.get("events") or [])
    changed |= _reconcile_documents(db, bill, data.get("documents") or [])
    changed |= _reconcile_sponsoring_ministries(
        db, bill, data.get("sponsoring_ministries") or []
    )
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
    _reconcile_orphan_voting_sessions(db, bill)

    status_changed = (not is_new) and old_status != new_status
    stage_changed = (not is_new) and stages_changed

    return bill, {
        "is_new": is_new,
        "status_changed": status_changed,
        "stage_changed": stage_changed,
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
    if data.get("chamber_type") == ChamberType.SENATE:
        party = _resolve_party_from_senado(db, data.get("_party_name"))
    else:
        party = _upsert_party_from_opendata(
            db, data.get("_party_name"), data.get("_party_alias")
        )
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
        photo_url=(data.get("photo_url") or "")[:500] or None,
        photo_thumbnail_url=(data.get("photo_thumbnail_url") or "")[:500] or None,
        profile_url=(data.get("profile_url") or "")[:500] or None,
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
                "photo_url": insert_stmt.excluded.photo_url,
                "photo_thumbnail_url": insert_stmt.excluded.photo_thumbnail_url,
                "profile_url": insert_stmt.excluded.profile_url,
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


def enrich_legislator_profile(
    db: Session, bcn_id: str, fields: dict[str, Any]
) -> Legislator | None:
    """Partially update an existing legislator with scraped/queried profile data.

    Matches by ``bcn_id`` and writes ONLY enrichment columns: district (via
    ``district_number``), photos, biography, profile URL, plus the BCN-sourced
    ``bcn_uri``, ``bcn_wiki_url``, ``profession``, ``twitter_handle``, and
    ``gender``. Never touches party, name, or ``is_active`` — OpenData-sourced
    identity/party data (ADR-0001) and chamber-sourced active flag stay
    authoritative. Returns ``None`` if no legislator matches (caller should log
    and skip).
    """
    legislator = db.execute(
        select(Legislator).where(Legislator.bcn_id == bcn_id)
    ).scalar_one_or_none()
    if legislator is None:
        return None

    changed = False

    district_number = fields.get("district_number")
    if district_number:
        district = db.execute(
            select(District).where(District.number == district_number)
        ).scalar_one_or_none()
        if district is not None and legislator.district_id != district.id:
            legislator.district_id = district.id
            changed = True

    for column, max_len in (
        ("photo_url", 500),
        ("photo_thumbnail_url", 500),
        ("profile_url", 500),
        ("biography", None),
        ("bcn_uri", 500),
        ("bcn_wiki_url", 500),
        ("profession", 200),
        ("twitter_handle", 50),
    ):
        value = fields.get(column)
        if not value:
            continue
        value = str(value).strip()
        if max_len is not None:
            value = value[:max_len]
        if getattr(legislator, column) != value:
            setattr(legislator, column, value)
            changed = True

    gender = fields.get("gender")
    if gender and legislator.gender != gender:
        legislator.gender = str(gender)[:1]
        changed = True

    if changed:
        _touch_syncable(db, legislator)
        db.flush()
    return legislator


def upsert_parliamentary_appointment(
    db: Session,
    *,
    legislator_id: int,
    bcn_appointment_uri: str,
    chamber_type: ChamberType,
    start_date: date,
    end_date: date,
) -> ParliamentaryAppointment:
    """Idempotently upsert a BCN parliamentary appointment.

    The BCN ``PositionPeriod`` URI (``bcn_appointment_uri``) is the natural
    upsert key — re-runs over the same appointment update the existing row
    rather than duplicating it. See ADR-0005.
    """
    chamber = _get_or_create_chamber(db, chamber_type)
    existing = db.execute(
        select(ParliamentaryAppointment).where(
            ParliamentaryAppointment.bcn_appointment_uri == bcn_appointment_uri
        )
    ).scalar_one_or_none()
    if existing is None:
        appointment = ParliamentaryAppointment(
            legislator_id=legislator_id,
            chamber_id=chamber.id,
            bcn_appointment_uri=bcn_appointment_uri,
            start_date=start_date,
            end_date=end_date,
        )
        db.add(appointment)
        db.flush()
        return appointment

    changed = False
    if existing.legislator_id != legislator_id:
        existing.legislator_id = legislator_id
        changed = True
    if existing.chamber_id != chamber.id:
        existing.chamber_id = chamber.id
        changed = True
    if existing.start_date != start_date:
        existing.start_date = start_date
        changed = True
    if existing.end_date != end_date:
        existing.end_date = end_date
        changed = True
    if changed:
        _touch_syncable(db, existing)
        db.flush()
    return existing


def upsert_bloc_affiliation(
    db: Session,
    *,
    party_id: int,
    bloc: Bloc | str,
    start_date: date,
    end_date: date | None = None,
) -> BlocAffiliation:
    """Idempotently upsert a party's bloc affiliation, keyed on (party, start).

    Editorial data with no upstream source (see ADR-0006). Re-running with the
    same ``start_date`` updates the existing row's ``bloc``/``end_date`` rather
    than duplicating it. To record a change of government, close the current row
    (set its ``end_date``) and call this again with a new ``start_date``.
    """
    normalized_bloc = _coerce_enum(Bloc, bloc)
    if normalized_bloc is None:
        raise ValueError(f"Invalid bloc value: {bloc!r}")

    existing = db.execute(
        select(BlocAffiliation)
        .where(BlocAffiliation.party_id == party_id)
        .where(BlocAffiliation.start_date == start_date)
    ).scalar_one_or_none()
    if existing is None:
        affiliation = BlocAffiliation(
            party_id=party_id,
            bloc=normalized_bloc,
            start_date=start_date,
            end_date=end_date,
        )
        db.add(affiliation)
        db.flush()
        return affiliation

    changed = False
    if existing.bloc != normalized_bloc:
        existing.bloc = normalized_bloc
        changed = True
    if existing.end_date != end_date:
        existing.end_date = end_date
        changed = True
    if changed:
        _touch_syncable(db, existing)
        db.flush()
    return existing


def update_legislator_default_bloc(
    db: Session, legislator_id: int, bloc: Bloc | str | None
) -> Legislator | None:
    """Set (or clear) a legislator's editorial ``default_bloc`` override.

    Used primarily to align independents (``party_id IS NULL``) in the majority
    simulator, where the party's bloc is unavailable. Pass ``None`` to clear,
    sending the legislator back to the "sin alinear" tray. See ADR-0006.
    """
    legislator = db.execute(
        select(Legislator).where(Legislator.id == legislator_id).with_for_update()
    ).scalar_one_or_none()
    if legislator is None:
        return None

    normalized = _coerce_enum(Bloc, bloc) if bloc is not None else None
    if legislator.default_bloc != normalized:
        legislator.default_bloc = normalized
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
        bill_bulletin_number=(bulletin or None) and bulletin[:50],
        voting_type=_coerce_enum(
            VotingType, data.get("voting_type"), VotingType.GENERAL
        ),
        subject=(data.get("subject") or "")[:2000],
        voting_date=_parse_datetime(data.get("voting_date")),
        result=_coerce_enum(VotingResult, data.get("result")),
        votes_for=int(data.get("votes_for", 0) or 0),
        votes_against=int(data.get("votes_against", 0) or 0),
        abstentions=int(data.get("abstentions", 0) or 0),
        dispensed_count=int(data.get("dispensed_count", 0) or 0),
        absences=int(data.get("absences", 0) or 0),
        paired_count=int(data.get("paired_count", data.get("paired", 0)) or 0),
        quorum_type=(data.get("quorum") or data.get("quorum_type") or "")[:100] or None,
        session_ref=(data.get("session_ref") or "")[:100] or None,
        stage_label=(data.get("stage_label") or data.get("stage") or "")[:200] or None,
        article_text=(data.get("article_text") or "")[:5000] or None,
        constitutional_procedure_id=_parse_int(data.get("constitutional_procedure_id")),
        constitutional_procedure_label=(
            data.get("constitutional_procedure_label") or ""
        )[:100]
        or None,
        regulatory_procedure_id=_parse_int(data.get("regulatory_procedure_id")),
        regulatory_procedure_label=(data.get("regulatory_procedure_label") or "")[:100]
        or None,
    )
    voting_session_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["bcn_id"],
            set_={
                "chamber_id": insert_stmt.excluded.chamber_id,
                "bill_id": insert_stmt.excluded.bill_id,
                "bill_bulletin_number": insert_stmt.excluded.bill_bulletin_number,
                "voting_type": insert_stmt.excluded.voting_type,
                "subject": insert_stmt.excluded.subject,
                "voting_date": insert_stmt.excluded.voting_date,
                "result": insert_stmt.excluded.result,
                "votes_for": insert_stmt.excluded.votes_for,
                "votes_against": insert_stmt.excluded.votes_against,
                "abstentions": insert_stmt.excluded.abstentions,
                "dispensed_count": insert_stmt.excluded.dispensed_count,
                "absences": insert_stmt.excluded.absences,
                "paired_count": insert_stmt.excluded.paired_count,
                "quorum_type": insert_stmt.excluded.quorum_type,
                "session_ref": insert_stmt.excluded.session_ref,
                "stage_label": insert_stmt.excluded.stage_label,
                "article_text": insert_stmt.excluded.article_text,
                "constitutional_procedure_id": insert_stmt.excluded.constitutional_procedure_id,
                "constitutional_procedure_label": insert_stmt.excluded.constitutional_procedure_label,
                "regulatory_procedure_id": insert_stmt.excluded.regulatory_procedure_id,
                "regulatory_procedure_label": insert_stmt.excluded.regulatory_procedure_label,
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
    if _reconcile_votes(
        db,
        voting_session,
        data.get("individual_votes") or [],
        chamber.chamber_type,
    ):
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


def apply_geography_dataset(db: Session, dataset: GeographyDataset) -> dict[str, int]:
    existing_regions = {
        region.number: region for region in db.execute(select(Region)).scalars().all()
    }
    existing_provinces = {
        province.number: province
        for province in db.execute(select(Province)).scalars().all()
    }
    existing_districts = {
        district.number: district
        for district in db.execute(select(District)).scalars().all()
    }
    existing_circumscriptions = {
        circ.number: circ
        for circ in db.execute(
            select(Circumscription).options(selectinload(Circumscription.regions))
        )
        .scalars()
        .all()
    }
    existing_communes = {
        commune.number: commune
        for commune in db.execute(select(Commune)).scalars().all()
    }

    region_by_number: dict[int, Region] = {}
    province_by_number: dict[int, Province] = {}
    district_by_number: dict[int, District] = {}
    circumscription_by_number: dict[int, Circumscription] = {}

    commune_to_district = {
        commune_number: district.number
        for district in dataset.districts
        for commune_number in district.commune_numbers
    }
    commune_to_circumscription = {
        commune_number: circumscription.number
        for circumscription in dataset.circumscriptions
        for commune_number in circumscription.commune_numbers
    }

    for region_data in dataset.regions:
        region = existing_regions.get(region_data.number)
        if region is None:
            region = Region(
                number=region_data.number,
                name=region_data.name,
                capital=region_data.capital,
            )
            db.add(region)
            existing_regions[region.number] = region
        else:
            changed = _set_syncable_attrs(
                db,
                region,
                name=region_data.name,
                capital=region_data.capital,
            )
            if changed:
                _touch_syncable(db, region)
        region_by_number[region_data.number] = region

    for region_data in dataset.regions:
        region = region_by_number[region_data.number]
        for province_data in region_data.provinces:
            province = existing_provinces.get(province_data.number)
            if province is None:
                province = Province(
                    number=province_data.number,
                    name=province_data.name,
                    region=region,
                )
                db.add(province)
                existing_provinces[province.number] = province
            else:
                changed = _set_syncable_attrs(db, province, name=province_data.name)
                if province.region != region:
                    province.region = region
                    changed = True
                if changed:
                    _touch_syncable(db, province)
            province_by_number[province_data.number] = province

    for district_data in dataset.districts:
        region = region_by_number[district_data.region_number]
        district = existing_districts.get(district_data.number)
        if district is None:
            district = District(
                number=district_data.number,
                name=district_data.name,
                region=region,
            )
            db.add(district)
            existing_districts[district.number] = district
        else:
            changed = _set_syncable_attrs(db, district, name=district_data.name)
            if district.region != region:
                district.region = region
                changed = True
            if changed:
                _touch_syncable(db, district)
        district_by_number[district_data.number] = district

    for circumscription_data in dataset.circumscriptions:
        circumscription = existing_circumscriptions.get(circumscription_data.number)
        if circumscription is None:
            circumscription = Circumscription(
                number=circumscription_data.number,
                name=circumscription_data.name,
            )
            db.add(circumscription)
            existing_circumscriptions[circumscription.number] = circumscription
        else:
            _set_syncable_attrs(db, circumscription, name=circumscription_data.name)
        circumscription_by_number[circumscription_data.number] = circumscription

    for region_data in dataset.regions:
        region = region_by_number[region_data.number]
        for province_data in region_data.provinces:
            province = province_by_number[province_data.number]
            for commune_data in province_data.communes:
                district = district_by_number[commune_to_district[commune_data.number]]
                circumscription = circumscription_by_number[
                    commune_to_circumscription[commune_data.number]
                ]
                commune = existing_communes.get(commune_data.number)
                if commune is None:
                    commune = Commune(
                        number=commune_data.number,
                        name=commune_data.name,
                        province=province,
                        region=region,
                        district=district,
                        circumscription=circumscription,
                    )
                    db.add(commune)
                    existing_communes[commune.number] = commune
                else:
                    changed = _set_syncable_attrs(db, commune, name=commune_data.name)
                    if commune.province != province:
                        commune.province = province
                        changed = True
                    if commune.region != region:
                        commune.region = region
                        changed = True
                    if commune.district != district:
                        commune.district = district
                        changed = True
                    if commune.circumscription != circumscription:
                        commune.circumscription = circumscription
                        changed = True
                    if changed:
                        _touch_syncable(db, commune)

    for circumscription_data in dataset.circumscriptions:
        circumscription = circumscription_by_number[circumscription_data.number]
        changed = False
        for region_number in circumscription_data.region_numbers:
            region = region_by_number[region_number]
            if region not in circumscription.regions:
                circumscription.regions.append(region)
                changed = True
        if changed:
            _touch_syncable(db, circumscription)

    db.flush()
    return {
        "regions": len(dataset.regions),
        "provinces": sum(len(region.provinces) for region in dataset.regions),
        "communes": sum(
            len(province.communes)
            for region in dataset.regions
            for province in region.provinces
        ),
        "districts": len(dataset.districts),
        "circumscriptions": len(dataset.circumscriptions),
    }


def upsert_topic(db: Session, data: dict[str, Any]) -> Topic:
    topic, _ = _upsert_topic_record(db, data.get("name") or "")
    return topic

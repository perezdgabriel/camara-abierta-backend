from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.geography.dataset import GeographyDataset
from app.models.base import global_sync_version_seq
from app.models.core import Circumscription, Commune, District, Province, Region, Topic
from app.models.diario_oficial import OfficialGazetteNorm, Regulation, RegulationStage
from app.models.enums import (
    BillOrigin,
    BillStatus,
    BillSummaryKind,
    BillSummaryStatus,
    BillType,
    Bloc,
    CalendarEventKind,
    CalendarEventSource,
    ChamberType,
    CommitteeType,
    StageType,
    UrgencyType,
    VoteChoice,
    VotingResult,
    VotingType,
)
from app.models.enums import LegislatureKind, SessionKind
from app.models.legislature import (
    BlocAffiliation,
    CalendarEvent,
    Chamber,
    Committee,
    CommitteeMembership,
    LegislativePeriod,
    LegislativeSession,
    Legislator,
    LegislatorMergeCandidate,
    LegislatorTerm,
    Legislature,
    PoliticalParty,
)
from app.models.proyecto import (
    Bill,
    BillAuthorship,
    BillDocument,
    BillEvent,
    BillSponsoringMinistry,
    BillStage,
    BillSummary,
    BillUrgency,
)
from app.models.votacion import Vote, VotingSession

logger = logging.getLogger(__name__)

UNKNOWN_START_DATE = date(1900, 1, 1)
_MIN_PLAUSIBLE_YEAR = 1800
_MAX_PLAUSIBLE_FUTURE_YEARS = 25

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


def _is_plausible_year(year: int) -> bool:
    return (
        _MIN_PLAUSIBLE_YEAR <= year <= date.today().year + _MAX_PLAUSIBLE_FUTURE_YEARS
    )


def _repair_common_year_typo(parsed_value: date) -> date | None:
    if not 2600 <= parsed_value.year <= 2699:
        return None
    corrected_year = parsed_value.year - 600
    if not _is_plausible_year(corrected_year):
        return None
    try:
        return parsed_value.replace(year=corrected_year)
    except ValueError:
        return None


def _normalize_parsed_date(
    parsed_value: date, *, raw_value: Any, parser_name: str
) -> date | None:
    if _is_plausible_year(parsed_value.year):
        return parsed_value

    repaired = _repair_common_year_typo(parsed_value)
    if repaired is not None:
        logger.warning(
            "%s: repaired implausible upstream year in %r from %s to %s",
            parser_name,
            raw_value,
            parsed_value.isoformat(),
            repaired.isoformat(),
        )
        return repaired

    logger.warning(
        "%s: rejecting implausible upstream year in %r (%s)",
        parser_name,
        raw_value,
        parsed_value.isoformat(),
    )
    return None


def _normalize_parsed_datetime(
    parsed_value: datetime, *, raw_value: Any, parser_name: str
) -> datetime | None:
    normalized_date = _normalize_parsed_date(
        parsed_value.date(), raw_value=raw_value, parser_name=parser_name
    )
    if normalized_date is None:
        return None
    if normalized_date == parsed_value.date():
        return parsed_value
    return parsed_value.replace(
        year=normalized_date.year,
        month=normalized_date.month,
        day=normalized_date.day,
    )


def _parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        parsed_value = value if not isinstance(value, datetime) else value.date()
        return _normalize_parsed_date(
            parsed_value, raw_value=value, parser_name="_parse_date"
        )
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed_value = date.fromisoformat(text)
    except ValueError:
        match = re.match(r"(\d{2})/(\d{2})/(\d{4})", text)
        if match:
            try:
                parsed_value = date(
                    int(match.group(3)),
                    int(match.group(2)),
                    int(match.group(1)),
                )
            except ValueError:
                return None
        else:
            return None
    return _normalize_parsed_date(
        parsed_value, raw_value=value, parser_name="_parse_date"
    )


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
        parsed_value = value.replace(tzinfo=None) if value.tzinfo is not None else value
        normalized = _normalize_parsed_datetime(
            parsed_value, raw_value=value, parser_name="_parse_datetime"
        )
        return normalized or _DATETIME_PARSE_SENTINEL
    if isinstance(value, date):
        normalized = _normalize_parsed_datetime(
            datetime.combine(value, datetime.min.time()),
            raw_value=value,
            parser_name="_parse_datetime",
        )
        return normalized or _DATETIME_PARSE_SENTINEL
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                parsed = None
            if parsed is not None:
                parsed_value = (
                    parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
                )
                normalized = _normalize_parsed_datetime(
                    parsed_value, raw_value=value, parser_name="_parse_datetime"
                )
                return normalized or _DATETIME_PARSE_SENTINEL
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


_AUTHORSHIP_NON_ALPHA_RE = re.compile(r"[^a-z0-9\s]")
_AUTHORSHIP_WHITESPACE_RE = re.compile(r"\s+")


def _canonicalize_legislator_name(name: str) -> str:
    """Normalize a legislator name for authorship matching.

    Folds the upstream ``Apellido_paterno Apellido_materno, Nombres`` form
    into the DB ``Nombres Apellido_paterno Apellido_materno`` form, then
    strips accents, lowercases, and collapses whitespace and punctuation
    so both sides land on the same key.
    """
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first.strip()} {last.strip()}"
    name = name.lower()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = _AUTHORSHIP_NON_ALPHA_RE.sub(" ", name)
    return _AUTHORSHIP_WHITESPACE_RE.sub(" ", name).strip()


def _build_legislator_lookup(db: Session) -> dict[str, int]:
    """Map ``canonicalize(Legislator.full_name) -> Legislator.id``.

    Collisions (two legislators normalizing to the same key) are logged at
    ERROR and dropped from the lookup — matching them later would be
    ambiguous, so we prefer a logged miss over a silent wrong-match.
    """
    lookup: dict[str, int] = {}
    seen_full_names: dict[str, str] = {}
    collided: set[str] = set()
    for legislator_id, full_name in db.execute(
        select(Legislator.id, Legislator.full_name)
    ):
        key = _canonicalize_legislator_name(full_name or "")
        if not key:
            continue
        if key in collided:
            continue
        prior = seen_full_names.get(key)
        if prior is not None:
            logger.error(
                "Legislator canonical-key collision on %r: %r and %r both "
                "normalize to the same key; both skipped from authorship matching",
                key,
                prior,
                full_name,
            )
            collided.add(key)
            lookup.pop(key, None)
            continue
        seen_full_names[key] = full_name
        lookup[key] = legislator_id
    return lookup


def _reconcile_authorships(
    db: Session, bill: Bill, authors: list[dict[str, Any]]
) -> bool:
    # Executive bills are authored by ministries, not legislators; authorship
    # rows don't apply and ministry names will never match the legislator table.
    if bill.origin == BillOrigin.EXECUTIVE:
        return False

    lookup = _build_legislator_lookup(db)

    desired_legislator_ids: set[int] = set()
    for author in authors:
        name = (author.get("name") or "").strip()
        if not name:
            continue
        key = _canonicalize_legislator_name(name)
        legislator_id = lookup.get(key)
        if legislator_id is None:
            logger.warning(
                "Unmatched authorship name on bill %s: %r",
                bill.bulletin_number,
                name,
            )
            continue
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
) -> tuple[bool, bool]:
    """Returns ``(changed, new_comparado_added)``.

    ``new_comparado_added`` flags an inserted row with ``document_type == "comparison"`` —
    used by ``upsert_bill`` to signal the amendments-layer summary that
    fresh comparado content is available (ADR-0019).
    """
    current_by_key = {
        _document_key(
            doc.document_type, doc.title, doc.document_url or "", doc.document_date
        ): doc
        for doc in bill.documents
    }
    desired_keys: set[tuple[Any, ...]] = set()
    changed = False
    new_comparado_added = False

    for payload in documents:
        document_type = (payload.get("document_type") or "other")[:50]
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
            if document_type == "comparison":
                new_comparado_added = True

    for key, document in list(current_by_key.items()):
        if key not in desired_keys:
            db.delete(document)
            changed = True
    return changed, new_comparado_added


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


def _resolve_term_period(
    db: Session, start_date_value: date
) -> LegislativePeriod | None:
    return (
        db.execute(
            select(LegislativePeriod)
            .where(LegislativePeriod.start_date <= start_date_value)
            .order_by(LegislativePeriod.start_date.desc())
        )
        .scalars()
        .first()
    )


def _resolve_term_party(
    db: Session, term_payload: dict[str, Any]
) -> PoliticalParty | None:
    source = term_payload.get("party_source")
    if source == "opendata":
        return _upsert_party_from_opendata(
            db,
            term_payload.get("party_name"),
            term_payload.get("party_alias"),
        )
    if source == "senado_abbreviation":
        return _resolve_party_from_senado(db, term_payload.get("party_name"))
    return None


def _resolve_term_district(db: Session, district_number: int | None) -> District | None:
    if not district_number:
        return None
    return db.execute(
        select(District).where(District.number == district_number)
    ).scalar_one_or_none()


def _reconcile_terms(
    db: Session, legislator: Legislator, term_payloads: list[dict[str, Any]]
) -> bool:
    """Reconcile a legislator's per-stint terms against a normalized payload list.

    Each entry in ``term_payloads`` describes one chamber stint (chamber,
    bridge ID, dates, party, district/circumscription) — the unified shape
    emitted by parsers in :mod:`app.ingestors.parsers.legislators`. Existing
    terms keyed by ``(chamber_id, start_date)`` are updated in place; new
    payloads create rows; orphaned terms are not deleted (multiple ingest
    sources contribute to the same person, so deletion would race). See
    ADR-0015.
    """
    changed = False
    current_by_key: dict[tuple[int, date], LegislatorTerm] = {
        (term.chamber_id, term.start_date): term for term in legislator.terms
    }

    for payload in term_payloads:
        start_date_value = _parse_date(payload.get("start_date"))
        if start_date_value is None:
            continue
        end_date_value = _parse_date(payload.get("end_date"))
        chamber = _get_or_create_chamber(db, payload.get("chamber_type"))
        period = _resolve_term_period(db, start_date_value)
        if period is None:
            continue

        party = _resolve_term_party(db, payload)
        district = _resolve_term_district(db, payload.get("district_number"))
        circumscription = _get_or_create_circumscription(
            db,
            payload.get("circumscription_number"),
            payload.get("_region_name"),
        )
        chamber_external_id = payload.get("chamber_external_id") or None

        key = (chamber.id, start_date_value)
        term = current_by_key.get(key)
        if term is None:
            term = LegislatorTerm(
                legislator_id=legislator.id,
                period_id=period.id,
                chamber_id=chamber.id,
                party_id=party.id if party else None,
                district_id=district.id if district else None,
                circumscription_id=circumscription.id if circumscription else None,
                chamber_external_id=chamber_external_id,
                start_date=start_date_value,
                end_date=end_date_value,
            )
            db.add(term)
            db.flush()
            current_by_key[key] = term
            _reconcile_orphan_votes(db, term)
            changed = True
            continue

        field_changed = False
        if term.period_id != period.id:
            term.period_id = period.id
            field_changed = True
        # ``party_id``: only overwrite when the new payload actually supplies
        # one. senado.cl periodos don't carry party for historical stints, so
        # we'd otherwise wipe a party we already learned from OpenData.
        if party is not None and term.party_id != party.id:
            term.party_id = party.id
            field_changed = True
        if district is not None and term.district_id != district.id:
            term.district_id = district.id
            field_changed = True
        if (
            circumscription is not None
            and term.circumscription_id != circumscription.id
        ):
            term.circumscription_id = circumscription.id
            field_changed = True
        if chamber_external_id and term.chamber_external_id != chamber_external_id:
            term.chamber_external_id = chamber_external_id
            field_changed = True
        if end_date_value is not None and term.end_date != end_date_value:
            term.end_date = end_date_value
            field_changed = True
        if field_changed:
            _touch_syncable(db, term)
            db.flush()
            _reconcile_orphan_votes(db, term)
            changed = True
    return changed


def count_orphan_votes_older_than(db: Session, sla_days: int) -> int:
    """Count votes still unresolved (``legislator_id IS NULL``) past the SLA.

    A vote is created orphan whenever its ``legislator_external_id`` does not
    match any :class:`LegislatorTerm` covering the vote date. The reconciler
    fills them in as terms arrive; rows older than ``sla_days`` indicate a
    bridge ID that never resolved and needs operator attention. See
    ADR-0015.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=sla_days)
    return int(
        db.execute(
            select(func.count(Vote.id))
            .where(Vote.legislator_id.is_(None))
            .where(Vote.created_at < cutoff)
        ).scalar_one()
        or 0
    )


def _reconcile_orphan_votes(db: Session, term: LegislatorTerm) -> int:
    """Link any orphan votes whose bridge + date matches the just-upserted term.

    Called after every ``LegislatorTerm`` write. Orphan votes (those saved
    with ``legislator_id IS NULL`` because the resolver had no matching
    term) are claimed by joining on ``legislator_external_id`` and the
    voting-session ``voting_date`` against the term's date window. See
    ADR-0015.
    """
    if not term.chamber_external_id:
        return 0
    end_date = term.end_date or date(9999, 12, 31)
    rows = db.execute(
        select(Vote.id, Vote.voting_session_id)
        .join(VotingSession, VotingSession.id == Vote.voting_session_id)
        .where(
            Vote.legislator_id.is_(None),
            Vote.legislator_external_id == term.chamber_external_id,
            func.date(VotingSession.voting_date) >= term.start_date,
            func.date(VotingSession.voting_date) <= end_date,
        )
    ).all()
    if not rows:
        return 0

    # Drop any already-resolved Vote for the same (session, legislator) — would
    # collide with the partial unique index. Last-writer-wins by orphan id.
    existing_resolved = {
        sid: vid
        for vid, sid in db.execute(
            select(Vote.id, Vote.voting_session_id).where(
                Vote.legislator_id == term.legislator_id,
                Vote.voting_session_id.in_([sid for _, sid in rows]),
            )
        ).all()
    }
    for orphan_id, session_id in rows:
        existing_id = existing_resolved.get(session_id)
        if existing_id is not None and existing_id != orphan_id:
            db.execute(delete(Vote).where(Vote.id == orphan_id))
            continue
        db.execute(
            update(Vote)
            .where(Vote.id == orphan_id)
            .values(
                legislator_id=term.legislator_id,
                sync_version=global_sync_version_seq.next_value(),
                updated_at=func.now(),
            )
        )
    return len(rows)


def _reconcile_committee_memberships(
    db: Session, committee: Committee, members: list[dict[str, Any]]
) -> bool:
    desired: dict[tuple[Any, ...], dict[str, Any]] = {}
    for member in members:
        bcn_id = member.get("bcn_id")
        if not bcn_id:
            continue
        # Committee membership uses the chamber bridge ID like vote rows do.
        # Resolve via any LegislatorTerm carrying the bridge — the membership
        # window can be open-ended, so we don't enforce a date join here.
        term = db.execute(
            select(LegislatorTerm)
            .where(LegislatorTerm.chamber_external_id == bcn_id)
            .order_by(LegislatorTerm.start_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if term is None:
            continue
        legislator_id = term.legislator_id
        start_date_value = _parse_date(member.get("start_date")) or UNKNOWN_START_DATE
        key = (legislator_id, member.get("role") or "member", start_date_value)
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
    """Reconcile a voting session's individual votes against the upstream list.

    Each upstream payload must carry ``legislator_external_id`` (the chamber
    bridge — ``camara:{Id}`` or ``senado:{PARLID}``). The resolver maps the
    bridge to a canonical :class:`Legislator` via :class:`LegislatorTerm`
    when a term covers the session's date; otherwise the row is saved
    orphaned (``legislator_id IS NULL``) and waits for term ingest to claim
    it. See ADR-0015.

    For Senate sessions the upstream restsil feed only emits rows for
    senators in SI/NO/ABSTENCION/PAREO buckets — anyone who did not vote
    leaves no row at all. To keep ``record_rate`` honest and feed the
    ``BAJO_REGISTRO`` signal, this function synthesises a
    :class:`VoteChoice.NO_VOTE` row for every senator whose
    :class:`LegislatorTerm` covers ``voting_date`` and who is absent from
    the upstream list. Senators without an ingested term are skipped
    (orphan-safe — the next refresh after term ingestion claims them).
    """
    voting_date = voting_session.voting_date.date()
    desired: dict[str, dict[str, Any]] = {}
    for payload in individual_votes:
        external_id = (payload.get("legislator_external_id") or "").strip()
        if not external_id:
            continue
        legislator_id = _resolve_vote_legislator(db, external_id, voting_date)
        desired[external_id] = {
            "legislator_id": legislator_id,
            "vote": _coerce_enum(VoteChoice, payload.get("vote"), VoteChoice.NO_VOTE),
        }

    if chamber_type == ChamberType.SENATE:
        roster = db.execute(
            select(LegislatorTerm.legislator_id, LegislatorTerm.chamber_external_id)
            .join(Chamber, Chamber.id == LegislatorTerm.chamber_id)
            .where(
                Chamber.chamber_type == ChamberType.SENATE,
                LegislatorTerm.chamber_external_id.is_not(None),
                LegislatorTerm.start_date <= voting_date,
                or_(
                    LegislatorTerm.end_date.is_(None),
                    LegislatorTerm.end_date >= voting_date,
                ),
            )
        ).all()
        for legislator_id, external_id in roster:
            if external_id in desired:
                continue
            desired[external_id] = {
                "legislator_id": legislator_id,
                "vote": VoteChoice.NO_VOTE,
            }

    current_by_external: dict[str, Vote] = {
        vote.legislator_external_id: vote
        for vote in voting_session.votes
        if vote.legislator_external_id
    }
    changed = False

    new_no_votes = sum(1 for p in desired.values() if p["vote"] == VoteChoice.NO_VOTE)
    if voting_session.no_votes != new_no_votes:
        voting_session.no_votes = new_no_votes
        changed = True

    for external_id, existing_vote in list(current_by_external.items()):
        if external_id not in desired:
            db.delete(existing_vote)
            changed = True

    for external_id, payload in desired.items():
        vote = current_by_external.get(external_id)
        if vote is None:
            db.add(
                Vote(
                    voting_session_id=voting_session.id,
                    legislator_id=payload["legislator_id"],
                    legislator_external_id=external_id,
                    vote=payload["vote"],
                )
            )
            changed = True
            continue
        field_changed = False
        if vote.vote != payload["vote"]:
            vote.vote = payload["vote"]
            field_changed = True
        if (
            vote.legislator_id is None
            and payload["legislator_id"] is not None
            and vote.legislator_id != payload["legislator_id"]
        ):
            vote.legislator_id = payload["legislator_id"]
            field_changed = True
        if field_changed:
            _touch_syncable(db, vote)
            changed = True
    return changed


def _resolve_vote_legislator(
    db: Session, external_id: str, voting_date: date
) -> int | None:
    """Resolve an upstream chamber bridge to a canonical ``Legislator.id``.

    The resolver joins ``LegislatorTerm.chamber_external_id`` with a date
    window covering ``voting_date`` (per ADR-0015). Returns ``None`` when no
    term matches — callers save the vote orphaned and rely on
    :func:`_reconcile_orphan_votes` to claim it after the term arrives.
    """
    term = db.execute(
        select(LegislatorTerm)
        .where(
            LegislatorTerm.chamber_external_id == external_id,
            LegislatorTerm.start_date <= voting_date,
            or_(
                LegislatorTerm.end_date.is_(None),
                LegislatorTerm.end_date >= voting_date,
            ),
        )
        .order_by(LegislatorTerm.start_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return term.legislator_id if term is not None else None


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
    """Link previously orphaned ``VotingSession`` rows to this bill (ADR-0013).

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
        select(Bill.id, Bill.status, Bill.full_text_url).where(
            Bill.bulletin_number == data["bulletin_number"]
        )
    ).first()
    is_new = existing is None
    old_status = existing.status if existing is not None else None
    old_full_text_url = existing.full_text_url if existing is not None else None
    new_status = _coerce_enum(BillStatus, data.get("status"), BillStatus.PENDING)
    already_terminal = existing is not None and existing.status in TERMINAL_STATUSES
    new_full_text_url = (data.get("message_url") or data.get("full_text_url") or "")[
        :500
    ] or None

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
        bill_type=bill_type,
        origin=origin,
        status=new_status,
        entry_date=entry_date_value,
        publication_date=_parse_date(data.get("publication_date")),
        law_number=(data.get("law_number") or "")[:50] or None,
        full_text_url=new_full_text_url,
        origin_chamber_id=origin_chamber.id,
    )
    bill_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["bulletin_number"],
            set_={
                "title": insert_stmt.excluded.title,
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
        full_text_url_changed = old_full_text_url != new_full_text_url
        return bill, {
            "is_new": False,
            "status_changed": status_changed,
            "stage_changed": False,
            "full_text_url_changed": full_text_url_changed,
            "new_comparado_added": False,
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
    documents_changed, new_comparado_added = _reconcile_documents(
        db, bill, data.get("documents") or []
    )
    changed |= documents_changed
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
    full_text_url_changed = old_full_text_url != new_full_text_url

    return bill, {
        "is_new": is_new,
        "status_changed": status_changed,
        "stage_changed": stage_changed,
        "full_text_url_changed": full_text_url_changed,
        "new_comparado_added": new_comparado_added,
        "old_status": old_status,
        "new_status": new_status,
    }


def upsert_bill_summary(
    db: Session,
    *,
    bill_id: int,
    kind: BillSummaryKind,
    status: BillSummaryStatus,
    content: dict[str, Any] | None,
    prompt_version: str | None,
    model_name: str | None,
    source_url: str | None,
    source_url_hash: str | None,
    error_reason: str | None,
    truncated: bool = False,
) -> BillSummary | None:
    """Idempotent upsert of one BillSummary row per (bill_id, kind).

    Bumps the bill's ``sync_version`` (so mobile clients re-fetch the bill
    detail and pick up the new structured summary) only when the summary
    row actually changes. See ADR-0019.
    """
    bill = db.execute(
        select(Bill).where(Bill.id == bill_id).with_for_update()
    ).scalar_one_or_none()
    if bill is None:
        return None

    summary = db.execute(
        select(BillSummary).where(
            BillSummary.bill_id == bill_id, BillSummary.kind == kind
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)
    fields = {
        "status": status,
        "content": content,
        "prompt_version": prompt_version,
        "model_name": model_name,
        "source_url": source_url,
        "source_url_hash": source_url_hash,
        "error_reason": error_reason,
        "truncated": truncated,
        "generated_at": now,
    }
    if summary is None:
        summary = BillSummary(bill_id=bill_id, kind=kind, **fields)
        db.add(summary)
        _touch_syncable(db, bill)
    else:
        changed = any(
            getattr(summary, key) != value
            for key, value in fields.items()
            if key != "generated_at"
        )
        for key, value in fields.items():
            setattr(summary, key, value)
        if changed:
            _touch_syncable(db, summary)
            _touch_syncable(db, bill)
    db.flush()
    return summary


def get_bill_summary(
    db: Session, *, bill_id: int, kind: BillSummaryKind
) -> BillSummary | None:
    return db.execute(
        select(BillSummary).where(
            BillSummary.bill_id == bill_id, BillSummary.kind == kind
        )
    ).scalar_one_or_none()


def _normalize_match_name(value: str) -> str:
    """Lowercase + strip-accents key used for cross-chamber person matching.

    Same shape used at vote-level resolution (``_normalize_person_name``) — kept
    on a dedicated helper to make the name-match intent explicit. See ADR-0015.
    """
    return _normalize_person_name(value or "")


def _find_legislator_candidates(
    db: Session, paternal: str, maternal: str, first: str
) -> list[Legislator]:
    """Return existing ``Legislator`` rows whose normalized name matches.

    Match key is ``(paternal, maternal, first)`` lowercased + accents stripped.
    The senator catalog and OpenData deputies expose the parts separately, so
    we can be exact rather than substring-matching ``full_name``. See ADR-0015.
    """
    paternal_key = _normalize_match_name(paternal)
    maternal_key = _normalize_match_name(maternal)
    first_key = _normalize_match_name(first)
    if not paternal_key or not first_key:
        return []
    candidates = db.execute(select(Legislator)).scalars().all()
    matches: list[Legislator] = []
    for candidate in candidates:
        c_first = _normalize_match_name(candidate.first_name or "")
        c_last = _normalize_match_name(candidate.last_name or "")
        if not c_first.startswith(first_key) and not first_key.startswith(c_first):
            continue
        last_combined = f"{paternal_key} {maternal_key}".strip()
        if c_last == last_combined:
            matches.append(candidate)
            continue
        if maternal_key and c_last == paternal_key:
            matches.append(candidate)
    return matches


def _terms_overlap(
    a_start: date, a_end: date | None, b_start: date, b_end: date | None
) -> bool:
    a_end = a_end or date(9999, 12, 31)
    b_end = b_end or date(9999, 12, 31)
    return a_start <= b_end and b_start <= a_end


def _disambiguate_by_term_overlap(
    candidates: list[Legislator], seed_terms: list[dict[str, Any]]
) -> Legislator | None:
    """Pick the candidate whose existing terms overlap the seed's term windows.

    Both sides describe the same person if at least one seed term aligns with
    a candidate's existing term (same chamber, overlapping dates). With
    Chilean two-apellido naming this resolves nearly every same-name
    collision. Returns ``None`` if zero or several candidates overlap (the
    caller writes a merge-review row).
    """
    if len(candidates) <= 1:
        return candidates[0] if candidates else None

    seed_windows: list[tuple[ChamberType, date, date | None]] = []
    for term in seed_terms:
        start = _parse_date(term.get("start_date"))
        if start is None:
            continue
        chamber = _coerce_enum(ChamberType, term.get("chamber_type"))
        if chamber is None:
            continue
        seed_windows.append((chamber, start, _parse_date(term.get("end_date"))))

    if not seed_windows:
        return None

    overlapping: list[Legislator] = []
    for candidate in candidates:
        for existing in candidate.terms:
            if existing.chamber is None:
                continue
            for chamber_type, start, end in seed_windows:
                if existing.chamber.chamber_type != chamber_type:
                    continue
                if _terms_overlap(existing.start_date, existing.end_date, start, end):
                    overlapping.append(candidate)
                    break
            if candidate in overlapping:
                break

    if len(overlapping) == 1:
        return overlapping[0]
    return None


def _write_merge_candidate(
    db: Session, seed: dict[str, Any], candidate_ids: list[int]
) -> None:
    """Defer ambiguous cross-chamber matches to manual admin review."""
    existing = db.execute(
        select(LegislatorMergeCandidate)
        .where(LegislatorMergeCandidate.source == seed.get("source"))
        .where(
            LegislatorMergeCandidate.source_external_id
            == str(seed.get("source_external_id") or "")
        )
        .where(LegislatorMergeCandidate.resolved_legislator_id.is_(None))
    ).scalar_one_or_none()
    if existing is not None:
        if existing.candidate_legislator_ids != candidate_ids:
            existing.candidate_legislator_ids = candidate_ids
            _touch_syncable(db, existing)
        return
    db.add(
        LegislatorMergeCandidate(
            source=(seed.get("source") or "")[:50],
            source_external_id=str(seed.get("source_external_id") or "")[:100],
            first_name=(seed.get("first_name") or "")[:100],
            last_name=(seed.get("last_name") or "")[:200],
            full_name=(seed.get("full_name") or "")[:200],
            candidate_legislator_ids=candidate_ids,
            payload={"terms": seed.get("terms") or []},
        )
    )
    db.flush()


def _merge_legislator_seed(db: Session, seed: dict[str, Any]) -> Legislator | None:
    """Resolve a normalized seed to an existing ``Legislator`` (if any).

    Priority: (1) ``bcn_uri`` exact match, (2) chamber bridge match against
    an existing ``LegislatorTerm``, (3) normalized-name match disambiguated by
    term-window overlap. Ambiguous name matches are written to the merge
    review queue and ``None`` is returned (caller creates a new legislator
    for now; admin can re-link later). See ADR-0015.
    """
    bcn_uri = (seed.get("bcn_uri") or "").strip() or None
    if bcn_uri:
        legislator = db.execute(
            select(Legislator).where(Legislator.bcn_uri == bcn_uri)
        ).scalar_one_or_none()
        if legislator is not None:
            return legislator

    for term in seed.get("terms") or []:
        external_id = (term.get("chamber_external_id") or "").strip()
        if not external_id:
            continue
        existing_term = db.execute(
            select(LegislatorTerm)
            .options(selectinload(LegislatorTerm.legislator))
            .where(LegislatorTerm.chamber_external_id == external_id)
            .limit(1)
        ).scalar_one_or_none()
        if existing_term is not None:
            return existing_term.legislator

    candidates = _find_legislator_candidates(
        db,
        seed.get("paternal_last_name") or "",
        seed.get("maternal_last_name") or "",
        seed.get("first_name") or "",
    )
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    resolved = _disambiguate_by_term_overlap(candidates, seed.get("terms") or [])
    if resolved is not None:
        return resolved
    _write_merge_candidate(db, seed, [c.id for c in candidates])
    return None


def _apply_seed_fields(legislator: Legislator, seed: dict[str, Any]) -> bool:
    """Fill empty person-level fields from the seed without overwriting set ones.

    Person-level data accumulates across ingest sources (OpenData has birth
    date and gender; senado.cl carries photos and phone). We only write a
    field when the new value is non-empty and the existing column is empty,
    so a deputy-side payload doesn't blank out the senator-side photo (and
    vice versa).
    """
    changed = False

    def _maybe(column: str, value: object, *, max_len: int | None = None) -> None:
        nonlocal changed
        if value is None:
            return
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return
            if max_len is not None:
                value = value[:max_len]
        current = getattr(legislator, column)
        if current is None or current == "":
            setattr(legislator, column, value)
            changed = True

    _maybe("first_name", seed.get("first_name"), max_len=100)
    _maybe("last_name", seed.get("last_name"), max_len=100)
    _maybe("full_name", seed.get("full_name"), max_len=200)
    _maybe("gender", seed.get("gender"), max_len=1)
    birth = _parse_date(seed.get("birth_date"))
    if birth is not None and legislator.birth_date is None:
        legislator.birth_date = birth
        changed = True
    _maybe("email", seed.get("email"), max_len=255)
    _maybe("phone", seed.get("phone"), max_len=50)
    _maybe("photo_url", seed.get("photo_url"), max_len=500)
    _maybe("photo_thumbnail_url", seed.get("photo_thumbnail_url"), max_len=500)
    _maybe("profile_url", seed.get("profile_url"), max_len=500)
    _maybe("bcn_uri", seed.get("bcn_uri"), max_len=500)
    _maybe("bcn_wiki_url", seed.get("bcn_wiki_url"), max_len=500)
    return changed


def upsert_legislator(db: Session, data: dict[str, Any]) -> Legislator:
    """Upsert one person (and their stints) from a normalized roster seed.

    The seed is the unified shape emitted by
    :class:`app.ingestors.parsers.legislators.LegislatorParser`: person-level
    fields plus a ``terms`` list of per-stint payloads. The function
    resolves the canonical :class:`Legislator` via :func:`_merge_legislator_seed`
    (creating it if needed), applies person-level fields without overwriting
    populated columns, then reconciles the term list. See ADR-0015.
    """
    legislator = _merge_legislator_seed(db, data)
    if legislator is None:
        legislator = Legislator(
            first_name=(data.get("first_name") or "")[:100] or "Desconocido",
            last_name=(data.get("last_name") or "")[:100] or "Desconocido",
            full_name=(data.get("full_name") or "")[:200] or "Desconocido",
            gender=(data.get("gender") or "")[:1] or None,
            birth_date=_parse_date(data.get("birth_date")),
            email=(data.get("email") or "")[:255] or None,
            phone=(data.get("phone") or "")[:50] or None,
            photo_url=(data.get("photo_url") or "")[:500] or None,
            photo_thumbnail_url=(data.get("photo_thumbnail_url") or "")[:500] or None,
            profile_url=(data.get("profile_url") or "")[:500] or None,
            bcn_uri=(data.get("bcn_uri") or "")[:500] or None,
            bcn_wiki_url=(data.get("bcn_wiki_url") or "")[:500] or None,
        )
        db.add(legislator)
        db.flush()
        changed_person = False
    else:
        changed_person = _apply_seed_fields(legislator, data)

    # Need terms loaded for reconciliation.
    legislator = db.execute(
        select(Legislator)
        .options(selectinload(Legislator.terms))
        .where(Legislator.id == legislator.id)
    ).scalar_one()

    changed_terms = _reconcile_terms(db, legislator, data.get("terms") or [])
    if changed_person or changed_terms:
        _touch_syncable(db, legislator)
    db.flush()
    return legislator


def enrich_legislator_profile(
    db: Session,
    bcn_uri: str | None = None,
    fields: dict[str, Any] | None = None,
    *,
    chamber_external_id: str | None = None,
) -> Legislator | None:
    """Partially update an existing legislator with scraped/queried profile data.

    Matches by ``bcn_uri`` (preferred — the cross-chamber identity) or by
    ``chamber_external_id`` via an existing :class:`LegislatorTerm`. Writes
    only enrichment columns: photos, biography, profile URL, plus the
    BCN-sourced ``bcn_uri``, ``bcn_wiki_url``, ``profession``,
    ``twitter_handle``, and ``gender``. Never touches name; per-stint
    fields (district, party) belong on terms and are not touched here.
    Returns ``None`` if no legislator matches. See ADR-0015.
    """
    fields = fields or {}
    legislator: Legislator | None = None
    bcn_uri = (bcn_uri or "").strip() or None
    if bcn_uri:
        legislator = db.execute(
            select(Legislator).where(Legislator.bcn_uri == bcn_uri)
        ).scalar_one_or_none()
    if legislator is None and chamber_external_id:
        term = db.execute(
            select(LegislatorTerm)
            .options(selectinload(LegislatorTerm.legislator))
            .where(LegislatorTerm.chamber_external_id == chamber_external_id)
            .limit(1)
        ).scalar_one_or_none()
        if term is not None:
            legislator = term.legislator
    if legislator is None:
        return None

    changed = False
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


def upsert_term_appointment(
    db: Session,
    *,
    bcn_uri: str,
    bcn_appointment_uri: str,
    chamber_type: ChamberType,
    start_date: date,
    end_date: date,
) -> LegislatorTerm | None:
    """Upsert a SPARQL-sourced appointment into the :class:`LegislatorTerm` table.

    Matches the legislator by ``bcn_uri`` (the BCN person URI is the
    cross-chamber identity). Within that legislator's terms, looks for an
    existing row keyed by ``bcn_appointment_uri`` (the BCN PositionPeriod
    URI) and updates it; otherwise opens a new term. Returns ``None`` if
    no legislator matches the URI — the caller logs and skips. See ADR-0015.
    """
    legislator = db.execute(
        select(Legislator).where(Legislator.bcn_uri == bcn_uri)
    ).scalar_one_or_none()
    if legislator is None:
        return None

    chamber = _get_or_create_chamber(db, chamber_type)
    period = _resolve_term_period(db, start_date)
    if period is None:
        return None

    existing = db.execute(
        select(LegislatorTerm).where(
            LegislatorTerm.bcn_appointment_uri == bcn_appointment_uri
        )
    ).scalar_one_or_none()
    if existing is None:
        # Try to match an existing chamber+start term (so SPARQL just stamps
        # the URI onto a term that another source already opened).
        existing = db.execute(
            select(LegislatorTerm).where(
                LegislatorTerm.legislator_id == legislator.id,
                LegislatorTerm.chamber_id == chamber.id,
                LegislatorTerm.start_date == start_date,
            )
        ).scalar_one_or_none()
    if existing is None:
        term = LegislatorTerm(
            legislator_id=legislator.id,
            period_id=period.id,
            chamber_id=chamber.id,
            bcn_appointment_uri=bcn_appointment_uri,
            start_date=start_date,
            end_date=end_date,
        )
        db.add(term)
        db.flush()
        _reconcile_orphan_votes(db, term)
        return term

    changed = False
    if existing.legislator_id != legislator.id:
        existing.legislator_id = legislator.id
        changed = True
    if existing.bcn_appointment_uri != bcn_appointment_uri:
        existing.bcn_appointment_uri = bcn_appointment_uri
        changed = True
    if existing.end_date != end_date and end_date is not None:
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

    Editorial data with no upstream source (see ADR-0014). Re-running with the
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
    sending the legislator back to the "sin alinear" tray. See ADR-0014.
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
        # ``no_votes`` is derived from the reconciled individual_votes (after
        # senate synthesis), not from upstream — chamber XML has no
        # ``TotalNoVota`` aggregate, and senate restsil has no aggregate either.
        # The reconciler sets the final value below.
        no_votes=0,
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
                "no_votes": insert_stmt.excluded.no_votes,
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


def upsert_legislature(db: Session, data: dict[str, Any]) -> Legislature:
    """Upsert a ``Legislature`` (1-year cycle) keyed on its historical ``number``.

    The parent ``LegislativePeriod`` is resolved by ``start_date``: we pick the
    most recently-started period whose start is on or before this legislatura's
    start. Assumes contiguous periods (half-open ``[start, end)``); see
    CONTEXT.md "Legislatura" + ADR-0016.
    """

    start_date_value = _parse_date(data.get("start_date")) or date.today()
    end_date_value = _parse_date(data.get("end_date")) or start_date_value
    kind_value = (data.get("kind") or "ordinaria").strip().lower()
    try:
        kind_enum = LegislatureKind(kind_value)
    except ValueError:
        kind_enum = LegislatureKind.ORDINARIA

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
            f"No legislative period found for legislature start_date={start_date_value}"
        )

    insert_stmt = pg_insert(Legislature).values(
        number=int(data["number"]),
        period_id=period.id,
        start_date=start_date_value,
        end_date=end_date_value,
        kind=kind_enum,
        description=(data.get("description") or "")[:200] or None,
    )
    legislature_id = db.execute(
        insert_stmt.on_conflict_do_update(
            index_elements=["number"],
            set_={
                "period_id": insert_stmt.excluded.period_id,
                "start_date": insert_stmt.excluded.start_date,
                "end_date": insert_stmt.excluded.end_date,
                "kind": insert_stmt.excluded.kind,
                "description": insert_stmt.excluded.description,
                "updated_at": func.now(),
                "sync_version": global_sync_version_seq.next_value(),
            },
        ).returning(Legislature.id)
    ).scalar_one()
    legislature = db.get(Legislature, legislature_id)
    if legislature is None:
        raise RuntimeError(f"Failed to load legislature id={legislature_id}")
    return legislature


def upsert_meeting_session(db: Session, data: dict[str, Any]) -> LegislativeSession:
    """Upsert a ``LegislativeSession`` (single Sesión meeting).

    The parent ``Legislature`` is resolved by ``_legislature_number`` when
    provided; otherwise by start_date falling within the half-open Legislature
    window. ``committee_id`` is null for Sala (plenary) sessions and points at
    the relevant ``Committee`` for Comisión sessions.
    """

    chamber = _get_or_create_chamber(db, data.get("_chamber_type") or "deputies")
    start_date_value = _parse_date(data.get("start_date")) or date.today()
    end_date_value = _parse_date(data.get("end_date"))

    legislature: Legislature | None = None
    legislature_number = data.get("_legislature_number")
    if legislature_number is not None:
        legislature = db.execute(
            select(Legislature).where(Legislature.number == int(legislature_number))
        ).scalar_one_or_none()
    if legislature is None:
        legislature = (
            db.execute(
                select(Legislature)
                .where(Legislature.start_date <= start_date_value)
                .where(Legislature.end_date > start_date_value)
                .order_by(Legislature.start_date.desc())
            )
            .scalars()
            .first()
        )
    if legislature is None:
        raise ValueError(
            f"No legislature found for session start_date={start_date_value} "
            f"number={legislature_number}"
        )

    committee_id: int | None = None
    committee_external_id = data.get("_committee_external_id")
    if committee_external_id is not None:
        committee = db.execute(
            select(Committee).where(Committee.id == int(committee_external_id))
        ).scalar_one_or_none()
        if committee is not None:
            committee_id = committee.id

    kind_value = (data.get("kind") or "ordinaria").strip().lower()
    try:
        kind_enum = SessionKind(kind_value)
    except ValueError:
        kind_enum = SessionKind.ORDINARIA

    session = db.execute(
        select(LegislativeSession)
        .where(LegislativeSession.legislature_id == legislature.id)
        .where(LegislativeSession.chamber_id == chamber.id)
        .where(LegislativeSession.number == int(data["number"]))
        .where(
            LegislativeSession.committee_id.is_(None)
            if committee_id is None
            else LegislativeSession.committee_id == committee_id
        )
    ).scalar_one_or_none()
    if session is None:
        session = LegislativeSession(
            number=int(data["number"]),
            kind=kind_enum,
            legislature_id=legislature.id,
            chamber_id=chamber.id,
            committee_id=committee_id,
            start_date=start_date_value,
            end_date=end_date_value,
        )
        db.add(session)
    else:
        changed = False
        if session.kind != kind_enum:
            session.kind = kind_enum
            changed = True
        if session.start_date != start_date_value:
            session.start_date = start_date_value
            changed = True
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


def upsert_calendar_event(db: Session, data: dict[str, Any]) -> CalendarEvent:
    """Upsert a :class:`CalendarEvent` row.

    Single mutation entrypoint for the calendar — both the admin form and
    future agenda scrapers call here. When ``external_ref`` is provided,
    dedup by ``(source, external_ref)`` and update in place; otherwise
    every call inserts a new row (manual entries don't dedup). See
    CONTEXT.md "Calendar event".
    """

    kind_raw = data.get("kind")
    if isinstance(kind_raw, CalendarEventKind):
        kind = kind_raw
    elif kind_raw is None:
        raise ValueError("CalendarEvent requires kind")
    else:
        kind = CalendarEventKind(str(kind_raw))

    source_raw = data.get("source", CalendarEventSource.MANUAL)
    if isinstance(source_raw, CalendarEventSource):
        source = source_raw
    else:
        source = CalendarEventSource(str(source_raw))

    starts_at = data.get("starts_at")
    if not isinstance(starts_at, datetime):
        raise ValueError("CalendarEvent requires a datetime starts_at")
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)

    ends_at = data.get("ends_at")
    if ends_at is not None and not isinstance(ends_at, datetime):
        raise ValueError("CalendarEvent ends_at must be a datetime or None")
    if isinstance(ends_at, datetime) and ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)

    title = (data.get("title") or "").strip()
    if not title:
        raise ValueError("CalendarEvent requires title")

    chamber_raw = data.get("chamber_type")
    chamber_type: ChamberType | None
    if chamber_raw is None or chamber_raw == "":
        chamber_type = None
    elif isinstance(chamber_raw, ChamberType):
        chamber_type = chamber_raw
    else:
        chamber_type = ChamberType(str(chamber_raw))

    external_ref = data.get("external_ref")
    if external_ref is not None:
        external_ref = str(external_ref).strip() or None

    attrs = {
        "kind": kind,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "title": title[:300],
        "description": data.get("description") or None,
        "location": (data.get("location") or None) and data["location"][:200],
        "chamber_type": chamber_type,
        "bill_id": data.get("bill_id"),
        "legislator_id": data.get("legislator_id"),
        "committee_id": data.get("committee_id"),
        "source": source,
        "external_ref": external_ref,
    }

    event: CalendarEvent | None = None
    if external_ref is not None:
        event = db.execute(
            select(CalendarEvent)
            .where(CalendarEvent.source == source)
            .where(CalendarEvent.external_ref == external_ref)
        ).scalar_one_or_none()

    if event is None:
        event = CalendarEvent(**attrs)
        db.add(event)
    else:
        if _set_syncable_attrs(db, event, **attrs):
            _touch_syncable(db, event)
    db.flush()
    return event

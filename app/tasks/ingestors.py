import asyncio
import datetime
import logging
import time
from typing import Any

from sqlalchemy import select

from app.core.celery_app import app
from app.core.config import settings
from app.core.session import task_session
from app.ingestors.clients.bcn import (
    BCNClient,
    fetch_person_appointments_parallel,
    fetch_person_profiles_parallel,
)
from app.ingestors.clients.camara import CamaraClient
from app.ingestors.clients.opendata_camara import OpenDataCamaraClient
from app.ingestors.clients.opendata_camara_async import (
    fetch_bill_details_parallel,
    fetch_voting_details_parallel,
)
from app.ingestors.clients.senado import SenadoClient
from app.ingestors.clients.senado_async import (
    fetch_bills_parallel,
    fetch_votes_parallel,
)
from app.ingestors.clients.senado_web import SenadoWebClient
from app.ingestors.parsers.bills import BillParser
from app.ingestors.parsers.committees import CommitteeParser
from app.ingestors.parsers.legislators import LegislatorParser
from app.ingestors.parsers.legislature import LegislatureParser
from app.models.enums import ChamberType
from app.models.ingestor_state import IngestorState
from app.tasks.base import DatabaseTask
from app.tasks.bills import sync_bill
from app.tasks.committees import sync_committee
from app.tasks.legislators import (
    sync_legislator,
    sync_legislator_bcn_enrichment,
    sync_parliamentary_appointment,
)
from app.tasks.legislature import sync_period, sync_session
from app.tasks.reference import sync_topic

logger = logging.getLogger(__name__)

REQUEST_DELAY = 1.0


def _get_state(db, entity_type: str, *, create: bool = True) -> IngestorState | None:
    state = db.execute(
        select(IngestorState).where(IngestorState.entity_type == entity_type)
    ).scalar_one_or_none()
    if state is None and create:
        state = IngestorState(entity_type=entity_type)
        db.add(state)
        db.flush()
    return state


def _build_dispatch_result(
    dispatched: int, errors: int, dry_run: bool, **extra: Any
) -> dict[str, Any]:
    result: dict[str, Any] = {"errors": errors, "dry_run": dry_run, **extra}
    if dry_run:
        result["dispatched"] = 0
        result["would_dispatch"] = dispatched
    else:
        result["dispatched"] = dispatched
    return result


def _get_last_sync_date(entity_type: str) -> datetime.date | None:
    try:
        with task_session() as db:
            state = _get_state(db, entity_type, create=False)
            if state is not None:
                return state.last_sync_date
    except Exception:
        logger.warning(
            "Failed to load ingestor state for %s", entity_type, exc_info=True
        )
    return None


def _mark_synced(entity_type: str) -> None:
    with task_session() as db:
        state = _get_state(db, entity_type)
        if state is not None:
            state.last_sync_date = datetime.date.today()


def _dispatch(task: Any, *args: Any) -> None:
    task.delay(*args)


def _load_opendata_bill_details_with_votes(
    bulletins: list[str],
) -> tuple[dict[str, dict[str, Any] | None], int]:
    errors = 0

    details_by_bulletin: dict[str, dict[str, Any] | None] = dict(
        asyncio.run(fetch_bill_details_parallel(bulletins))
    )

    all_vote_ids: list[int] = []
    for detail in details_by_bulletin.values():
        if detail is None:
            continue
        for raw_vote in detail.get("chamber_votes", []):
            voting_id = raw_vote.get("id")
            if voting_id:
                all_vote_ids.append(int(voting_id))

    vote_details_by_id: dict[int, dict[str, Any]] = {}
    if all_vote_ids:
        vote_results = asyncio.run(fetch_voting_details_parallel(all_vote_ids))
        vote_details_by_id = {
            voting_id: vote_detail
            for voting_id, vote_detail in vote_results
            if vote_detail is not None
        }
        errors += sum(
            1 for voting_id in all_vote_ids if voting_id not in vote_details_by_id
        )

    for detail in details_by_bulletin.values():
        if detail is None:
            continue
        enriched_votes: list[dict[str, Any]] = []
        for raw_vote in detail.get("chamber_votes", []):
            voting_id = raw_vote.get("id")
            if not voting_id:
                enriched_votes.append(raw_vote)
                continue
            vote_detail = vote_details_by_id.get(int(voting_id))
            if vote_detail is None:
                enriched_votes.append(raw_vote)
                continue
            enriched_vote = {**raw_vote, **vote_detail}
            enriched_vote["individual_votes"] = vote_detail.get("individual_votes", [])
            enriched_votes.append(enriched_vote)
        detail["chamber_votes"] = enriched_votes

    return details_by_bulletin, errors


def run_ingest_bills(
    bulletin: str | None = None,
    since: str | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    dispatched = 0
    errors = 0
    bulletins: list[str] = []
    since_date: datetime.date | None = None
    mode = "single_bulletin" if bulletin else "full_scan"

    try:
        if since:
            since_date = datetime.date.fromisoformat(since)

        if bulletin:
            bulletins = [bulletin]
        else:
            if since_date is None:
                since_date = _get_last_sync_date("bills")

            # Bulletin discovery is a bounded re-scan of OpenData's per-year bill
            # lists. ``since_date`` (from ``--since`` or the last sync) is coarsened
            # to its year: we re-scan from that year to the current one and re-fetch
            # each bill's full Senado detail. With no prior sync we backfill from the
            # configured start year. OpenData exposes no "modified since" field, so
            # late activity on bills older than the window is not picked up.
            current_year = datetime.date.today().year
            if since_date is not None:
                mode = "incremental"
                start_year = since_date.year
            else:
                start_year = settings.ingestor_bills_start_year

            seen: set[str] = set()
            with OpenDataCamaraClient() as opendata:
                for year in range(start_year, current_year + 1):
                    try:
                        for proyecto in opendata.get_mensajes_x_anno(year):
                            bn = proyecto["bulletin_number"]
                            if bn and bn not in seen:
                                seen.add(bn)
                                bulletins.append(bn)
                    except Exception:
                        logger.exception("Failed to fetch mensajes for year %d", year)
                        errors += 1
                    try:
                        for proyecto in opendata.get_mociones_x_anno(year):
                            bn = proyecto["bulletin_number"]
                            if bn and bn not in seen:
                                seen.add(bn)
                                bulletins.append(bn)
                    except Exception:
                        logger.exception("Failed to fetch mociones for year %d", year)
                        errors += 1

        if bulletins:
            results = asyncio.run(fetch_bills_parallel(bulletins))
            valid_bulletins = [bn for bn, raw in results if raw is not None]
            opendata_details, detail_errors = _load_opendata_bill_details_with_votes(
                valid_bulletins
            )
            errors += detail_errors
            # Senate votes come from the dedicated votaciones.php endpoint, which is
            # the complete source — the <votacion> nodes embedded in the bill detail
            # (tramitacion.php) are frequently absent (ADR-0008).
            senate_votes = dict(asyncio.run(fetch_votes_parallel(valid_bulletins)))
            for bulletin_number, raw in results:
                try:
                    if raw is None:
                        continue
                    payload = BillParser.parse_bill(raw)
                    fetched_votes = senate_votes.get(bulletin_number)
                    if fetched_votes is not None:
                        payload["_votaciones"] = fetched_votes
                    opendata_detail = opendata_details.get(bulletin_number)
                    if opendata_detail is not None:
                        payload.update(
                            BillParser.parse_opendata_enrichment(opendata_detail)
                        )
                    if not dry_run:
                        _dispatch(sync_bill, payload)
                    dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to ingest bill bulletin=%s", bulletin_number
                    )
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch bills")
        errors += 1

    if not dry_run:
        _mark_synced("bills")

    return _build_dispatch_result(
        dispatched,
        errors,
        dry_run,
        bulletin=bulletin,
        since=since_date.isoformat() if since_date else None,
        mode=mode,
        candidates=len(bulletins),
    )


def run_ingest_legislators(*, dry_run: bool = False) -> dict[str, Any]:
    """Refresh the legislator roster + biographic data.

    Per ADR-0005, BCN linked data is the source of truth for *which*
    legislators are currently seated. The pipeline shape:

    1. **Deputies** stay on OpenData Cámara for identity + party + district
       number (ADR-0001 / ADR-0003 unchanged); camara.cl district scraping
       runs separately.
    2. **Senators** come from BCN's active appointments cross-referenced with
       the senado.cl metadata catalog (circumscription, region, party
       abbreviation, email, phone, photo) by ``ID_PARLAMENTARIO``.
    3. **Both chambers** receive BCN biographic enrichment (profession,
       twitter handle, BCN wiki page, photo) joined by ``idCamara`` /
       ``idSenado``.
    4. **Term history** for both chambers is backfilled into
       ``ParliamentaryAppointment`` rows via per-URI fan-out.
    """
    dispatched = 0
    errors = 0

    # 1. Deputies — OpenData stays primary.
    try:
        with OpenDataCamaraClient() as opendata:
            for raw in opendata.get_diputados_periodo_actual():
                try:
                    payload = LegislatorParser.parse_opendata_deputy(raw)
                    if not dry_run:
                        _dispatch(sync_legislator, payload)
                    dispatched += 1
                except Exception:
                    logger.exception("Failed to parse deputy from OpenDataCamaraClient")
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch deputies from OpenDataCamaraClient")
        errors += 1

    time.sleep(REQUEST_DELAY)

    # 2. BCN roster — source of truth for who is currently seated (both chambers).
    bcn_rows: list[dict[str, Any]] = []
    try:
        with BCNClient() as bcn:
            bcn_rows = bcn.get_active_appointments()
    except Exception:
        logger.exception("Failed to fetch active appointments from BCN")
        errors += 1

    # Reduce to one normalized roster entry per bcn_id (latest term wins on
    # duplicates — should not happen for active legislators, but a defensive
    # de-dupe keeps the senator/deputy join clean).
    roster_by_bcn_id: dict[str, dict[str, Any]] = {}
    for row in bcn_rows:
        parsed = LegislatorParser.parse_bcn_roster_row(row)
        if parsed is None:
            continue
        roster_by_bcn_id[parsed["bcn_id"]] = parsed

    senator_roster = [
        entry
        for entry in roster_by_bcn_id.values()
        if entry["chamber_type"] == ChamberType.SENATE
    ]
    deputy_roster = [
        entry
        for entry in roster_by_bcn_id.values()
        if entry["chamber_type"] == ChamberType.DEPUTIES
    ]
    logger.info(
        "BCN roster: %d senators, %d deputies after de-dupe",
        len(senator_roster),
        len(deputy_roster),
    )

    # 3. Senators — BCN roster merged with senado.cl metadata catalog by PARLID.
    senate_catalog: dict[int, dict[str, Any]] = {}
    try:
        with SenadoWebClient() as senado_web:
            senate_catalog = senado_web.get_full_catalog()
    except Exception:
        logger.exception("Failed to fetch senate catalog from SenadoWebClient")
        errors += 1

    for entry in senator_roster:
        try:
            try:
                parlid = int(entry["external_id"])
            except TypeError, ValueError:
                parlid = None
            catalog_row = senate_catalog.get(parlid) if parlid is not None else None
            if catalog_row is None:
                logger.warning(
                    "BCN senator %s has no senado catalog entry (PARLID %s)",
                    entry["bcn_id"],
                    entry.get("external_id"),
                )
                continue
            payload = LegislatorParser.parse_senator(catalog_row)
            payload["bcn_uri"] = entry["bcn_uri"]
            if not dry_run:
                _dispatch(sync_legislator, payload)
            dispatched += 1
        except Exception:
            logger.exception("Failed to merge senator %s", entry.get("bcn_id"))
            errors += 1

    # 4. BCN biographic enrichment — fan-out per URI for both chambers.
    enrichment_uris = [
        entry["bcn_uri"] for entry in roster_by_bcn_id.values() if entry.get("bcn_uri")
    ]
    profiles: dict[str, dict[str, Any] | None] = {}
    try:
        profiles = asyncio.run(fetch_person_profiles_parallel(enrichment_uris))
    except Exception:
        logger.exception("Failed to fan-out BCN profile enrichment")
        errors += 1

    for entry in roster_by_bcn_id.values():
        try:
            profile = profiles.get(entry["bcn_uri"])
            if profile is None:
                continue
            payload = LegislatorParser.parse_bcn_profile(profile)
            # Drop empty entries so enrich_legislator_profile does not touch
            # already-populated columns with empty strings.
            cleaned = {k: v for k, v in payload.items() if v}
            cleaned["bcn_uri"] = entry["bcn_uri"]
            if not dry_run:
                _dispatch(sync_legislator_bcn_enrichment, entry["bcn_id"], cleaned)
            dispatched += 1
        except Exception:
            logger.exception(
                "Failed to dispatch BCN enrichment for %s", entry.get("bcn_id")
            )
            errors += 1

    # 5. Term history backfill — every past + present appointment per legislator.
    appointments_by_uri: dict[str, list[dict[str, Any]]] = {}
    try:
        appointments_by_uri = asyncio.run(
            fetch_person_appointments_parallel(enrichment_uris)
        )
    except Exception:
        logger.exception("Failed to fan-out BCN appointment history")
        errors += 1

    for entry in roster_by_bcn_id.values():
        appointments = appointments_by_uri.get(entry["bcn_uri"], [])
        for appointment in appointments:
            try:
                term_payload = LegislatorParser.parse_bcn_appointment(appointment)
                if term_payload is None:
                    continue
                term_payload["chamber_type"] = term_payload["chamber_type"].value
                if not dry_run:
                    _dispatch(
                        sync_parliamentary_appointment,
                        entry["bcn_id"],
                        term_payload,
                    )
                dispatched += 1
            except Exception:
                logger.exception(
                    "Failed to dispatch appointment for %s", entry.get("bcn_id")
                )
                errors += 1

    if not dry_run:
        _mark_synced("legislators")

    return _build_dispatch_result(dispatched, errors, dry_run)


def run_ingest_committees(*, dry_run: bool = False) -> dict[str, Any]:
    dispatched = 0
    errors = 0

    try:
        with SenadoClient() as senado:
            for raw in senado.get_comisiones():
                try:
                    payload = CommitteeParser.parse_senate_committee(raw)
                    if not dry_run:
                        _dispatch(sync_committee, payload)
                    dispatched += 1
                except Exception:
                    logger.exception("Failed to parse senate committee")
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch committees from SenadoClient")
        errors += 1

    time.sleep(REQUEST_DELAY)

    try:
        with OpenDataCamaraClient() as opendata:
            for raw in opendata.get_comisiones_vigentes():
                try:
                    time.sleep(REQUEST_DELAY)
                    comision_id = raw.get("id")
                    detail = opendata.get_comision(comision_id) if comision_id else None
                    payload = (
                        CommitteeParser.parse_opendata_committee_detail(detail)
                        if detail
                        else CommitteeParser.parse_opendata_committee(raw)
                    )
                    if not dry_run:
                        _dispatch(sync_committee, payload)
                    dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse opendata committee id=%s", raw.get("id")
                    )
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch committees from OpenDataCamaraClient")
        errors += 1

    if not dry_run:
        _mark_synced("committees")

    return _build_dispatch_result(dispatched, errors, dry_run)


def run_ingest_legislature(*, dry_run: bool = False) -> dict[str, Any]:
    dispatched = 0
    errors = 0

    try:
        with OpenDataCamaraClient() as opendata:
            for raw in opendata.get_periodos_legislativos():
                try:
                    parsed = LegislatureParser.parse_legislative_period(raw)
                    if parsed.get("number") and parsed.get("start_date"):
                        if not dry_run:
                            _dispatch(sync_period, parsed)
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse legislative period from OpenDataCamaraClient"
                    )
                    errors += 1
            time.sleep(REQUEST_DELAY)
            for raw in opendata.get_legislaturas():
                try:
                    parsed = LegislatureParser.parse_legislature(raw)
                    if parsed.get("number") and parsed.get("start_date"):
                        if not dry_run:
                            _dispatch(sync_session, parsed)
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse legislative session from OpenDataCamaraClient"
                    )
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch legislature data from OpenDataCamaraClient")
        errors += 1

    time.sleep(REQUEST_DELAY)

    try:
        with CamaraClient() as camara:
            for raw in camara.get_periodos_legislativos():
                try:
                    parsed = LegislatureParser.parse_legislative_period(raw)
                    if parsed.get("number") and parsed.get("start_date"):
                        if not dry_run:
                            _dispatch(sync_period, parsed)
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse legislative period from CamaraClient"
                    )
                    errors += 1
            time.sleep(REQUEST_DELAY)
            for raw in camara.get_legislaturas():
                try:
                    parsed = LegislatureParser.parse_legislature(raw)
                    if parsed.get("number") and parsed.get("start_date"):
                        if not dry_run:
                            _dispatch(sync_session, parsed)
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse legislative session from CamaraClient"
                    )
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch legislature data from CamaraClient")
        errors += 1

    if not dry_run:
        _mark_synced("legislature")

    return _build_dispatch_result(dispatched, errors, dry_run)


def run_ingest_reference_data(*, dry_run: bool = False) -> dict[str, Any]:
    dispatched = 0
    errors = 0

    try:
        with OpenDataCamaraClient() as opendata:
            for topic in opendata.get_materias():
                try:
                    if topic.get("name"):
                        if not dry_run:
                            _dispatch(sync_topic, topic)
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse reference topic name=%s", topic.get("name")
                    )
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch reference data from OpenDataCamaraClient")
        errors += 1

    if not dry_run:
        _mark_synced("reference")

    return _build_dispatch_result(dispatched, errors, dry_run)


@app.task(name="app.tasks.ingestors.ingest_bills", bind=True, base=DatabaseTask)
def ingest_bills(self, bulletin: str | None = None, since: str | None = None) -> dict:
    return run_ingest_bills(bulletin=bulletin, since=since)


@app.task(name="app.tasks.ingestors.ingest_legislators", bind=True, base=DatabaseTask)
def ingest_legislators(self) -> dict:
    return run_ingest_legislators()


@app.task(name="app.tasks.ingestors.ingest_committees", bind=True, base=DatabaseTask)
def ingest_committees(self) -> dict:
    return run_ingest_committees()


@app.task(name="app.tasks.ingestors.ingest_legislature", bind=True, base=DatabaseTask)
def ingest_legislature(self) -> dict:
    return run_ingest_legislature()


@app.task(
    name="app.tasks.ingestors.ingest_reference_data", bind=True, base=DatabaseTask
)
def ingest_reference_data(self) -> dict:
    return run_ingest_reference_data()

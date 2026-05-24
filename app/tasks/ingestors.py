import asyncio
import datetime
import logging
import time
from typing import Any

from sqlalchemy import select

from app.core.celery_app import app
from app.core.config import settings
from app.core.session import task_session
from app.ingestors.clients.camara import CamaraClient
from app.ingestors.clients.opendata_camara import OpenDataCamaraClient
from app.ingestors.clients.opendata_camara_async import fetch_voting_details_parallel
from app.ingestors.clients.senado import SenadoClient
from app.ingestors.clients.senado_async import fetch_bills_parallel
from app.ingestors.parsers.bills import BillParser
from app.ingestors.parsers.committees import CommitteeParser
from app.ingestors.parsers.legislators import LegislatorParser
from app.ingestors.parsers.legislature import LegislatureParser
from app.ingestors.parsers.votes import VoteParser
from app.models.ingestor_state import IngestorState
from app.tasks.base import DatabaseTask
from app.tasks.bills import sync_bill
from app.tasks.committees import sync_committee
from app.tasks.legislators import sync_legislator
from app.tasks.legislature import sync_period, sync_session
from app.tasks.reference import sync_district, sync_region
from app.tasks.voting import sync_voting_session

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


def _resolve_since_date(entity_type: str, *, fallback_days: int) -> datetime.date:
    fallback = datetime.date.today() - datetime.timedelta(days=fallback_days)
    last_sync_date = _get_last_sync_date(entity_type)
    if last_sync_date is not None:
        return last_sync_date
    return fallback


def _mark_synced(entity_type: str) -> None:
    with task_session() as db:
        state = _get_state(db, entity_type)
        if state is not None:
            state.last_sync_date = datetime.date.today()


def _dispatch(task: Any, *args: Any) -> None:
    task.delay(*args)


def _load_opendata_bill_detail_with_votes(
    opendata: OpenDataCamaraClient, bulletin_number: str
) -> tuple[dict[str, Any] | None, int]:
    errors = 0
    detail = opendata.get_bill_detail(bulletin_number)
    if detail is None:
        return None, errors

    requested_vote_ids = [
        int(voting_id)
        for raw_vote in detail.get("chamber_votes", [])
        for voting_id in [raw_vote.get("id")]
        if voting_id
    ]
    vote_details_by_id: dict[int, dict[str, Any]] = {}
    if requested_vote_ids:
        vote_results = asyncio.run(fetch_voting_details_parallel(requested_vote_ids))
        vote_details_by_id = {
            voting_id: vote_detail
            for voting_id, vote_detail in vote_results
            if vote_detail is not None
        }
        errors += sum(
            1 for voting_id in requested_vote_ids if voting_id not in vote_details_by_id
        )

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
    return detail, errors


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

            seen: set[str] = set()
            if since_date is not None:
                mode = "incremental"
                with SenadoClient() as senado:
                    for bulletin_number in senado.get_bills_by_date(since_date):
                        if bulletin_number and bulletin_number not in seen:
                            seen.add(bulletin_number)
                            bulletins.append(bulletin_number)
            else:
                start_year = settings.ingestor_bills_start_year
                current_year = datetime.date.today().year
                with OpenDataCamaraClient() as opendata:
                    for year in range(start_year, current_year + 1):
                        try:
                            for proyecto in opendata.get_mensajes_x_anno(year):
                                bn = proyecto["bulletin_number"]
                                if bn and bn not in seen:
                                    seen.add(bn)
                                    bulletins.append(bn)
                        except Exception:
                            logger.exception(
                                "Failed to fetch mensajes for year %d", year
                            )
                            errors += 1
                        try:
                            for proyecto in opendata.get_mociones_x_anno(year):
                                bn = proyecto["bulletin_number"]
                                if bn and bn not in seen:
                                    seen.add(bn)
                                    bulletins.append(bn)
                        except Exception:
                            logger.exception(
                                "Failed to fetch mociones for year %d", year
                            )
                            errors += 1

        if bulletins:
            results = asyncio.run(fetch_bills_parallel(bulletins))
            with OpenDataCamaraClient() as opendata:
                for bulletin_number, raw in results:
                    try:
                        if raw is None:
                            continue
                        payload = BillParser.parse_bill(raw)
                        time.sleep(REQUEST_DELAY)
                        opendata_detail, detail_errors = (
                            _load_opendata_bill_detail_with_votes(
                                opendata, bulletin_number
                            )
                        )
                        errors += detail_errors
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
    dispatched = 0
    errors = 0

    try:
        with SenadoClient() as senado:
            for raw in senado.get_senadores_vigentes():
                try:
                    payload = LegislatorParser.parse_senator(raw)
                    if not dry_run:
                        _dispatch(sync_legislator, payload)
                    dispatched += 1
                except Exception:
                    logger.exception("Failed to parse senator from SenadoClient")
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch senators from SenadoClient")
        errors += 1

    time.sleep(REQUEST_DELAY)

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
            for region in opendata.get_regiones():
                try:
                    if region.get("number"):
                        if not dry_run:
                            _dispatch(sync_region, region)
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse region number=%s", region.get("number")
                    )
                    errors += 1
            time.sleep(REQUEST_DELAY)
            for district in opendata.get_distritos():
                try:
                    if district.get("number"):
                        if not dry_run:
                            _dispatch(sync_district, district)
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse district number=%s", district.get("number")
                    )
                    errors += 1
    except Exception:
        logger.exception("Failed to fetch reference data from OpenDataCamaraClient")
        errors += 1

    if not dry_run:
        _mark_synced("reference")

    return _build_dispatch_result(dispatched, errors, dry_run)


def run_ingest_voting_sessions(
    since: str | None = None, *, dry_run: bool = False
) -> dict[str, Any]:
    dispatched = 0
    errors = 0
    bulletins: list[str] = []
    since_date = datetime.date.fromisoformat(since) if since else None
    if since_date is None:
        since_date = _resolve_since_date("voting", fallback_days=1)

    try:
        with SenadoClient() as senado:
            seen: set[str] = set()
            for bulletin_number in senado.get_bills_by_date(since_date):
                if not bulletin_number or bulletin_number in seen:
                    continue
                seen.add(bulletin_number)
                bulletins.append(bulletin_number)
                try:
                    time.sleep(REQUEST_DELAY)
                    for raw_vote in senado.get_votes_by_bulletin(bulletin_number):
                        try:
                            payload = VoteParser.parse_senate_vote(
                                raw_vote, bulletin=bulletin_number
                            )
                            if not dry_run:
                                _dispatch(sync_voting_session, payload, bulletin_number)
                            dispatched += 1
                        except Exception:
                            logger.exception(
                                "Failed to parse vote for bulletin=%s", bulletin_number
                            )
                            errors += 1
                except Exception:
                    logger.exception(
                        "Failed to fetch votes for bulletin=%s", bulletin_number
                    )
                    errors += 1
    except Exception:
        logger.exception(
            "Failed to fetch bill bulletins for voting from SenadoClient since=%s",
            since_date,
        )
        errors += 1

    try:
        with OpenDataCamaraClient() as opendata:
            for bulletin_number in bulletins:
                try:
                    time.sleep(REQUEST_DELAY)
                    detail, detail_errors = _load_opendata_bill_detail_with_votes(
                        opendata, bulletin_number
                    )
                    errors += detail_errors
                    if detail is None:
                        continue
                    for raw_vote in detail.get("chamber_votes", []):
                        voting_id = raw_vote.get("id")
                        if not voting_id:
                            continue
                        try:
                            payload = VoteParser.parse_chamber_vote(
                                raw_vote, bulletin=bulletin_number
                            )
                            if not dry_run:
                                _dispatch(sync_voting_session, payload, bulletin_number)
                            dispatched += 1
                        except Exception:
                            logger.exception(
                                "Failed to parse Chamber vote bulletin=%s voting_id=%s",
                                bulletin_number,
                                voting_id,
                            )
                            errors += 1
                except Exception:
                    logger.exception(
                        "Failed to fetch Chamber votes for bulletin=%s",
                        bulletin_number,
                    )
                    errors += 1
    except Exception:
        logger.exception(
            "Failed to fetch Chamber voting sessions from OpenDataCamaraClient"
        )
        errors += 1

    if not dry_run:
        _mark_synced("voting")

    return _build_dispatch_result(
        dispatched, errors, dry_run, since=since_date.isoformat()
    )


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


@app.task(
    name="app.tasks.ingestors.ingest_voting_sessions", bind=True, base=DatabaseTask
)
def ingest_voting_sessions(self, since: str | None = None) -> dict:
    return run_ingest_voting_sessions(since=since)

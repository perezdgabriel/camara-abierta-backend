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
    fetch_person_appointments_parallel,
    fetch_person_profiles_parallel,
)
from app.ingestors.clients.bcn_rest import BCNRestClient
from app.ingestors.clients.camara import CamaraClient
from app.ingestors.clients.opendata_camara import (
    OpenDataCamaraClient,
    parse_bulletin_from_description,
)
from app.ingestors.clients.opendata_camara_async import (
    fetch_bill_details_parallel,
    fetch_chamber_vote_summaries_parallel,
    fetch_voting_details_parallel,
)
from app.ingestors.clients.restsil_senado import RestsilSenadoClient
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
from app.ingestors.parsers.votes import VoteParser
from app.models.ingestor_state import IngestorState
from app.tasks.base import DatabaseTask
from app.tasks.bills import sync_bill
from app.tasks.committees import sync_committee
from app.tasks.legislators import (
    sync_legislator,
    sync_legislator_bcn_enrichment,
    sync_parliamentary_appointment,
)
from app.tasks.legislature import sync_legislature, sync_period
from app.tasks.reference import sync_topic
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


def _discover_bulletins_opendata(
    start_year: int, current_year: int
) -> tuple[list[str], int]:
    """Legacy OpenData year-scan discovery (ADR-0013).

    Iterates ``get_mensajes_x_anno`` + ``get_mociones_x_anno`` across the
    requested year range. Kept in tree as the failover path per ADR-0013 —
    activated when ``settings.ingestor_bills_source == "opendata"``.
    """
    seen: set[str] = set()
    bulletins: list[str] = []
    errors = 0
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
    return bulletins, errors


def _discover_bulletins_restsil(
    start_year: int, current_year: int, *, full_backfill: bool
) -> tuple[list[str], int, bool]:
    """Restsil desc-paged discovery (ADR-0013).

    Policy (also documented in the ADR):

    - Always scan the **current year**, all statuses — picks up newly filed
      bills and anything just-changed via the re-fetch loop in
      ``run_ingest_bills``.
    - Past years are scanned only when ``full_backfill`` is true (cold start
      or daily gate elapsed). In daily mode we restrict the past-years scan
      to ``estado=T`` (~7,000 rows globally vs ~18,000 unfiltered), since
      terminal bills won't get new activity that the existing
      ``upsert_bill`` reconciliation cares about.

    Returns ``(bulletins, errors, scanned_past_years)`` so the caller can
    update the ``last_full_year_scan_date`` cursor only after a successful
    past-years sweep.
    """
    seen: set[str] = set()
    bulletins: list[str] = []
    errors = 0
    scanned_past_years = False
    with RestsilSenadoClient() as restsil:
        # Current year — always, all statuses.
        try:
            for row in restsil.iter_bills_desc(
                fecha_desde=current_year, fecha_hasta=current_year
            ):
                summary = BillParser.parse_restsil_summary(row)
                bn = summary["bulletin_number"]
                if bn and bn not in seen:
                    seen.add(bn)
                    bulletins.append(bn)
        except Exception:
            logger.exception("Failed restsil current-year scan year=%d", current_year)
            errors += 1

        # Past years — gated.
        if full_backfill and start_year < current_year:
            past_filters: dict[str, Any] = {
                "fecha_desde": start_year,
                "fecha_hasta": current_year - 1,
            }
            # Cold-start backfill: keep all statuses so we seed the full
            # history. Daily refresh: restrict to active bills.
            if _restsil_bills_has_cursor():
                past_filters["estado"] = "T"
            try:
                for row in restsil.iter_bills_desc(**past_filters):
                    summary = BillParser.parse_restsil_summary(row)
                    bn = summary["bulletin_number"]
                    if bn and bn not in seen:
                        seen.add(bn)
                        bulletins.append(bn)
                scanned_past_years = True
            except Exception:
                logger.exception(
                    "Failed restsil past-years scan %d-%d",
                    start_year,
                    current_year - 1,
                )
                errors += 1
    return bulletins, errors, scanned_past_years


def _restsil_bills_has_cursor() -> bool:
    """True iff a prior full-backfill cursor exists on the bills state row.

    Used by ``_discover_bulletins_restsil`` to distinguish cold start (no
    cursor → backfill all statuses) from steady-state daily refresh (cursor
    set → past-years scan restricts to ``estado=T``).
    """
    try:
        with task_session() as db:
            state = _get_state(db, "bills", create=False)
            return state is not None and bool(state.last_cursor)
    except Exception:
        logger.warning("Failed to read bills cursor", exc_info=True)
        return False


def _should_scan_past_years(now: datetime.date) -> bool:
    """Daily gate for the past-years restsil sweep.

    Past years' bill lists are nearly static; running the sweep on every
    5×/day tick is wasteful (see ``ingest_bills_optimizations.md`` §4). Run
    it at most once per day, tracked via ``IngestorState.last_cursor`` on the
    ``bills`` row (ISO date of last past-years sweep). When the cursor is
    absent (cold start), always sweep.
    """
    try:
        with task_session() as db:
            state = _get_state(db, "bills", create=False)
            if state is None or not state.last_cursor:
                return True
            try:
                last = datetime.date.fromisoformat(state.last_cursor)
            except ValueError:
                return True
            return last < now
    except Exception:
        logger.warning("Failed to read bills past-years cursor", exc_info=True)
        return True


def _mark_past_years_scanned(now: datetime.date) -> None:
    with task_session() as db:
        state = _get_state(db, "bills")
        if state is not None:
            state.last_cursor = now.isoformat()


def run_ingest_bills(
    bulletin: str | None = None,
    since: str | None = None,
    *,
    dry_run: bool = False,
    source: str | None = None,
) -> dict[str, Any]:
    dispatched = 0
    errors = 0
    bulletins: list[str] = []
    since_date: datetime.date | None = None
    mode = "single_bulletin" if bulletin else "full_scan"
    effective_source = source or settings.ingestor_bills_source
    scanned_past_years = False

    try:
        if since:
            since_date = datetime.date.fromisoformat(since)

        if bulletin:
            bulletins = [bulletin]
        else:
            if since_date is None:
                since_date = _get_last_sync_date("bills")

            current_year = datetime.date.today().year
            if since_date is not None:
                mode = "incremental"
                start_year = since_date.year
            else:
                start_year = settings.ingestor_bills_start_year

            if effective_source == "restsil":
                # Daily-gated past-years sweep; cold start (no cursor) → full
                # backfill.
                scan_past = _should_scan_past_years(datetime.date.today())
                bulletins, disco_errors, scanned_past_years = (
                    _discover_bulletins_restsil(
                        start_year, current_year, full_backfill=scan_past
                    )
                )
                errors += disco_errors
            else:
                bulletins, disco_errors = _discover_bulletins_opendata(
                    start_year, current_year
                )
                errors += disco_errors

        if bulletins:
            results = asyncio.run(fetch_bills_parallel(bulletins))
            valid_bulletins = [bn for bn, raw in results if raw is not None]
            opendata_details, detail_errors = _load_opendata_bill_details_with_votes(
                valid_bulletins
            )
            errors += detail_errors
            # Senate vote capture: when the dedicated restsil-driven
            # ``run_ingest_senate_votes`` task owns Senate votes (ADR-0013),
            # we no longer fetch them per-bulletin from votaciones.php on the
            # bill ingest path. The wspublico path remains as failover and is
            # activated by flipping ``ingestor_senate_votes_source``.
            if settings.ingestor_senate_votes_source == "wspublico":
                senate_votes = dict(asyncio.run(fetch_votes_parallel(valid_bulletins)))
            else:
                senate_votes = {}
            for bulletin_number, raw in results:
                try:
                    if raw is None:
                        continue
                    payload = BillParser.parse_bill(raw)
                    fetched_votes = senate_votes.get(bulletin_number)
                    if fetched_votes is not None:
                        payload["_votaciones"] = fetched_votes
                    elif settings.ingestor_senate_votes_source == "restsil":
                        # Drop the embedded ``<votacion>`` payload — the
                        # dedicated task owns them. Avoids creating stale
                        # rows under the legacy key shape on the bills path.
                        payload["_votaciones"] = []
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
        if scanned_past_years:
            _mark_past_years_scanned(datetime.date.today())

    return _build_dispatch_result(
        dispatched,
        errors,
        dry_run,
        bulletin=bulletin,
        since=since_date.isoformat() if since_date else None,
        mode=mode,
        candidates=len(bulletins),
        source=effective_source,
        scanned_past_years=scanned_past_years,
    )


# --------------------------------------------------------------------------
# Senate votes — dedicated restsil-driven ingest (ADR-0013)
# --------------------------------------------------------------------------


def _get_senate_votes_watermark() -> int | None:
    """Highest ``ID_VOTACION`` previously ingested, or ``None`` on cold start."""
    try:
        with task_session() as db:
            state = _get_state(db, "senate_votes", create=False)
            if state is None or not state.last_cursor:
                return None
            try:
                return int(state.last_cursor)
            except ValueError:
                logger.warning(
                    "senate_votes cursor is not an integer: %r", state.last_cursor
                )
                return None
    except Exception:
        logger.warning("Failed to read senate_votes watermark", exc_info=True)
        return None


def _set_senate_votes_watermark(new_max: int) -> None:
    with task_session() as db:
        state = _get_state(db, "senate_votes")
        if state is None:
            return
        existing = int(state.last_cursor) if state.last_cursor else 0
        if new_max > existing:
            state.last_cursor = str(new_max)
        state.last_sync_date = datetime.date.today()


def run_ingest_senate_votes(
    *,
    bulletin: str | None = None,
    dry_run: bool = False,
    source: str | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Restsil desc-paged Senate-vote ingest (ADR-0013).

    Walks ``buscarVotaciones?order=desc&sort=HORA`` and dispatches one
    ``sync_voting_session`` per row. Stops at the first row whose
    ``ID_VOTACION`` is at or below the stored watermark; updates the
    watermark to the new max at the end.

    With ``source="wspublico"`` (or the settings flag flipped to that
    value), the task no-ops and prints a hint — the failover path captures
    Senate votes on the bills ingest instead. There is no useful "scan all
    votes by date" wspublico endpoint, so flipping the source is what
    activates the failover.
    """
    effective_source = source or settings.ingestor_senate_votes_source
    if effective_source != "restsil":
        logger.info(
            "run_ingest_senate_votes is a no-op while source=%r — failover "
            "is via run_ingest_bills + fetch_votes_parallel.",
            effective_source,
        )
        return _build_dispatch_result(
            0, 0, dry_run, source=effective_source, mode="skip"
        )

    dispatched = 0
    errors = 0
    candidates = 0
    new_max: int = 0
    watermark = _get_senate_votes_watermark()
    mode = (
        "single_bulletin"
        if bulletin
        else ("cold_start" if watermark is None else "incremental")
    )

    try:
        with RestsilSenadoClient() as restsil:
            # Targeted ``--bulletin`` recovery must ignore the global
            # watermark: the operator is explicitly asking to re-fetch a
            # bulletin, and the relevant vote IDs are usually historical
            # (well below the current watermark). Without this, the desc
            # walk stops at the first row because every row is at or below
            # the watermark.
            iterator = restsil.iter_votes_desc(
                stop_at_id=None if bulletin else watermark,
                max_pages=max_pages,
                boletin=bulletin,
            )
            for row in iterator:
                candidates += 1
                try:
                    vote_id = int(row.get("ID_VOTACION") or 0)
                    if vote_id > new_max:
                        new_max = vote_id
                    payload = VoteParser.parse_restsil_senate_vote(row)
                    if not dry_run:
                        _dispatch(
                            sync_voting_session,
                            payload,
                            payload.get("bill_bulletin"),
                        )
                    dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to dispatch restsil senate vote id=%s",
                        row.get("ID_VOTACION"),
                    )
                    errors += 1
    except Exception:
        logger.exception("Failed restsil senate-votes ingest")
        errors += 1

    # Targeted single-bulletin runs are ops affordances; they should not
    # advance the global watermark even though the ID may be the latest.
    if not dry_run and bulletin is None and new_max > 0:
        _set_senate_votes_watermark(new_max)

    return _build_dispatch_result(
        dispatched,
        errors,
        dry_run,
        bulletin=bulletin,
        candidates=candidates,
        watermark_before=watermark,
        watermark_after=new_max if (new_max and not bulletin) else watermark,
        mode=mode,
        source=effective_source,
    )


# --------------------------------------------------------------------------
# Chamber votes — dedicated OpenData-bulk-driven ingest (ADR-0013)
# --------------------------------------------------------------------------


def _get_chamber_votes_watermark() -> int | None:
    """Highest chamber-vote ``<Id>`` previously ingested, or ``None`` on cold start."""
    try:
        with task_session() as db:
            state = _get_state(db, "chamber_votes", create=False)
            if state is None or not state.last_cursor:
                return None
            try:
                return int(state.last_cursor)
            except ValueError:
                logger.warning(
                    "chamber_votes cursor is not an integer: %r", state.last_cursor
                )
                return None
    except Exception:
        logger.warning("Failed to read chamber_votes watermark", exc_info=True)
        return None


def _set_chamber_votes_watermark(new_max: int) -> None:
    with task_session() as db:
        state = _get_state(db, "chamber_votes")
        if state is None:
            return
        existing = int(state.last_cursor) if state.last_cursor else 0
        if new_max > existing:
            state.last_cursor = str(new_max)
        state.last_sync_date = datetime.date.today()


def _bill_exists(bulletin: str) -> bool:
    from app.models.proyecto import Bill

    try:
        with task_session() as db:
            return (
                db.execute(
                    select(Bill.id).where(Bill.bulletin_number == bulletin)
                ).scalar_one_or_none()
                is not None
            )
    except Exception:
        logger.warning(
            "Failed to check bill existence for bulletin %s", bulletin, exc_info=True
        )
        return True  # fail open — avoid spurious sync_bill enqueues


def _collect_new_chamber_votes(
    years: list[int],
    watermark: int | None,
) -> tuple[list[dict[str, Any]], int]:
    """Walk the bulk year feed across ``years``; return ``(rows_above_watermark, errors)``.

    Rows are returned in encounter order (newest year first, upstream desc
    order within each year). Each row carries the parsed bulletin under
    ``_bulletin``; rows without a parseable bulletin are dropped here.
    """
    errors = 0
    collected: list[dict[str, Any]] = []
    with OpenDataCamaraClient() as opendata:
        for year in years:
            try:
                rows = opendata.get_votes_by_year(year)
            except Exception:
                logger.exception("Failed to fetch chamber votes for year %d", year)
                errors += 1
                continue
            for row in rows:
                vote_id = int(row.get("id") or 0)
                if not vote_id:
                    continue
                if watermark is not None and vote_id <= watermark:
                    continue
                bulletin = parse_bulletin_from_description(row.get("description"))
                if not bulletin:
                    # Non-bill chamber votes (Proyectos de Acuerdo, internal
                    # procedural votes) are out of scope — see ADR-0013.
                    continue
                row["_bulletin"] = bulletin
                collected.append(row)
    return collected, errors


def run_ingest_chamber_votes(
    *,
    year: int | None = None,
    bulletin: str | None = None,
    dry_run: bool = False,
    source: str | None = None,
    max_years: int | None = None,
) -> dict[str, Any]:
    """OpenData bulk Chamber-vote ingest (ADR-0013).

    Walks ``retornarVotacionesXAnno?prmAnno=YYYY``, parses the bulletin from
    each ``<Descripcion>``, enriches per-bulletin via
    ``retornarVotacionesXProyectoLey``, and dispatches one
    ``sync_voting_session`` per vote. Stops at the first row whose ``<Id>``
    is at or below the stored watermark; updates the watermark to the new
    max at the end.

    With ``source="bill_detail"`` (or the settings flag flipped to that
    value), the task no-ops — the failover path captures Chamber votes via
    the embedded ``<VotacionProyectoLey>`` loop in ``sync_bill``.

    Targeted runs (``--year`` or ``--bulletin``) do not advance the global
    watermark.
    """
    effective_source = source or settings.ingestor_chamber_votes_source
    if effective_source != "bulk":
        logger.info(
            "run_ingest_chamber_votes is a no-op while source=%r — failover "
            "is via sync_bill's embedded chamber-vote loop.",
            effective_source,
        )
        return _build_dispatch_result(
            0, 0, dry_run, source=effective_source, mode="skip"
        )

    today = datetime.date.today()
    current_year = today.year
    watermark = _get_chamber_votes_watermark()
    cap = max_years or settings.ingestor_chamber_votes_max_years_per_tick

    if bulletin is not None:
        years_to_scan: list[int] = []
        mode = "single_bulletin"
    elif year is not None:
        years_to_scan = [year]
        mode = "single_year"
    elif watermark is None:
        start_year = max(settings.ingestor_bills_start_year, current_year - cap + 1)
        years_to_scan = list(range(current_year, start_year - 1, -1))
        mode = "cold_start"
    else:
        years_to_scan = [current_year]
        mode = "incremental"

    dispatched = 0
    errors = 0
    candidates = 0
    new_max = 0
    bulletins_seen: set[str] = set()
    sync_bill_enqueued: set[str] = set()

    # Targeted single-bulletin recovery: skip discovery walk, go straight
    # to per-bulletin enrichment + per-vote detail.
    if bulletin is not None:
        rows_for_bulletin: list[dict[str, Any]] = []
        try:
            with OpenDataCamaraClient() as opendata:
                summaries = opendata.get_chamber_votes_for_bulletin(bulletin)
            for summary in summaries:
                vote_id = int(summary.get("id") or 0)
                if not vote_id:
                    continue
                summary["_bulletin"] = bulletin
                rows_for_bulletin.append(summary)
            candidates = len(rows_for_bulletin)
            rich_by_id: dict[int, dict[str, Any]] = {
                int(s["id"]): s for s in summaries if s.get("id")
            }
            vote_ids = [int(s["id"]) for s in rows_for_bulletin]
            details_by_id = _fetch_vote_details(vote_ids)
            for summary in rows_for_bulletin:
                try:
                    if _dispatch_chamber_vote(
                        summary,
                        rich_by_id,
                        details_by_id,
                        dry_run=dry_run,
                        bulletins_seen=bulletins_seen,
                        sync_bill_enqueued=sync_bill_enqueued,
                    ):
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to dispatch chamber vote id=%s (bulletin=%s)",
                        summary.get("id"),
                        bulletin,
                    )
                    errors += 1
        except Exception:
            logger.exception(
                "Failed chamber-votes targeted ingest for bulletin %s", bulletin
            )
            errors += 1

        return _build_dispatch_result(
            dispatched,
            errors,
            dry_run,
            bulletin=bulletin,
            candidates=candidates,
            mode=mode,
            source=effective_source,
        )

    # Discovery walk across years.
    try:
        new_rows, discovery_errors = _collect_new_chamber_votes(
            years_to_scan, watermark
        )
        errors += discovery_errors
        candidates = len(new_rows)

        if new_rows:
            new_max = max(int(r["id"]) for r in new_rows)

            distinct_bulletins = list({row["_bulletin"] for row in new_rows})
            rich_by_id = _fetch_rich_summaries(distinct_bulletins)

            vote_ids = [int(row["id"]) for row in new_rows]
            details_by_id = _fetch_vote_details(vote_ids)

            for row in new_rows:
                try:
                    if _dispatch_chamber_vote(
                        row,
                        rich_by_id,
                        details_by_id,
                        dry_run=dry_run,
                        bulletins_seen=bulletins_seen,
                        sync_bill_enqueued=sync_bill_enqueued,
                    ):
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to dispatch chamber vote id=%s", row.get("id")
                    )
                    errors += 1
    except Exception:
        logger.exception("Failed chamber-votes ingest")
        errors += 1

    # Targeted (year) and dry runs do not advance the watermark.
    advance_watermark = (
        not dry_run and bulletin is None and year is None and new_max > 0
    )
    if advance_watermark:
        _set_chamber_votes_watermark(new_max)

    return _build_dispatch_result(
        dispatched,
        errors,
        dry_run,
        bulletin=bulletin,
        candidates=candidates,
        watermark_before=watermark,
        watermark_after=new_max if advance_watermark else watermark,
        mode=mode,
        source=effective_source,
        years_scanned=years_to_scan,
        bulletins_enriched=len(bulletins_seen),
        sync_bill_enqueued=len(sync_bill_enqueued),
    )


def _fetch_rich_summaries(
    bulletins: list[str],
) -> dict[int, dict[str, Any]]:
    """Per-bulletin rich-summary fan-out → indexed by vote ``<Id>``."""
    rich_by_id: dict[int, dict[str, Any]] = {}
    if not bulletins:
        return rich_by_id
    pairs = asyncio.run(
        fetch_chamber_vote_summaries_parallel(
            bulletins,
            max_concurrency=settings.ingestor_opendata_async_concurrency,
        )
    )
    for _bulletin, rows in pairs:
        for row in rows:
            vote_id = row.get("id")
            if vote_id:
                rich_by_id[int(vote_id)] = row
    return rich_by_id


def _fetch_vote_details(
    vote_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Per-vote per-deputy detail fan-out → indexed by vote ``<Id>``."""
    if not vote_ids:
        return {}
    pairs = asyncio.run(
        fetch_voting_details_parallel(
            vote_ids,
            max_concurrency=settings.ingestor_opendata_async_concurrency,
        )
    )
    return {voting_id: detail for voting_id, detail in pairs if detail is not None}


def _dispatch_chamber_vote(
    summary: dict[str, Any],
    rich_by_id: dict[int, dict[str, Any]],
    details_by_id: dict[int, dict[str, Any]],
    *,
    dry_run: bool,
    bulletins_seen: set[str],
    sync_bill_enqueued: set[str],
) -> bool:
    """Merge bulk + rich + per-deputy data → ``sync_voting_session`` dispatch.

    Returns True iff a dispatch happened (or would happen in dry-run).
    """
    bulletin = summary["_bulletin"]
    bulletins_seen.add(bulletin)
    vote_id = int(summary["id"])

    # Bulk summary is the base; rich summary overlays voting_type/article_text/
    # tramites; vote detail overlays individual_votes + counts. The vote_detail
    # totals are authoritative when present.
    merged: dict[str, Any] = {**summary}
    rich = rich_by_id.get(vote_id)
    if rich is not None:
        merged.update(rich)
    detail = details_by_id.get(vote_id)
    if detail is not None:
        merged.update(detail)

    payload = VoteParser.parse_chamber_vote(merged, bulletin=bulletin)

    if not dry_run:
        if bulletin not in sync_bill_enqueued and not _bill_exists(bulletin):
            sync_bill_enqueued.add(bulletin)
            try:
                _trigger_targeted_bill_ingest(bulletin)
            except Exception:
                logger.exception(
                    "Failed to enqueue bill ingest for orphan bulletin %s",
                    bulletin,
                )
        _dispatch(sync_voting_session, payload, bulletin)
    return True


def _trigger_targeted_bill_ingest(bulletin: str) -> None:
    """Kick a targeted ``ingest_bills`` Celery task for an orphan bulletin.

    The bill ingest fetches Senado detail + OpenData enrichment for the
    bulletin and ultimately calls ``sync_bill`` with the right shape.
    Once the bill row lands, ``upsert_bill``'s reconcile step relinks
    the previously-orphaned ``VotingSession`` rows (ADR-0013).
    """
    # Local import avoids a circular dependency at module load.
    from app.tasks.ingestors import ingest_bills as _ingest_bills_task

    _ingest_bills_task.delay(bulletin=bulletin)


def run_ingest_legislators(*, dry_run: bool = False) -> dict[str, Any]:
    """Refresh the legislator roster + chamber-sourced metadata.

    Per ADR-0012, the BCN REST endpoint ``ObtenerParlamentariosActivos`` is
    the active-roster authority for both chambers. The pipeline shape:

    1. **BCN REST** drives the active roster for both chambers in one call.
    2. **Deputies** are enriched with OpenData (gender + ``_militancias``
       party history) joined by ``IdEnCamaraDeOrigen`` ↔ OpenData ``Id``.
    3. **Senators** are enriched with the senado.cl metadata catalog (gender,
       phone, photo, slug-derived profile URL) joined by
       ``IdEnCamaraDeOrigen`` ↔ ``ID_PARLAMENTARIO``.
    4. **BCN REST enrichment** (``bcn_uri`` + ``bcn_wiki_url``) is dispatched
       for every active legislator — no dependency on BCN SPARQL.

    BCN SPARQL biographic enrichment (profession, twitter, photo) and
    ``ParliamentaryAppointment`` history are no longer part of this flow —
    BCN SPARQL has been chronically returning 502s and was delaying every
    run. Use ``python -m app.cli ingestors bcn-sparql-enrichment`` to run
    those passes separately when SPARQL is healthy.
    """
    dispatched = 0
    errors = 0

    # 1. Historical deputy roster — OpenData ``retornarDiputados`` returns
    #    every person ever seated as a deputy with full militancia history.
    #    Each row becomes a person seed with one term per militancia carrying
    #    ``camara:{Id}``. See ADR-0015.
    try:
        with OpenDataCamaraClient() as opendata:
            historical_deputies = opendata.get_all_diputados()
    except Exception:
        logger.exception(
            "Failed to fetch historical deputies from OpenData; aborting "
            "legislator ingest"
        )
        errors += 1
        return _build_dispatch_result(dispatched, errors, dry_run)

    for raw in historical_deputies:
        try:
            payload = LegislatorParser.parse_opendata_deputy(raw)
            if payload is None:
                continue
            if not dry_run:
                _dispatch(sync_legislator, payload)
            dispatched += 1
        except Exception:
            logger.exception("Failed to dispatch historical deputy %s", raw.get("id"))
            errors += 1

    time.sleep(REQUEST_DELAY)

    # 2. Historical senator roster — senado.cl ``hemicycle?limit=1000`` (no
    #    vigentes filter). Returns every senator with their PERIODOS chamber
    #    history. Stub records with PERIODOS == [] are dropped client-side.
    try:
        with SenadoWebClient() as senado_web:
            historical_senators = senado_web.get_historical_catalog()
    except Exception:
        logger.exception("Failed to fetch historical senators from SenadoWebClient")
        errors += 1
        historical_senators = []

    for raw in historical_senators:
        try:
            payload = LegislatorParser.parse_senator(raw)
            if payload is None:
                continue
            if not dry_run:
                _dispatch(sync_legislator, payload)
            dispatched += 1
        except Exception:
            logger.exception(
                "Failed to dispatch historical senator %s", raw.get("ID_PARLAMENTARIO")
            )
            errors += 1

    # 3. BCN REST enrichment — bcn_uri + bcn_wiki_url, keyed by chamber
    #    bridge. The BCN active-roster pass is no longer the identity source;
    #    it only stamps cross-chamber identity onto whichever Legislator now
    #    holds the matching chamber bridge.
    bcn_rest_rows: list[dict[str, Any]] = []
    try:
        with BCNRestClient() as bcn_rest:
            bcn_rest_rows = bcn_rest.get_active_parliamentarians()
    except Exception:
        logger.exception("Failed to fetch active roster from BCN REST")
        errors += 1

    for raw in bcn_rest_rows:
        try:
            enrichment = LegislatorParser.parse_bcn_rest_enrichment(raw)
            if enrichment is None:
                continue
            bridge = enrichment.pop("chamber_external_id")
            cleaned = {k: v for k, v in enrichment.items() if v}
            if not cleaned:
                continue
            if not dry_run:
                _dispatch(sync_legislator_bcn_enrichment, bridge, cleaned)
            dispatched += 1
        except Exception:
            logger.exception("Failed to dispatch BCN REST enrichment")
            errors += 1

    if not dry_run:
        _mark_synced("legislators")

    return _build_dispatch_result(dispatched, errors, dry_run)


def run_ingest_bcn_sparql_enrichment(*, dry_run: bool = False) -> dict[str, Any]:
    """Run BCN SPARQL-backed enrichment passes against the active roster.

    Split out of ``run_ingest_legislators`` because BCN SPARQL has been
    chronically returning 502s and the per-URI fan-out (with its retry
    budget) was delaying every legislator ingest. This function is meant to
    run only when SPARQL is healthy — typically out-of-band from the main
    legislator ingest.

    Two passes against the same URI list (fetched from BCN REST, which is
    fast and reliable):

    1. ``fetch_person_profiles_parallel`` → ``sync_legislator_bcn_enrichment``
       with profession, twitter, gender, BCN-sourced photo.
    2. ``fetch_person_appointments_parallel`` →
       ``sync_parliamentary_appointment`` with term history rows keyed by
       BCN ``PositionPeriod`` URI.

    Both passes are wrapped in try/except so a SPARQL outage degrades to a
    no-op rather than killing the function.
    """
    dispatched = 0
    errors = 0

    bcn_rest_rows: list[dict[str, Any]] = []
    try:
        with BCNRestClient() as bcn_rest:
            bcn_rest_rows = bcn_rest.get_active_parliamentarians()
    except Exception:
        logger.exception(
            "Failed to fetch active roster from BCN REST; aborting SPARQL enrichment"
        )
        errors += 1
        return _build_dispatch_result(dispatched, errors, dry_run)

    sparql_uris = [raw.get("bcn_uri") for raw in bcn_rest_rows if raw.get("bcn_uri")]

    # Profile enrichment pass — keyed by ``bcn_uri`` (the SPARQL person URI
    # is the cross-chamber identity; matches Legislator.bcn_uri directly).
    profiles: dict[str, dict[str, Any] | None] = {}
    try:
        profiles = asyncio.run(fetch_person_profiles_parallel(sparql_uris))
    except Exception as exc:
        logger.warning(
            "BCN SPARQL profile enrichment unavailable (continuing): %s", exc
        )

    for bcn_uri, profile in profiles.items():
        if profile is None:
            continue
        try:
            payload = LegislatorParser.parse_bcn_profile(profile)
            cleaned = {k: v for k, v in payload.items() if v}
            if not cleaned:
                continue
            if not dry_run:
                _dispatch(sync_legislator_bcn_enrichment, bcn_uri, cleaned)
            dispatched += 1
        except Exception:
            logger.exception("Failed to dispatch BCN SPARQL enrichment for %s", bcn_uri)
            errors += 1

    # Appointment-history backfill pass — also keyed by ``bcn_uri``. The
    # appointment writes to ``LegislatorTerm.bcn_appointment_uri`` on whichever
    # existing chamber+start term matches, or opens a new one.
    appointments_by_uri: dict[str, list[dict[str, Any]]] = {}
    try:
        appointments_by_uri = asyncio.run(
            fetch_person_appointments_parallel(sparql_uris)
        )
    except Exception as exc:
        logger.warning(
            "BCN SPARQL appointment history unavailable (continuing): %s", exc
        )

    for bcn_uri, appointments in appointments_by_uri.items():
        for appointment in appointments:
            try:
                term_payload = LegislatorParser.parse_bcn_appointment(appointment)
                if term_payload is None:
                    continue
                term_payload["chamber_type"] = term_payload["chamber_type"].value
                if not dry_run:
                    _dispatch(
                        sync_parliamentary_appointment,
                        bcn_uri,
                        term_payload,
                    )
                dispatched += 1
            except Exception:
                logger.exception("Failed to dispatch appointment for %s", bcn_uri)
                errors += 1

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
                            _dispatch(sync_legislature, parsed)
                        dispatched += 1
                except Exception:
                    logger.exception(
                        "Failed to parse legislature from OpenDataCamaraClient"
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
                            _dispatch(sync_legislature, parsed)
                        dispatched += 1
                except Exception:
                    logger.exception("Failed to parse legislature from CamaraClient")
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


@app.task(name="app.tasks.ingestors.ingest_senate_votes", bind=True, base=DatabaseTask)
def ingest_senate_votes(self, bulletin: str | None = None) -> dict:
    return run_ingest_senate_votes(bulletin=bulletin)


@app.task(name="app.tasks.ingestors.ingest_chamber_votes", bind=True, base=DatabaseTask)
def ingest_chamber_votes(
    self,
    year: int | None = None,
    bulletin: str | None = None,
) -> dict:
    return run_ingest_chamber_votes(year=year, bulletin=bulletin)


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

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from importlib import import_module
from typing import Any, Callable, TypeVar

R = TypeVar("R")

LOGGER = logging.getLogger(__name__)
SCRAPER_ENGINES = ("playwright", "camoufox", "patchright")


def _load_attr(module_name: str, attr_name: str) -> Any:
    module = import_module(module_name)
    return getattr(module, attr_name)


def _list_jobs(_: argparse.Namespace) -> dict[str, list[str]]:
    return {
        "scrapers": ["diario-oficial", "cgr-reglamentos", "diputados"],
        "ingestors": [
            "bills",
            "legislators",
            "committees",
            "legislature",
            "reference-data",
            "voting-sessions",
        ],
        "loaders": ["geography"],
        "voting-signals": ["backfill", "refresh-aggregate", "seed-fixtures"],
    }


def _run_diario_oficial(args: argparse.Namespace) -> dict[str, Any]:
    run_scrape = _load_attr("app.scrapers.diario_oficial", "run_scrape")
    result = run_scrape(
        date.fromisoformat(args.target_date) if args.target_date else date.today(),
        engine=args.engine,
        headed=args.headed,
        dry_run=args.dry_run,
    )
    return {"job": "diario-oficial", **result}


def _run_cgr_reglamentos(args: argparse.Namespace) -> dict[str, Any]:
    run_scrape = _load_attr("app.scrapers.cgr_reglamentos", "run_scrape")
    result = run_scrape(engine=args.engine, headed=args.headed, dry_run=args.dry_run)
    return {"job": "cgr-reglamentos", **result}


def _run_diputados(args: argparse.Namespace) -> dict[str, Any]:
    run_scrape = _load_attr("app.scrapers.camara_diputados", "run_scrape")
    result = run_scrape(engine=args.engine, headed=args.headed, dry_run=args.dry_run)
    return {"job": "diputados", **result}


def _run_bills(args: argparse.Namespace) -> dict[str, Any]:
    run_ingest_bills = _load_attr("app.tasks.ingestors", "run_ingest_bills")
    result = run_ingest_bills(
        bulletin=args.bulletin,
        since=args.since,
        dry_run=args.dry_run,
    )
    return {"job": "bills", **result}


def _run_legislators(args: argparse.Namespace) -> dict[str, Any]:
    run_ingest_legislators = _load_attr("app.tasks.ingestors", "run_ingest_legislators")
    result = run_ingest_legislators(dry_run=args.dry_run)
    return {"job": "legislators", **result}


def _run_committees(args: argparse.Namespace) -> dict[str, Any]:
    run_ingest_committees = _load_attr("app.tasks.ingestors", "run_ingest_committees")
    result = run_ingest_committees(dry_run=args.dry_run)
    return {"job": "committees", **result}


def _run_legislature(args: argparse.Namespace) -> dict[str, Any]:
    run_ingest_legislature = _load_attr("app.tasks.ingestors", "run_ingest_legislature")
    result = run_ingest_legislature(dry_run=args.dry_run)
    return {"job": "legislature", **result}


def _run_reference_data(args: argparse.Namespace) -> dict[str, Any]:
    run_ingest_reference_data = _load_attr(
        "app.tasks.ingestors", "run_ingest_reference_data"
    )
    result = run_ingest_reference_data(dry_run=args.dry_run)
    return {"job": "reference-data", **result}


def _run_geography(args: argparse.Namespace) -> dict[str, Any]:
    run_load_geography = _load_attr("app.geography.loader", "run_load_geography")
    result = run_load_geography(
        dataset_path=args.dataset,
        dry_run=args.dry_run,
    )
    return {"job": "geography", **result}


def _run_voting_sessions(args: argparse.Namespace) -> dict[str, Any]:
    run_ingest_voting_sessions = _load_attr(
        "app.tasks.ingestors", "run_ingest_voting_sessions"
    )
    result = run_ingest_voting_sessions(since=args.since, dry_run=args.dry_run)
    return {"job": "voting-sessions", **result}


def _with_session(fn: Callable[[Any], R]) -> R:
    """Open a SQLAlchemy session, run ``fn(db)``, commit, return its result."""
    SessionLocal = _load_attr("app.core.database", "SessionLocal")
    db = SessionLocal()
    try:
        result = fn(db)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _run_voting_signals_backfill(args: argparse.Namespace) -> dict[str, Any]:
    backfill_signals = _load_attr("app.services.voting_signals", "backfill_signals")
    since = date.fromisoformat(args.since) if args.since else None
    result = _with_session(lambda db: backfill_signals(db, since=since))
    return {"job": "voting-signals-backfill", **result}


def _run_voting_signals_refresh_aggregate(args: argparse.Namespace) -> dict[str, Any]:
    refresh = _load_attr("app.services.voting_signals", "refresh_window_aggregate")
    payload = _with_session(
        lambda db: dict(refresh(db, window_days=args.window_days).payload)
    )
    return {
        "job": "voting-signals-refresh-aggregate",
        "window_days": args.window_days,
        "payload": payload,
    }


def _run_voting_signals_seed_fixtures(args: argparse.Namespace) -> dict[str, Any]:
    seed = _load_attr("app.services.voting_signals", "seed_signal_fixtures")
    base = date.fromisoformat(args.base_date) if args.base_date else None
    result = _with_session(lambda db: seed(db, base_date=base))
    return {"job": "voting-signals-seed-fixtures", **result}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Run scrapers, ingestors, and manual data loaders from the backend workspace.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="Logging level for CLI execution.",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    list_parser = subparsers.add_parser(
        "list", help="List available scrapers, ingestors, and loaders."
    )
    list_parser.set_defaults(runner=_list_jobs)

    dry_run_parent = argparse.ArgumentParser(add_help=False)
    dry_run_parent.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse data without enqueuing downstream sync tasks or updating sync state.",
    )

    scraper_common_parent = argparse.ArgumentParser(add_help=False)
    scraper_common_parent.add_argument(
        "--engine",
        default="playwright",
        choices=SCRAPER_ENGINES,
        help="Browser engine used by scraper commands.",
    )
    scraper_common_parent.add_argument(
        "--headed",
        action="store_true",
        help="Run browser-based scrapers with a visible browser window.",
    )

    scraper_parsers = subparsers.add_parser("scrapers", help="Run scraper jobs.")
    scraper_subparsers = scraper_parsers.add_subparsers(dest="scraper")
    scraper_subparsers.required = True

    diario_parser = scraper_subparsers.add_parser(
        "diario-oficial",
        parents=[dry_run_parent, scraper_common_parent],
        help="Run the Diario Oficial scraper for a single date.",
    )
    diario_parser.add_argument(
        "--target-date",
        help="Target date in ISO format. Defaults to today.",
    )
    diario_parser.set_defaults(runner=_run_diario_oficial)

    cgr_parser = scraper_subparsers.add_parser(
        "cgr-reglamentos",
        parents=[dry_run_parent, scraper_common_parent],
        help="Run the CGR reglamentos scraper.",
    )
    cgr_parser.set_defaults(runner=_run_cgr_reglamentos)

    diputados_parser = scraper_subparsers.add_parser(
        "diputados",
        parents=[dry_run_parent, scraper_common_parent],
        help="Scrape camara.cl to enrich deputies with district + photo.",
    )
    diputados_parser.set_defaults(runner=_run_diputados)

    ingestor_parsers = subparsers.add_parser("ingestors", help="Run ingestor jobs.")
    ingestor_subparsers = ingestor_parsers.add_subparsers(dest="ingestor")
    ingestor_subparsers.required = True

    bills_parser = ingestor_subparsers.add_parser(
        "bills",
        parents=[dry_run_parent],
        help="Fetch and enqueue bill sync jobs.",
    )
    bills_parser.add_argument(
        "--bulletin", help="Fetch a single bill bulletin instead of querying all years."
    )
    bills_parser.add_argument(
        "--since",
        help="Only fetch bill bulletins modified since this ISO date.",
    )
    bills_parser.set_defaults(runner=_run_bills)

    legislators_parser = ingestor_subparsers.add_parser(
        "legislators",
        parents=[dry_run_parent],
        help="Fetch and enqueue legislator sync jobs.",
    )
    legislators_parser.set_defaults(runner=_run_legislators)

    committees_parser = ingestor_subparsers.add_parser(
        "committees",
        parents=[dry_run_parent],
        help="Fetch and enqueue committee sync jobs.",
    )
    committees_parser.set_defaults(runner=_run_committees)

    legislature_parser = ingestor_subparsers.add_parser(
        "legislature",
        parents=[dry_run_parent],
        help="Fetch and enqueue legislature sync jobs.",
    )
    legislature_parser.set_defaults(runner=_run_legislature)

    reference_data_parser = ingestor_subparsers.add_parser(
        "reference-data",
        parents=[dry_run_parent],
        help="Fetch and enqueue topic reference-data sync jobs.",
    )
    reference_data_parser.set_defaults(runner=_run_reference_data)

    geography_parser = subparsers.add_parser(
        "geography",
        parents=[dry_run_parent],
        help="Load the checked-in geography baseline synchronously.",
    )
    geography_parser.add_argument(
        "--dataset",
        help=(
            "Optional path to a geography dataset JSON file. "
            "Defaults to the checked-in baseline."
        ),
    )
    geography_parser.set_defaults(runner=_run_geography)

    voting_parser = ingestor_subparsers.add_parser(
        "voting-sessions",
        parents=[dry_run_parent],
        help="Fetch and enqueue voting session sync jobs.",
    )
    voting_parser.add_argument(
        "--since", help="Only fetch voting sessions since this ISO date."
    )
    voting_parser.set_defaults(runner=_run_voting_sessions)

    signals_parser = subparsers.add_parser(
        "voting-signals",
        help="Compute behavior-revealing signals for /votaciones.",
    )
    signals_subparsers = signals_parser.add_subparsers(dest="signals_command")
    signals_subparsers.required = True

    backfill_parser = signals_subparsers.add_parser(
        "backfill",
        help="Recompute signals for historical voting sessions.",
    )
    backfill_parser.add_argument(
        "--since",
        help="ISO date floor; if omitted, all sessions are scanned.",
    )
    backfill_parser.set_defaults(runner=_run_voting_signals_backfill)

    refresh_agg_parser = signals_subparsers.add_parser(
        "refresh-aggregate",
        help="Refresh the rolling-window aggregates row.",
    )
    refresh_agg_parser.add_argument(
        "--window-days", type=int, default=30, help="Window size in days."
    )
    refresh_agg_parser.set_defaults(runner=_run_voting_signals_refresh_aggregate)

    seed_parser = signals_subparsers.add_parser(
        "seed-fixtures",
        help="Insert hand-crafted sessions that fire each signal type (local dev).",
    )
    seed_parser.add_argument(
        "--base-date",
        help="Anchor date for the fixture sessions (defaults to yesterday).",
    )
    seed_parser.set_defaults(runner=_run_voting_signals_seed_fixtures)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    runner: Callable[[argparse.Namespace], dict[str, Any]] = args.runner
    try:
        result = runner(args)
    except KeyboardInterrupt:
        LOGGER.error("Interrupted")
        return 130
    except Exception:
        LOGGER.exception("CLI command failed")
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("errors"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

from celery.schedules import crontab  # type: ignore[import-untyped]

from app.core.celery_app import app

app.conf.beat_schedule = {
    "scrape-diario-oficial": {
        "task": "app.tasks.scrapers.scrape_diario_oficial",
        "schedule": crontab(hour=11, minute=0),
    },
    "scrape-cgr-reglamentos": {
        "task": "app.tasks.scrapers.scrape_cgr_reglamentos",
        "schedule": crontab(hour="6,12,18", minute=0),
    },
    "ingest-bills": {
        "task": "app.tasks.ingestors.ingest_bills",
        "schedule": crontab(hour="5,9,13,17,21", minute=0),
    },
    "ingest-senate-votes": {
        # Same 5×/day cadence as bills. The watermarked desc-walk is cheap
        # (one paged restsil call when nothing new) so cadence is mostly a
        # freshness knob — see ADR-0013.
        "task": "app.tasks.ingestors.ingest_senate_votes",
        "schedule": crontab(hour="5,9,13,17,21", minute=15),
    },
    "ingest-chamber-votes": {
        # OpenData bulk-year-feed driven, watermarked by `<Id>`. Offset 30
        # minutes from bills/senate-votes within each wave so the upstream
        # isn't hit by three tasks at once. See ADR-0013.
        "task": "app.tasks.ingestors.ingest_chamber_votes",
        "schedule": crontab(hour="5,9,13,17,21", minute=30),
    },
    "ingest-legislators": {
        "task": "app.tasks.ingestors.ingest_legislators",
        "schedule": crontab(hour=3, minute=0),
    },
    "ingest-committees": {
        "task": "app.tasks.ingestors.ingest_committees",
        "schedule": crontab(hour=3, minute=0),
    },
    "ingest-legislature": {
        "task": "app.tasks.ingestors.ingest_legislature",
        "schedule": crontab(hour=3, minute=0),
    },
    "ingest-reference-data": {
        "task": "app.tasks.ingestors.ingest_reference_data",
        "schedule": crontab(hour=3, minute=30),
    },
    "refresh-voting-window-aggregate": {
        "task": "app.tasks.voting.refresh_voting_window_aggregate",
        "schedule": crontab(hour=4, minute=0),
        "kwargs": {"window_days": 30},
    },
    "refresh-legislator-voting-stats": {
        "task": "app.tasks.voting.refresh_legislator_voting_stats",
        "schedule": crontab(hour=4, minute=20),
    },
    "alert-orphan-votes": {
        # Daily SLA check on vote rows that resolved with legislator_id=NULL.
        # See ADR-0015 — orphans get claimed by _reconcile_orphan_votes once
        # the matching LegislatorTerm arrives; this surfaces ones that don't.
        "task": "app.tasks.legislators.alert_orphan_votes",
        "schedule": crontab(hour=5, minute=45),
    },
}

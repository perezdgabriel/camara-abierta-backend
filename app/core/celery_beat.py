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
    "ingest-voting-sessions": {
        "task": "app.tasks.ingestors.ingest_voting_sessions",
        "schedule": crontab(hour=3, minute=15),
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
}

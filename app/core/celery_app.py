from celery import Celery  # type: ignore[import-untyped]

from app.core.config import settings

app = Celery("camara_abierta")

app.conf.update(
    broker_url=settings.redis_url,
    result_backend=settings.redis_url,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Santiago",
    enable_utc=False,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    worker_enable_remote_control=False,
    task_default_queue="default",
    imports=(
        "app.tasks.scrapers",
        "app.tasks.ingestors",
        "app.tasks.normas",
        "app.tasks.reglamentos",
        "app.tasks.bills",
        "app.tasks.legislators",
        "app.tasks.committees",
        "app.tasks.voting",
        "app.tasks.legislature",
        "app.tasks.reference",
    ),
)
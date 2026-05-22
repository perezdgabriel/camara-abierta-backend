from celery import Task  # type: ignore[import-untyped]


class DatabaseTask(Task):
    abstract = True
    autoretry_for = (Exception,)
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 3

"""Jobs handler: EventBridge Scheduler fires with `{"task": "<name>"}` and we run
the Celery-free implementation in-process (DISPATCH_BACKEND=serverless). Mirrors
the beat entries in app/core/celery_beat.py, minus the descoped scrapers.

Three shapes of entrypoint:
- Ingestors expose plain `run_*` functions (the bodies the Celery tasks wrap).
- A few maintenance tasks have no `run_*` equivalent, so we execute the Celery
  task eagerly via `task.apply().get()` (same body, in-process).
- Tabla Semanal ingestion is triggered by an S3 `ObjectCreated` event (a human
  uploads the weekly agenda PDF) rather than a schedule; the event carries
  `Records` instead of `task`, so it's dispatched from a dedicated branch that
  downloads the object and calls `run_ingest_tabla_semanal` directly (see
  ADR-0017 §8).

After a job succeeds we fire a best-effort cache-revalidation ping to the
frontend for the tags it touched (never fails the job).
"""

import importlib
from typing import Any

from app.services.revalidate import revalidate

# task name -> (module, attribute). Plain Celery-free functions, called with no
# args (defaults).
_RUN_FUNCS: dict[str, tuple[str, str]] = {
    "ingest_bills": ("app.tasks.ingestors", "run_ingest_bills"),
    "ingest_senate_votes": ("app.tasks.ingestors", "run_ingest_senate_votes"),
    "ingest_chamber_votes": ("app.tasks.ingestors", "run_ingest_chamber_votes"),
    "ingest_legislators": ("app.tasks.ingestors", "run_ingest_legislators"),
    "ingest_committees": ("app.tasks.ingestors", "run_ingest_committees"),
    "ingest_legislature": ("app.tasks.ingestors", "run_ingest_legislature"),
}

# task name -> (module, attribute). Celery tasks with no run_* wrapper; executed
# eagerly in-process.
_CELERY_TASKS: dict[str, tuple[str, str]] = {
    "refresh_voting_window_aggregate": (
        "app.tasks.voting",
        "refresh_voting_window_aggregate",
    ),
    "refresh_legislator_voting_stats": (
        "app.tasks.voting",
        "refresh_legislator_voting_stats",
    ),
    "alert_orphan_votes": ("app.tasks.legislators", "alert_orphan_votes"),
}

# task name -> frontend cache tags to expire after a successful run (see the
# frontend contract in docs/deploy/backend-agent-plan.md).
_REVAL_TAGS: dict[str, list[str]] = {
    "ingest_bills": ["bills", "dashboard"],
    "ingest_senate_votes": ["votes", "dashboard"],
    "ingest_chamber_votes": ["votes", "dashboard"],
    "ingest_legislators": ["legislators"],
    "ingest_committees": ["reference"],
    "ingest_legislature": ["reference"],
    "refresh_voting_window_aggregate": ["dashboard"],
    "refresh_legislator_voting_stats": ["dashboard", "legislators"],
}


# Frontend cache tags to expire after a Tabla Semanal ingest. Reuses "dashboard"
# (already expired by several other ingestors) rather than inventing an
# unconfirmed "calendar" tag.
_TABLA_SEMANAL_REVAL_TAGS: list[str] = ["dashboard"]


def _resolve(module_name: str, attr: str) -> Any:
    return getattr(importlib.import_module(module_name), attr)


def _is_s3_event(event: dict[str, Any]) -> bool:
    records = event.get("Records") or []
    return bool(records) and all(r.get("eventSource") == "aws:s3" for r in records)


def _run_tabla_semanal_from_s3(record: dict[str, Any]) -> dict[str, Any]:
    import os
    import urllib.parse

    import boto3

    from app.tasks.ingestors import run_ingest_tabla_semanal

    bucket = record["s3"]["bucket"]["name"]
    # S3 event keys are URL-encoded (e.g. spaces -> "+"); decode before use.
    key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
    tmp_path = os.path.join("/tmp", os.path.basename(key))

    boto3.client("s3").download_file(bucket, key, tmp_path)
    return run_ingest_tabla_semanal(pdf_path=tmp_path, dry_run=False)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if _is_s3_event(event):
        results = [_run_tabla_semanal_from_s3(r) for r in event["Records"]]
        revalidate(_TABLA_SEMANAL_REVAL_TAGS)
        return {"task": "ingest_tabla_semanal", "result": results}

    task = event.get("task")
    if task in _RUN_FUNCS:
        result = _resolve(*_RUN_FUNCS[task])()
    elif task in _CELERY_TASKS:
        result = _resolve(*_CELERY_TASKS[task]).apply().get()
    else:
        raise ValueError(f"Unknown task: {task!r}")

    revalidate(_REVAL_TAGS.get(task, []))
    return {"task": task, "result": result}

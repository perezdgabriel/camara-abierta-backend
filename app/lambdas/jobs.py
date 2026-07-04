"""Jobs handler: EventBridge Scheduler fires with `{"task": "<name>"}` and we run
the Celery-free implementation in-process (DISPATCH_BACKEND=serverless). Mirrors
the beat entries in app/core/celery_beat.py, minus the descoped scrapers.

Two shapes of entrypoint:
- Ingestors expose plain `run_*` functions (the bodies the Celery tasks wrap).
- A few maintenance tasks have no `run_*` equivalent, so we execute the Celery
  task eagerly via `task.apply().get()` (same body, in-process).

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


def _resolve(module_name: str, attr: str) -> Any:
    return getattr(importlib.import_module(module_name), attr)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    task = event.get("task")
    if task in _RUN_FUNCS:
        result = _resolve(*_RUN_FUNCS[task])()
    elif task in _CELERY_TASKS:
        result = _resolve(*_CELERY_TASKS[task]).apply().get()
    else:
        raise ValueError(f"Unknown task: {task!r}")

    revalidate(_REVAL_TAGS.get(task, []))
    return {"task": task, "result": result}

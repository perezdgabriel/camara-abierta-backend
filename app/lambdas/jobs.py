"""Jobs handler: EventBridge Scheduler fires with `{"task": "<name>"}` and we
call the plain (non-Celery) implementation. Mirrors app/core/celery_beat.py.

The ingestors' `run_*` functions are the Celery-free implementations the tasks
wrap (e.g. run_ingest_bills at app/tasks/ingestors.py). Confirm the exact
callable names for the voting/legislators/committees entries before relying on
them — a couple are marked TODO.
"""

import importlib
from typing import Any

# task name -> (module, callable). Called with no args (defaults) unless noted.
_DISPATCH: dict[str, tuple[str, str]] = {
    "ingest_bills": ("app.tasks.ingestors", "run_ingest_bills"),
    "ingest_senate_votes": ("app.tasks.ingestors", "run_ingest_senate_votes"),
    "ingest_chamber_votes": ("app.tasks.ingestors", "run_ingest_chamber_votes"),
    # TODO: confirm these plain-function names exist / match:
    "ingest_legislators": ("app.tasks.ingestors", "run_ingest_legislators"),
    "ingest_committees": ("app.tasks.ingestors", "run_ingest_committees"),
    "ingest_legislature": ("app.tasks.ingestors", "run_ingest_legislature"),
    "refresh_voting_window_aggregate": ("app.tasks.voting", "run_refresh_voting_window_aggregate"),
    "refresh_legislator_voting_stats": ("app.tasks.voting", "run_refresh_legislator_voting_stats"),
    "alert_orphan_votes": ("app.tasks.legislators", "run_alert_orphan_votes"),
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    task = event.get("task")
    if task not in _DISPATCH:
        raise ValueError(f"Unknown task: {task!r}")
    module_name, func_name = _DISPATCH[task]
    func = getattr(importlib.import_module(module_name), func_name)
    result = func()
    return {"task": task, "result": result}

"""Task dispatch seam — the one place that decides *how* a Celery task runs.

Local/dev keeps the real broker (``task.delay``). The deployed job Lambda has no
worker, so ``serverless`` mode runs task bodies **inline** (``task.apply().get()``
— eager, in-process, reusing the same body + ``task_session``) except for LLM
summaries, which cross an SQS boundary to the concurrency-capped LLM Lambda
(ADR-0022). Task *bodies* are unchanged; only dispatch differs.

LLM tasks are identified by an explicit allowlist rather than the Celery
``queue`` attribute: ``generate_bill_summary_layer`` does not declare
``queue="llm"`` (only the descoped ``process_norma`` does), so a ``.queue``
check would route the wrong tasks.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import settings

# Task names whose work must go to the SQS -> LLM Lambda path in serverless mode.
_LLM_TASKS: frozenset[str] = frozenset({"app.tasks.bills.generate_bill_summary_layer"})


def dispatch(task: Any, *args: Any, **kwargs: Any) -> Any:
    """Route a Celery task through the configured backend.

    - ``celery``: hand off to the broker (``task.delay``).
    - ``serverless``: LLM tasks -> SQS; everything else runs inline and its
      result is returned (exceptions propagate).
    """
    if settings.dispatch_backend == "celery":
        return task.delay(*args, **kwargs)

    if task.name in _LLM_TASKS:
        return _send_to_llm_queue(task.name, args, kwargs)

    return task.apply(args=list(args), kwargs=kwargs).get()


def _send_to_llm_queue(
    task_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    if not settings.llm_queue_url:
        raise RuntimeError(
            "LLM_QUEUE_URL is not set but a serverless LLM task was dispatched"
        )
    import boto3  # lazy: only needed in the deployed path

    body = {"task": task_name, "args": list(args), "kwargs": kwargs}
    boto3.client("sqs").send_message(
        QueueUrl=settings.llm_queue_url,
        MessageBody=json.dumps(body),
    )

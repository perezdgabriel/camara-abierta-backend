"""LLM handler: SQS-driven task execution (the `-c 1` llm worker analog).

Reserved concurrency (set in infra/compute_stack.py) caps parallel Anthropic
calls; batch_size=1 means one message per invocation. The message is the generic
dispatch envelope produced by app/core/dispatch.py:

    {"task": "<celery task name>", "args": [...], "kwargs": {...}}

We look the task up in the Celery registry and run its body eagerly in-process
(`task.apply(...).get()`), which re-raises on failure — that returns the message
to the queue, and after max_receive_count (3) it lands in the DLQ, which the
CloudWatch alarm watches.
"""

import json
import logging
from typing import Any

from app.core.celery_app import app as celery_app

# Lambda's default root logger level is WARNING; the timing breadcrumbs in
# app/services/pdf.py are logger.info, so raise it here to actually see them
# in CloudWatch.
logging.getLogger().setLevel(logging.INFO)

# Populate the task registry (celery_app.conf.imports) so lookups by name work
# without a running worker. Done once at cold start.
celery_app.loader.import_default_modules()


def handler(event: dict[str, Any], context: Any) -> None:
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        task = celery_app.tasks[body["task"]]
        task.apply(args=body.get("args", []), kwargs=body.get("kwargs", {})).get()

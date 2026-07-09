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
import time
from typing import Any

from app.core.celery_app import app as celery_app

logger = logging.getLogger(__name__)

# Lambda's default root logger level is WARNING; the timing breadcrumbs in
# app/services/pdf.py are logger.info, so raise it here to actually see them
# in CloudWatch.
logging.getLogger().setLevel(logging.INFO)

# Populate the task registry (celery_app.conf.imports) so lookups by name work
# without a running worker. Done once at cold start.
celery_app.loader.import_default_modules()


def _debug_network_check() -> None:
    """Temporary diagnostic: raw DNS/TCP/TLS/HTTP check against Anthropic's API.

    Bill 214's proposal-layer job hangs inside the Anthropic SDK call with no
    exception ever raised and no request ever landing in the Anthropic
    console — this isolates whether it's DNS, TCP connect, TLS, or the HTTP
    request itself that's stalling from this Lambda's VPC egress path.
    """
    import socket

    import httpx

    host = "api.anthropic.com"

    t0 = time.monotonic()
    try:
        addrs = socket.getaddrinfo(host, 443)
        logger.info(
            "DNS resolved %s in %.2fs: %s", host, time.monotonic() - t0, addrs[:1]
        )
    except Exception as exc:
        logger.warning("DNS FAILED after %.2fs: %s", time.monotonic() - t0, exc)
        return

    t0 = time.monotonic()
    try:
        sock = socket.create_connection((host, 443), timeout=10)
        sock.close()
        logger.info("TCP connect to %s:443 in %.2fs", host, time.monotonic() - t0)
    except Exception as exc:
        logger.warning("TCP connect FAILED after %.2fs: %s", time.monotonic() - t0, exc)
        return

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(f"https://{host}/")
            logger.info(
                "HTTPS GET %s -> %s in %.2fs",
                host,
                r.status_code,
                time.monotonic() - t0,
            )
    except Exception as exc:
        logger.warning("HTTPS GET FAILED after %.2fs: %s", time.monotonic() - t0, exc)


def handler(event: dict[str, Any], context: Any) -> None:
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        if body.get("task") == "__debug_network_check__":
            _debug_network_check()
            continue
        task = celery_app.tasks[body["task"]]
        task.apply(args=body.get("args", []), kwargs=body.get("kwargs", {})).get()

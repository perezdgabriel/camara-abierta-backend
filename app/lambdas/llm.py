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


def _debug_anthropic_call(chars: int = 20) -> None:
    """Temporary diagnostic: a real call through the actual SDK client with a
    configurable-size synthetic prompt, to bisect whether the bill 214 hang
    (150K chars) is a genuine size threshold or specific to that content.

    Raw DNS/TCP/HTTPS to the domain all work fine (see _debug_network_check),
    and MSS clamping on the NAT (deployed 2026-07-09) did NOT fix the bill 214
    hang, so this isolates size as a variable directly, using the exact same
    client construction as production (_claude_client).
    """
    from app.core.config import settings
    from app.services.llm import _claude_client

    content = "Cuenta hasta diez. " + ("x" * chars)
    t0 = time.monotonic()
    try:
        client = _claude_client()
        logger.info("Anthropic client constructed in %.2fs", time.monotonic() - t0)
        t0 = time.monotonic()
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=10,
            messages=[{"role": "user", "content": content}],
        )
        logger.info(
            "messages.create() (chars=%d) succeeded in %.2fs: %s",
            chars,
            time.monotonic() - t0,
            response.content,
        )
    except Exception as exc:
        logger.warning(
            "messages.create() (chars=%d) FAILED after %.2fs: %s: %s",
            chars,
            time.monotonic() - t0,
            type(exc).__name__,
            exc,
        )


def _debug_tool_call(chars: int = 20) -> None:
    """Temporary diagnostic: the exact production tool-call shape (forced
    tool_choice + max_tokens=2048), but with a small/configurable prompt.

    Bisection ruled out prompt size entirely: a synthetic 150K-char prompt
    with a bare messages.create() (max_tokens=10) succeeds in <1s, but bill
    214's real 150K-char prompt through the production _claude_tool_call path
    hangs. The one thing not yet isolated is the tools/tool_choice/max_tokens
    shape itself — a forced structured-JSON generation may take Claude
    genuinely much longer to produce than a bare 10-token completion, and if
    that longer in-flight duration is what the NAT can't sustain (rather than
    payload size), a small prompt through the *real* call shape should
    reproduce the hang.
    """
    from app.core.config import settings
    from app.services.llm import PROPOSAL_TOOL, _claude_client

    content = "Resume esto: " + ("x" * chars)
    t0 = time.monotonic()
    try:
        client = _claude_client()
        logger.info("Anthropic client constructed in %.2fs", time.monotonic() - t0)
        t0 = time.monotonic()
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            temperature=0.2,
            tools=[PROPOSAL_TOOL],
            tool_choice={"type": "tool", "name": PROPOSAL_TOOL["name"]},
            messages=[{"role": "user", "content": content}],
        )
        logger.info(
            "tool call (chars=%d) succeeded in %.2fs: %s",
            chars,
            time.monotonic() - t0,
            response.content,
        )
    except Exception as exc:
        logger.warning(
            "tool call (chars=%d) FAILED after %.2fs: %s: %s",
            chars,
            time.monotonic() - t0,
            type(exc).__name__,
            exc,
        )


def handler(event: dict[str, Any], context: Any) -> None:
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        if body.get("task") == "__debug_network_check__":
            _debug_network_check()
            continue
        if body.get("task") == "__debug_anthropic_call__":
            _debug_anthropic_call(**body.get("kwargs", {}))
            continue
        if body.get("task") == "__debug_tool_call__":
            _debug_tool_call(**body.get("kwargs", {}))
            continue
        task = celery_app.tasks[body["task"]]
        task.apply(args=body.get("args", []), kwargs=body.get("kwargs", {})).get()

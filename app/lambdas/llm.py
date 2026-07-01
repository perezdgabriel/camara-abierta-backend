"""LLM handler: SQS-driven bill summarization (the `-c 1` llm worker analog).

Reserved concurrency (set in infra/compute_stack.py) caps parallel Anthropic
calls; batch_size=1 means one bill per invocation. Raising on failure returns the
message to the queue, and after max_receive_count (3) it lands in the DLQ, which
the CloudWatch alarm watches.

Wire `_summarize_bill` to the real summary path (app/services/llm.py — the same
call `ai bills regenerate` uses).
"""

import json
from typing import Any


def _summarize_bill(bill_id: int) -> None:
    # TODO: call the existing PROPOSAL AI-summary path (app/services/llm.py),
    # e.g. within a task_session(). Kept as a stub to avoid guessing the API.
    raise NotImplementedError


def handler(event: dict[str, Any], context: Any) -> None:
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        _summarize_bill(int(body["bill_id"]))

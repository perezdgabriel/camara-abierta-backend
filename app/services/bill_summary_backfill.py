from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dispatch import dispatch
from app.models.enums import BillSummaryKind
from app.models.proyecto import Bill, BillSummary

ALL_KINDS = (BillSummaryKind.PROPOSAL, BillSummaryKind.AMENDMENTS)


def _resolve_kinds(kind: str) -> tuple[BillSummaryKind, ...]:
    if kind == "all":
        return ALL_KINDS
    return (BillSummaryKind(kind),)


def regenerate_bill_summaries(
    db: Session,
    *,
    bulletin: str | None,
    kind: str,
    stale_only: bool,
) -> dict[str, Any]:
    """Enqueue summary regeneration tasks for matching bills.

    Idempotent — the worker honors the same hash/version gate that lives
    sync does, so re-running with the same flags is safe. Honors the global
    ``AI_SUMMARY_ENABLED`` gate. See ADR-0019.
    """
    if not settings.ai_summary_enabled:
        return {
            "bills_scanned": 0,
            "tasks_enqueued": 0,
            "enqueued": [],
            "disabled": True,
        }

    from app.tasks.bills import generate_bill_summary_layer

    kinds = _resolve_kinds(kind)
    prompt_version = settings.ai_summary_prompt_version
    model_name = settings.anthropic_model

    bill_query = select(Bill.id, Bill.bulletin_number)
    if bulletin:
        bill_query = bill_query.where(Bill.bulletin_number == bulletin)
    bills = list(db.execute(bill_query).all())

    enqueued: list[dict[str, Any]] = []
    for bill_id, bulletin_number in bills:
        for kind_enum in kinds:
            if stale_only:
                summary = db.execute(
                    select(BillSummary).where(
                        BillSummary.bill_id == bill_id,
                        BillSummary.kind == kind_enum,
                    )
                ).scalar_one_or_none()
                if summary is not None and (
                    summary.prompt_version == prompt_version
                    and summary.model_name == model_name
                ):
                    continue
            dispatch(generate_bill_summary_layer, bill_id, kind_enum.value)
            enqueued.append(
                {
                    "bulletin": bulletin_number,
                    "bill_id": bill_id,
                    "kind": kind_enum.value,
                }
            )

    return {
        "bills_scanned": len(bills),
        "tasks_enqueued": len(enqueued),
        "enqueued": enqueued,
    }

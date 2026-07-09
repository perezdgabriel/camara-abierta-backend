import hashlib
import logging
import time
from typing import Any

from app.core.celery_app import app
from app.core.config import settings
from app.core.dispatch import dispatch
from app.core.session import task_session
from app.ingestors.parsers.votes import VoteParser
from app.models.core import Topic
from app.models.enums import BillSummaryKind, BillSummaryStatus
from app.services.llm import (
    can_generate_bill_summary,
    generate_amendments_summary,
    generate_proposal_summary,
)
from app.services.notifications import send_alerta_proyecto
from app.services.pdf import extract_comparado_text_from_url, extract_text_from_url
from app.services.proyectos import get_bill
from app.services.write import (
    apply_bill_topic_classification,
    get_bill_summary,
    upsert_bill,
    upsert_bill_summary,
)
from app.tasks.base import DatabaseTask
from app.tasks.voting import sync_voting_session

logger = logging.getLogger(__name__)


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _hash_url(url: str | None) -> str | None:
    if not url:
        return None
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


_AMENDMENTS_SEPARATOR_LEN = len("\n\n---\n\n")


def _joined_len(texts: list[str]) -> int:
    if not texts:
        return 0
    return sum(len(t) for t in texts) + _AMENDMENTS_SEPARATOR_LEN * (len(texts) - 1)


def _truncate_to_budget(texts: list[str], budget: int) -> bool:
    """Shrink ``texts`` in place so the joined length fits in ``budget``.

    Drops whole trailing comparados first; if the first comparado alone
    still exceeds the budget, char-truncates it. Returns ``True`` if any
    truncation happened.
    """
    if _joined_len(texts) <= budget:
        return False
    while len(texts) > 1 and _joined_len(texts) > budget:
        texts.pop()
    if texts and len(texts[0]) > budget:
        texts[0] = texts[0][:budget]
    return True


def _layer_is_stale(
    summary,
    *,
    prompt_version: str,
    model_name: str,
) -> bool:
    """A row whose prompt/model no longer matches current config is stale.

    Drives self-healing regeneration on prompt/model upgrades — see ADR-0019.
    """
    if summary is None:
        return True
    return summary.prompt_version != prompt_version or summary.model_name != model_name


def _decide_summary_triggers(
    db, *, bill_id: int, change_info: dict[str, Any]
) -> list[BillSummaryKind]:
    """Translate the bill change_info into the layers that need regeneration.

    See ADR-0019 §triggers. Returns the kinds to enqueue; empty list = no-op.
    Honors the global ``AI_SUMMARY_ENABLED`` gate (default off) so a fresh
    ingest does not burn LLM budget.
    """
    if not settings.ai_summary_enabled:
        return []
    prompt_version = settings.ai_summary_prompt_version
    model_name = settings.anthropic_model
    kinds: list[BillSummaryKind] = []

    proposal = get_bill_summary(db, bill_id=bill_id, kind=BillSummaryKind.PROPOSAL)
    proposal_stale = _layer_is_stale(
        proposal, prompt_version=prompt_version, model_name=model_name
    )
    if (
        proposal_stale
        or change_info.get("is_new")
        or change_info.get("full_text_url_changed")
    ):
        kinds.append(BillSummaryKind.PROPOSAL)

    amendments = get_bill_summary(db, bill_id=bill_id, kind=BillSummaryKind.AMENDMENTS)
    amendments_stale = _layer_is_stale(
        amendments, prompt_version=prompt_version, model_name=model_name
    )
    if amendments_stale or change_info.get("new_comparado_added"):
        kinds.append(BillSummaryKind.AMENDMENTS)

    return kinds


@app.task(name="app.tasks.bills.sync_bill", bind=True, base=DatabaseTask)
def sync_bill(self, data: dict) -> dict:
    with task_session() as db:
        bill, change_info = upsert_bill(db, data)
        bill_id = bill.id
        summary_kinds = _decide_summary_triggers(
            db, bill_id=bill_id, change_info=change_info
        )

    for kind in summary_kinds:
        dispatch(generate_bill_summary_layer, bill_id, kind.value)

    for raw_vote in data.get("_votaciones", []):
        dispatch(
            sync_voting_session,
            VoteParser.parse_senate_vote(raw_vote, bulletin=data["bulletin_number"]),
            data["bulletin_number"],
        )

    # ADR-0013: the dedicated chamber-votes task owns this dispatch in the
    # default ``bulk`` configuration. The embedded loop here is the failover
    # path, activated via ``INGESTOR_CHAMBER_VOTES_SOURCE=bill_detail``.
    if settings.ingestor_chamber_votes_source == "bill_detail":
        for raw_vote in data.get("_camara_votaciones", []):
            if not raw_vote.get("id"):
                continue
            dispatch(
                sync_voting_session,
                VoteParser.parse_chamber_vote(
                    raw_vote, bulletin=data["bulletin_number"]
                ),
                data["bulletin_number"],
            )

    bulletin_number = data["bulletin_number"]
    title = data.get("title") or ""

    if change_info["is_new"]:
        send_alerta_proyecto(
            bulletin_number=bulletin_number,
            title=title,
            change_type="new",
            extra={
                "entry_date": str(data.get("entry_date") or ""),
                "origin": _enum_value(
                    data.get("origin_type") or data.get("origin") or ""
                ),
            },
        )

    if change_info["status_changed"]:
        send_alerta_proyecto(
            bulletin_number=bulletin_number,
            title=title,
            change_type="status_changed",
            extra={
                "old_status": _enum_value(change_info.get("old_status") or ""),
                "new_status": _enum_value(change_info.get("new_status") or ""),
            },
        )

    if change_info["stage_changed"]:
        send_alerta_proyecto(
            bulletin_number=bulletin_number,
            title=title,
            change_type="stage_changed",
        )

    return {"bill_id": bill_id, "status": "ok"}


@app.task(
    name="app.tasks.bills.generate_bill_summary_layer",
    bind=True,
    base=DatabaseTask,
)
def generate_bill_summary_layer(self, bill_id: int, kind: str) -> dict:
    """Generate one summary layer for a bill and upsert the result.

    Persists ``SKIPPED`` / ``FAILED`` rows so callers can distinguish
    never-tried from tried-and-failed. See ADR-0019.
    """
    try:
        kind_enum = BillSummaryKind(kind)
    except ValueError:
        return {"bill_id": bill_id, "kind": kind, "status": "invalid_kind"}

    if not can_generate_bill_summary():
        return {"bill_id": bill_id, "kind": kind, "status": "llm_unavailable"}

    if kind_enum is BillSummaryKind.PROPOSAL:
        return _generate_proposal_layer(bill_id)
    if kind_enum is BillSummaryKind.AMENDMENTS:
        return _generate_amendments_layer(bill_id)
    return {"bill_id": bill_id, "kind": kind, "status": "unsupported_kind"}


def _persist_layer(
    bill_id: int,
    *,
    kind: BillSummaryKind,
    status: BillSummaryStatus,
    content: dict[str, Any] | None,
    source_url: str | None,
    error_reason: str | None,
    truncated: bool = False,
) -> str:
    with task_session() as db:
        summary = upsert_bill_summary(
            db,
            bill_id=bill_id,
            kind=kind,
            status=status,
            content=content,
            prompt_version=settings.ai_summary_prompt_version,
            model_name=settings.anthropic_model,
            source_url=source_url,
            source_url_hash=_hash_url(source_url),
            error_reason=error_reason,
            truncated=truncated,
        )
    return "missing" if summary is None else status.value


def _generate_proposal_layer(bill_id: int) -> dict:
    with task_session() as db:
        bill = get_bill(db, bill_id)
        if bill is None:
            return {
                "bill_id": bill_id,
                "kind": BillSummaryKind.PROPOSAL.value,
                "status": "missing",
            }
        full_text_url = bill.full_text_url

    if not full_text_url:
        status = _persist_layer(
            bill_id,
            kind=BillSummaryKind.PROPOSAL,
            status=BillSummaryStatus.SKIPPED,
            content=None,
            source_url=None,
            error_reason="no_full_text_url",
        )
        return {
            "bill_id": bill_id,
            "kind": BillSummaryKind.PROPOSAL.value,
            "status": status,
        }

    full_text = extract_text_from_url(full_text_url)
    if not full_text:
        status = _persist_layer(
            bill_id,
            kind=BillSummaryKind.PROPOSAL,
            status=BillSummaryStatus.SKIPPED,
            content=None,
            source_url=full_text_url,
            error_reason="pdf_extraction_failed",
        )
        return {
            "bill_id": bill_id,
            "kind": BillSummaryKind.PROPOSAL.value,
            "status": status,
        }

    logger.info("bill %s: querying existing topics", bill_id)
    t0 = time.monotonic()
    with task_session() as db:
        existing_topics = [name for (name,) in db.query(Topic.name).all()]
    logger.info(
        "bill %s: got %d topics in %.1fs",
        bill_id,
        len(existing_topics),
        time.monotonic() - t0,
    )

    texts = [full_text]
    truncated = _truncate_to_budget(texts, settings.ai_summary_max_input_chars)
    full_text = texts[0]

    logger.info(
        "bill %s: calling Claude (truncated=%s, chars=%d)",
        bill_id,
        truncated,
        len(full_text),
    )
    t0 = time.monotonic()
    try:
        content = generate_proposal_summary(
            full_text, existing_topics, truncated=truncated
        )
        logger.info(
            "bill %s: Claude call finished in %.1fs", bill_id, time.monotonic() - t0
        )
    except Exception as exc:
        logger.warning(
            "bill %s: Claude call raised after %.1fs: %s",
            bill_id,
            time.monotonic() - t0,
            exc,
        )
        logger.warning("Claude proposal summary failed for bill %s: %s", bill_id, exc)
        status = _persist_layer(
            bill_id,
            kind=BillSummaryKind.PROPOSAL,
            status=BillSummaryStatus.FAILED,
            content=None,
            source_url=full_text_url,
            error_reason=f"{type(exc).__name__}: {exc}",
        )
        return {
            "bill_id": bill_id,
            "kind": BillSummaryKind.PROPOSAL.value,
            "status": status,
        }

    status = _persist_layer(
        bill_id,
        kind=BillSummaryKind.PROPOSAL,
        status=BillSummaryStatus.SUCCESS,
        content=content,
        source_url=full_text_url,
        error_reason=None,
        truncated=truncated,
    )

    # Claude's strict tool-use schema can't express minItems/maxItems on
    # arrays, so the 1-3 count from ADR-0021 is enforced here rather than
    # structurally.
    topics = (content.get("topics") or [])[:3]
    if topics:
        with task_session() as db:
            bill = get_bill(db, bill_id)
            if bill is not None:
                apply_bill_topic_classification(db, bill, topics)

    return {
        "bill_id": bill_id,
        "kind": BillSummaryKind.PROPOSAL.value,
        "status": status,
    }


def _generate_amendments_layer(bill_id: int) -> dict:
    with task_session() as db:
        bill = get_bill(db, bill_id)
        if bill is None:
            return {
                "bill_id": bill_id,
                "kind": BillSummaryKind.AMENDMENTS.value,
                "status": "missing",
            }
        comparado_urls = [
            doc.document_url
            for doc in bill.documents
            if doc.document_type == "comparison" and doc.document_url
        ]

    if not comparado_urls:
        status = _persist_layer(
            bill_id,
            kind=BillSummaryKind.AMENDMENTS,
            status=BillSummaryStatus.SKIPPED,
            content=None,
            source_url=None,
            error_reason="no_comparados",
        )
        return {
            "bill_id": bill_id,
            "kind": BillSummaryKind.AMENDMENTS.value,
            "status": status,
        }

    comparado_texts: list[str] = []
    for url in comparado_urls:
        text = extract_comparado_text_from_url(url)
        if text:
            comparado_texts.append(text)

    if not comparado_texts:
        status = _persist_layer(
            bill_id,
            kind=BillSummaryKind.AMENDMENTS,
            status=BillSummaryStatus.SKIPPED,
            content=None,
            source_url=comparado_urls[0],
            error_reason="pdf_extraction_failed",
        )
        return {
            "bill_id": bill_id,
            "kind": BillSummaryKind.AMENDMENTS.value,
            "status": status,
        }

    truncated = _truncate_to_budget(
        comparado_texts, settings.ai_summary_max_input_chars
    )

    try:
        content = generate_amendments_summary(comparado_texts, truncated=truncated)
    except Exception as exc:
        logger.warning("Claude amendments summary failed for bill %s: %s", bill_id, exc)
        status = _persist_layer(
            bill_id,
            kind=BillSummaryKind.AMENDMENTS,
            status=BillSummaryStatus.FAILED,
            content=None,
            source_url=comparado_urls[0],
            error_reason=f"{type(exc).__name__}: {exc}",
        )
        return {
            "bill_id": bill_id,
            "kind": BillSummaryKind.AMENDMENTS.value,
            "status": status,
        }

    status = _persist_layer(
        bill_id,
        kind=BillSummaryKind.AMENDMENTS,
        status=BillSummaryStatus.SUCCESS,
        content=content,
        source_url=comparado_urls[0],
        error_reason=None,
        truncated=truncated,
    )
    return {
        "bill_id": bill_id,
        "kind": BillSummaryKind.AMENDMENTS.value,
        "status": status,
    }

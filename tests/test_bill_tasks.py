from contextlib import contextmanager
from types import SimpleNamespace

from app.models.enums import BillOrigin, BillStatus, BillSummaryKind, BillSummaryStatus
from app.tasks import bills as bill_tasks


def ns(**kwargs):
    return SimpleNamespace(**kwargs)


class FakeTopicDb:
    """Fake session supporting the ``db.query(Topic.name).all()`` lookup."""

    def __init__(self, names=()):
        self._names = [(n,) for n in names]

    def query(self, *_args):
        return self

    def all(self):
        return self._names


def session_sequence(*dbs):
    queue = list(dbs)

    @contextmanager
    def _task_session():
        assert queue, "unexpected task_session() call"
        yield queue.pop(0)

    return _task_session


def test_generate_proposal_layer_returns_llm_unavailable_when_no_backend(monkeypatch):
    monkeypatch.setattr(bill_tasks, "can_generate_bill_summary", lambda: False)

    result = bill_tasks.generate_bill_summary_layer.run(99, "proposal")

    assert result == {
        "bill_id": 99,
        "kind": "proposal",
        "status": "llm_unavailable",
    }


def test_generate_proposal_layer_persists_success(monkeypatch):
    first_db = object()
    topics_db = FakeTopicDb(["Trabajo", "Salud"])
    second_db = object()
    apply_db = object()
    upserts: list[dict] = []
    applied: list[tuple] = []

    monkeypatch.setattr(bill_tasks, "can_generate_bill_summary", lambda: True)
    monkeypatch.setattr(
        bill_tasks,
        "task_session",
        session_sequence(first_db, topics_db, second_db, apply_db),
    )

    def fake_get_bill(db, bill_id):
        assert bill_id == 7
        return ns(full_text_url="https://example.com/bill.pdf")

    def fake_extract(url):
        assert url == "https://example.com/bill.pdf"
        return "texto completo"

    def fake_generate_proposal(text, existing_topics):
        assert text == "texto completo"
        assert existing_topics == ["Trabajo", "Salud"]
        return {
            "propose": "Cosa",
            "affected_groups": ["A"],
            "why_it_matters": "Importa",
            "key_objections": [],
            "topics": ["Trabajo"],
        }

    def fake_upsert(db, **kwargs):
        assert db is second_db
        upserts.append(kwargs)
        return ns(id=1)

    def fake_apply(db, bill, topic_names):
        assert db is apply_db
        applied.append(topic_names)

    monkeypatch.setattr(bill_tasks, "get_bill", fake_get_bill)
    monkeypatch.setattr(bill_tasks, "extract_text_from_url", fake_extract)
    monkeypatch.setattr(bill_tasks, "generate_proposal_summary", fake_generate_proposal)
    monkeypatch.setattr(bill_tasks, "upsert_bill_summary", fake_upsert)
    monkeypatch.setattr(bill_tasks, "apply_bill_topic_classification", fake_apply)

    result = bill_tasks.generate_bill_summary_layer.run(7, "proposal")

    assert result == {"bill_id": 7, "kind": "proposal", "status": "success"}
    assert len(upserts) == 1
    payload = upserts[0]
    assert payload["bill_id"] == 7
    assert payload["kind"] is BillSummaryKind.PROPOSAL
    assert payload["status"] is BillSummaryStatus.SUCCESS
    assert payload["content"]["propose"] == "Cosa"
    assert payload["source_url"] == "https://example.com/bill.pdf"
    assert payload["source_url_hash"] is not None
    assert applied == [["Trabajo"]]


def test_generate_proposal_layer_persists_skipped_when_no_full_text_url(monkeypatch):
    db = object()
    upserts: list[dict] = []

    monkeypatch.setattr(bill_tasks, "can_generate_bill_summary", lambda: True)
    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(db, db))
    monkeypatch.setattr(
        bill_tasks, "get_bill", lambda _db, _bid: ns(full_text_url=None)
    )
    monkeypatch.setattr(
        bill_tasks,
        "upsert_bill_summary",
        lambda _db, **kwargs: upserts.append(kwargs) or ns(id=1),
    )

    result = bill_tasks.generate_bill_summary_layer.run(7, "proposal")

    assert result["status"] == "skipped"
    assert upserts[0]["status"] is BillSummaryStatus.SKIPPED
    assert upserts[0]["error_reason"] == "no_full_text_url"


def test_generate_proposal_layer_persists_failed_when_llm_raises(monkeypatch):
    upserts: list[dict] = []

    monkeypatch.setattr(bill_tasks, "can_generate_bill_summary", lambda: True)
    monkeypatch.setattr(
        bill_tasks,
        "task_session",
        session_sequence(object(), FakeTopicDb(), object()),
    )
    monkeypatch.setattr(
        bill_tasks,
        "get_bill",
        lambda _db, _bid: ns(full_text_url="https://example.com/x.pdf"),
    )
    monkeypatch.setattr(bill_tasks, "extract_text_from_url", lambda _url: "texto")

    def raise_(_text, _existing_topics):
        raise RuntimeError("boom")

    monkeypatch.setattr(bill_tasks, "generate_proposal_summary", raise_)
    monkeypatch.setattr(
        bill_tasks,
        "upsert_bill_summary",
        lambda _db, **kwargs: upserts.append(kwargs) or ns(id=1),
    )

    result = bill_tasks.generate_bill_summary_layer.run(7, "proposal")

    assert result["status"] == "failed"
    assert upserts[0]["status"] is BillSummaryStatus.FAILED
    assert "RuntimeError: boom" in upserts[0]["error_reason"]


def test_sync_bill_enqueues_proposal_layer_on_new_bill(monkeypatch):
    db = object()
    queued: list[tuple[int, str]] = []
    notifications: list[dict] = []

    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(db))
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_enabled", True)

    def fake_upsert_bill(_db, data):
        return ns(id=42), {
            "is_new": True,
            "status_changed": False,
            "stage_changed": False,
            "full_text_url_changed": False,
            "new_comparado_added": False,
            "old_status": None,
            "new_status": BillStatus.PENDING,
        }

    monkeypatch.setattr(bill_tasks, "upsert_bill", fake_upsert_bill)
    # Existing rows: none — proposal must enqueue. Amendments has no trigger
    # signal, but a missing row still counts as stale, so it also enqueues.
    monkeypatch.setattr(
        bill_tasks, "get_bill_summary", lambda _db, *, bill_id, kind: None
    )
    monkeypatch.setattr(
        bill_tasks.generate_bill_summary_layer,
        "delay",
        lambda bill_id, kind: queued.append((bill_id, kind)),
    )
    monkeypatch.setattr(
        bill_tasks.sync_voting_session, "delay", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(
        bill_tasks,
        "send_alerta_proyecto",
        lambda **kwargs: notifications.append(kwargs),
    )

    result = bill_tasks.sync_bill.run(
        {
            "bulletin_number": "555-06",
            "title": "Proyecto demo",
            "entry_date": "2026-05-10",
            "origin_type": BillOrigin.EXECUTIVE,
            "_votaciones": [],
        }
    )

    assert result == {"bill_id": 42, "status": "ok"}
    assert (42, "proposal") in queued
    assert (42, "amendments") in queued
    assert notifications[0]["change_type"] == "new"


def test_sync_bill_skips_layers_when_nothing_changed(monkeypatch):
    db = object()
    queued: list[tuple[int, str]] = []

    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(db))
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_enabled", True)
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_prompt_version", "v2")
    monkeypatch.setattr(bill_tasks.settings, "anthropic_model", "claude-haiku-4-5")

    def fake_upsert_bill(_db, _data):
        return ns(id=42), {
            "is_new": False,
            "status_changed": False,
            "stage_changed": False,
            "full_text_url_changed": False,
            "new_comparado_added": False,
            "old_status": BillStatus.PENDING,
            "new_status": BillStatus.PENDING,
        }

    fresh_row = ns(prompt_version="v2", model_name="claude-haiku-4-5")

    monkeypatch.setattr(bill_tasks, "upsert_bill", fake_upsert_bill)
    monkeypatch.setattr(
        bill_tasks,
        "get_bill_summary",
        lambda _db, *, bill_id, kind: fresh_row,
    )
    monkeypatch.setattr(
        bill_tasks.generate_bill_summary_layer,
        "delay",
        lambda bill_id, kind: queued.append((bill_id, kind)),
    )
    monkeypatch.setattr(
        bill_tasks.sync_voting_session, "delay", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(bill_tasks, "send_alerta_proyecto", lambda **_kw: None)

    bill_tasks.sync_bill.run(
        {
            "bulletin_number": "555-06",
            "title": "Proyecto demo",
            "entry_date": "2026-05-10",
            "origin_type": BillOrigin.EXECUTIVE,
            "_votaciones": [],
        }
    )

    assert queued == []


def test_sync_bill_regenerates_proposal_on_status_change(monkeypatch):
    db = object()
    queued: list[tuple[int, str]] = []

    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(db))
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_enabled", True)
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_prompt_version", "v2")
    monkeypatch.setattr(bill_tasks.settings, "anthropic_model", "claude-haiku-4-5")

    def fake_upsert_bill(_db, _data):
        return ns(id=42), {
            "is_new": False,
            "status_changed": True,
            "stage_changed": False,
            "full_text_url_changed": False,
            "new_comparado_added": False,
            "old_status": BillStatus.PENDING,
            "new_status": BillStatus.APPROVED,
        }

    fresh_row = ns(prompt_version="v2", model_name="claude-haiku-4-5")

    monkeypatch.setattr(bill_tasks, "upsert_bill", fake_upsert_bill)
    monkeypatch.setattr(
        bill_tasks,
        "get_bill_summary",
        lambda _db, *, bill_id, kind: fresh_row,
    )
    monkeypatch.setattr(
        bill_tasks.generate_bill_summary_layer,
        "delay",
        lambda bill_id, kind: queued.append((bill_id, kind)),
    )
    monkeypatch.setattr(
        bill_tasks.sync_voting_session, "delay", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(bill_tasks, "send_alerta_proyecto", lambda **_kw: None)

    bill_tasks.sync_bill.run(
        {
            "bulletin_number": "555-06",
            "title": "Proyecto demo",
            "entry_date": "2026-05-10",
            "origin_type": BillOrigin.EXECUTIVE,
            "_votaciones": [],
        }
    )

    assert (42, "proposal") in queued
    assert (42, "amendments") not in queued


def test_sync_bill_regenerates_amendments_on_new_comparado(monkeypatch):
    db = object()
    queued: list[tuple[int, str]] = []

    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(db))
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_enabled", True)
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_prompt_version", "v2")
    monkeypatch.setattr(bill_tasks.settings, "anthropic_model", "claude-haiku-4-5")

    def fake_upsert_bill(_db, _data):
        return ns(id=42), {
            "is_new": False,
            "status_changed": False,
            "stage_changed": False,
            "full_text_url_changed": False,
            "new_comparado_added": True,
            "old_status": BillStatus.PENDING,
            "new_status": BillStatus.PENDING,
        }

    fresh_row = ns(prompt_version="v2", model_name="claude-haiku-4-5")

    monkeypatch.setattr(bill_tasks, "upsert_bill", fake_upsert_bill)
    monkeypatch.setattr(
        bill_tasks,
        "get_bill_summary",
        lambda _db, *, bill_id, kind: fresh_row,
    )
    monkeypatch.setattr(
        bill_tasks.generate_bill_summary_layer,
        "delay",
        lambda bill_id, kind: queued.append((bill_id, kind)),
    )
    monkeypatch.setattr(
        bill_tasks.sync_voting_session, "delay", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(bill_tasks, "send_alerta_proyecto", lambda **_kw: None)

    bill_tasks.sync_bill.run(
        {
            "bulletin_number": "555-06",
            "title": "Proyecto demo",
            "entry_date": "2026-05-10",
            "origin_type": BillOrigin.EXECUTIVE,
            "_votaciones": [],
        }
    )

    assert (42, "amendments") in queued
    assert (42, "proposal") not in queued


def test_sync_bill_enqueues_no_layers_when_feature_disabled(monkeypatch):
    """Default AI_SUMMARY_ENABLED=False — no LLM tasks even on a brand-new bill."""
    db = object()
    queued: list[tuple[int, str]] = []

    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(db))
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_enabled", False)

    def fake_upsert_bill(_db, _data):
        return ns(id=42), {
            "is_new": True,
            "status_changed": False,
            "stage_changed": False,
            "full_text_url_changed": False,
            "new_comparado_added": False,
            "old_status": None,
            "new_status": BillStatus.PENDING,
        }

    monkeypatch.setattr(bill_tasks, "upsert_bill", fake_upsert_bill)
    monkeypatch.setattr(
        bill_tasks.generate_bill_summary_layer,
        "delay",
        lambda bill_id, kind: queued.append((bill_id, kind)),
    )
    monkeypatch.setattr(
        bill_tasks.sync_voting_session, "delay", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(bill_tasks, "send_alerta_proyecto", lambda **_kw: None)

    bill_tasks.sync_bill.run(
        {
            "bulletin_number": "555-06",
            "title": "Proyecto demo",
            "entry_date": "2026-05-10",
            "origin_type": BillOrigin.EXECUTIVE,
            "_votaciones": [],
        }
    )

    assert queued == []


def test_generate_amendments_layer_truncates_when_text_exceeds_budget(monkeypatch):
    """Oversized comparado triggers cell truncation and persists truncated=True."""
    first_db = object()
    second_db = object()
    upserts: list[dict] = []
    amendments_calls: list[dict] = []

    monkeypatch.setattr(bill_tasks, "can_generate_bill_summary", lambda: True)
    monkeypatch.setattr(
        bill_tasks, "task_session", session_sequence(first_db, second_db)
    )
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_max_input_chars", 1_000)

    bill_with_comparado = ns(
        documents=[
            ns(
                document_type="comparison",
                document_url="https://example.com/c.pdf",
            )
        ]
    )

    monkeypatch.setattr(bill_tasks, "get_bill", lambda _db, _bid: bill_with_comparado)
    monkeypatch.setattr(
        bill_tasks,
        "extract_comparado_text_from_url",
        lambda _url: "x" * 5_000,
    )

    def fake_generate_amendments(comparado_texts, *, truncated):
        amendments_calls.append({"texts": comparado_texts, "truncated": truncated})
        return {"changes": ["se cambia algo"]}

    monkeypatch.setattr(
        bill_tasks, "generate_amendments_summary", fake_generate_amendments
    )
    monkeypatch.setattr(
        bill_tasks,
        "upsert_bill_summary",
        lambda _db, **kwargs: upserts.append(kwargs) or ns(id=1),
    )

    result = bill_tasks.generate_bill_summary_layer.run(7, "amendments")

    assert result == {"bill_id": 7, "kind": "amendments", "status": "success"}
    assert len(amendments_calls) == 1
    call = amendments_calls[0]
    assert call["truncated"] is True
    # Text was truncated to fit the 1_000-char budget.
    assert len(call["texts"][0]) <= 1_000
    assert len(upserts) == 1
    assert upserts[0]["status"] is BillSummaryStatus.SUCCESS
    assert upserts[0]["truncated"] is True


def test_generate_amendments_layer_passes_truncated_false_when_within_budget(
    monkeypatch,
):
    first_db = object()
    second_db = object()
    upserts: list[dict] = []
    amendments_calls: list[dict] = []

    monkeypatch.setattr(bill_tasks, "can_generate_bill_summary", lambda: True)
    monkeypatch.setattr(
        bill_tasks, "task_session", session_sequence(first_db, second_db)
    )
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_max_input_chars", 1_000_000)

    bill_with_comparado = ns(
        documents=[
            ns(
                document_type="comparison",
                document_url="https://example.com/c.pdf",
            )
        ]
    )

    monkeypatch.setattr(bill_tasks, "get_bill", lambda _db, _bid: bill_with_comparado)
    monkeypatch.setattr(
        bill_tasks, "extract_comparado_text_from_url", lambda _url: "texto corto"
    )

    def fake_generate_amendments(comparado_texts, *, truncated):
        amendments_calls.append({"truncated": truncated})
        return {"changes": []}

    monkeypatch.setattr(
        bill_tasks, "generate_amendments_summary", fake_generate_amendments
    )
    monkeypatch.setattr(
        bill_tasks,
        "upsert_bill_summary",
        lambda _db, **kwargs: upserts.append(kwargs) or ns(id=1),
    )

    bill_tasks.generate_bill_summary_layer.run(7, "amendments")

    assert amendments_calls[0]["truncated"] is False
    assert upserts[0]["truncated"] is False


def test_sync_bill_regenerates_layer_on_stale_prompt_version(monkeypatch):
    db = object()
    queued: list[tuple[int, str]] = []

    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(db))
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_enabled", True)
    monkeypatch.setattr(bill_tasks.settings, "ai_summary_prompt_version", "v3")
    monkeypatch.setattr(bill_tasks.settings, "anthropic_model", "claude-haiku-4-5")

    def fake_upsert_bill(_db, _data):
        return ns(id=42), {
            "is_new": False,
            "status_changed": False,
            "stage_changed": False,
            "full_text_url_changed": False,
            "new_comparado_added": False,
            "old_status": BillStatus.PENDING,
            "new_status": BillStatus.PENDING,
        }

    stale_row = ns(prompt_version="v2", model_name="claude-haiku-4-5")

    monkeypatch.setattr(bill_tasks, "upsert_bill", fake_upsert_bill)
    monkeypatch.setattr(
        bill_tasks,
        "get_bill_summary",
        lambda _db, *, bill_id, kind: stale_row,
    )
    monkeypatch.setattr(
        bill_tasks.generate_bill_summary_layer,
        "delay",
        lambda bill_id, kind: queued.append((bill_id, kind)),
    )
    monkeypatch.setattr(
        bill_tasks.sync_voting_session, "delay", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(bill_tasks, "send_alerta_proyecto", lambda **_kw: None)

    bill_tasks.sync_bill.run(
        {
            "bulletin_number": "555-06",
            "title": "Proyecto demo",
            "entry_date": "2026-05-10",
            "origin_type": BillOrigin.EXECUTIVE,
            "_votaciones": [],
        }
    )

    # Both layers stale on prompt version bump → both enqueue
    assert (42, "proposal") in queued
    assert (42, "amendments") in queued

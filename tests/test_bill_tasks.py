from contextlib import contextmanager
from types import SimpleNamespace

from app.models.enums import BillOrigin, BillStatus
from app.tasks import bills as bill_tasks


def ns(**kwargs):
    return SimpleNamespace(**kwargs)


def session_sequence(*dbs):
    queue = list(dbs)

    @contextmanager
    def _task_session():
        assert queue, "unexpected task_session() call"
        yield queue.pop(0)

    return _task_session


def test_generate_bill_ai_summary_returns_llm_unavailable_when_no_backend(monkeypatch):
    monkeypatch.setattr(bill_tasks, "can_generate_bill_summary", lambda: False)

    result = bill_tasks.generate_bill_ai_summary.run(99)

    assert result == {"bill_id": 99, "status": "llm_unavailable"}


def test_generate_bill_ai_summary_extracts_text_and_persists_summary(monkeypatch):
    first_db = object()
    second_db = object()
    third_db = object()

    monkeypatch.setattr(bill_tasks, "can_generate_bill_summary", lambda: True)
    monkeypatch.setattr(
        bill_tasks, "task_session", session_sequence(first_db, second_db, third_db)
    )

    def fake_get_bill(db, bill_id):
        assert db is first_db
        assert bill_id == 7
        return ns(full_text=None, full_text_url="https://example.com/bill.pdf")

    def fake_update_bill_full_text(db, bill_id, full_text):
        assert db is second_db
        assert bill_id == 7
        assert full_text == "texto completo"
        return ns(full_text="texto completo")

    def fake_update_bill_ai_summary(db, bill_id, ai_summary):
        assert db is third_db
        assert bill_id == 7
        assert ai_summary == "resumen ciudadano"
        return ns(id=bill_id, ai_summary=ai_summary)

    monkeypatch.setattr(bill_tasks, "get_bill", fake_get_bill)
    monkeypatch.setattr(
        bill_tasks, "extract_text_from_url", lambda url: "texto completo"
    )
    monkeypatch.setattr(bill_tasks, "update_bill_full_text", fake_update_bill_full_text)
    monkeypatch.setattr(
        bill_tasks, "generate_bill_summary", lambda text: "resumen ciudadano"
    )
    monkeypatch.setattr(
        bill_tasks, "update_bill_ai_summary", fake_update_bill_ai_summary
    )

    result = bill_tasks.generate_bill_ai_summary.run(7)

    assert result == {"bill_id": 7, "status": "summarized"}


def test_sync_bill_enqueues_summary_votes_and_notifications_for_existing_bill(
    monkeypatch,
):
    first_db = object()
    queued_summary_ids: list[int] = []
    queued_votes: list[tuple[dict, str]] = []
    notifications: list[dict] = []

    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(first_db))

    def fake_upsert_bill(db, data):
        assert db is first_db
        assert data["bulletin_number"] == "555-06"
        return ns(id=42), {
            "is_new": False,
            "status_changed": True,
            "stage_changed": True,
            "old_status": BillStatus.PENDING,
            "new_status": BillStatus.APPROVED,
        }

    monkeypatch.setattr(bill_tasks, "upsert_bill", fake_upsert_bill)
    monkeypatch.setattr(
        bill_tasks.generate_bill_ai_summary, "delay", queued_summary_ids.append
    )
    monkeypatch.setattr(
        bill_tasks.VoteParser,
        "parse_senate_vote",
        lambda raw_vote, bulletin: {"parsed": raw_vote, "bulletin": bulletin},
    )
    monkeypatch.setattr(
        bill_tasks.sync_voting_session,
        "delay",
        lambda payload, bulletin: queued_votes.append((payload, bulletin)),
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
            "_votaciones": [{"session": "42"}],
        }
    )

    assert result == {"bill_id": 42, "status": "ok"}
    assert queued_summary_ids == [42]
    assert queued_votes == [
        ({"parsed": {"session": "42"}, "bulletin": "555-06"}, "555-06")
    ]
    assert len(notifications) == 2
    assert notifications[0]["change_type"] == "status_changed"
    assert notifications[0]["extra"]["old_status"] == "pending"
    assert notifications[0]["extra"]["new_status"] == "approved"
    assert notifications[1]["change_type"] == "stage_changed"


def test_sync_bill_enqueues_only_new_notification_for_new_bill(monkeypatch):
    first_db = object()
    queued_summary_ids: list[int] = []
    notifications: list[dict] = []

    monkeypatch.setattr(bill_tasks, "task_session", session_sequence(first_db))

    def fake_upsert_bill(db, data):
        assert db is first_db
        assert data["bulletin_number"] == "555-06"
        return ns(id=42), {
            "is_new": True,
            "status_changed": False,
            "stage_changed": False,
            "old_status": None,
            "new_status": BillStatus.PENDING,
        }

    monkeypatch.setattr(bill_tasks, "upsert_bill", fake_upsert_bill)
    monkeypatch.setattr(
        bill_tasks.generate_bill_ai_summary, "delay", queued_summary_ids.append
    )
    monkeypatch.setattr(
        bill_tasks.sync_voting_session,
        "delay",
        lambda payload, bulletin: None,
    )
    monkeypatch.setattr(
        bill_tasks.VoteParser,
        "parse_senate_vote",
        lambda raw_vote, bulletin: {"parsed": raw_vote, "bulletin": bulletin},
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
    assert queued_summary_ids == [42]
    assert len(notifications) == 1
    assert notifications[0]["change_type"] == "new"
    assert notifications[0]["extra"]["origin"] == "executive"

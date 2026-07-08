from app.services import bill_summary_backfill as m


class _FakeExec:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        return _FakeExec(self._rows)


def test_status_filter_enqueues_matching_rows_and_ignores_stale_only(monkeypatch):
    # The status branch selects the (bill_id, bulletin) rows for us; verify it
    # enqueues exactly those for the target kind, and that stale_only (which
    # would otherwise drop failed/skipped rows carrying the current version) is
    # ignored when statuses is set.
    monkeypatch.setattr(m.settings, "ai_summary_enabled", True)
    calls = []
    monkeypatch.setattr(m, "dispatch", lambda _task, *args: calls.append(args))

    db = _FakeSession([(1, "100-01"), (2, "200-02")])
    result = m.regenerate_bill_summaries(
        db,
        bulletin=None,
        kind="proposal",
        stale_only=True,
        statuses=["skipped", "failed"],
    )

    assert result["tasks_enqueued"] == 2
    assert calls == [(1, "proposal"), (2, "proposal")]


def test_no_op_when_feature_disabled(monkeypatch):
    monkeypatch.setattr(m.settings, "ai_summary_enabled", False)
    db = _FakeSession([(1, "100-01")])
    result = m.regenerate_bill_summaries(
        db, bulletin=None, kind="all", stale_only=False, statuses=["failed"]
    )
    assert result == {
        "bills_scanned": 0,
        "tasks_enqueued": 0,
        "enqueued": [],
        "disabled": True,
    }

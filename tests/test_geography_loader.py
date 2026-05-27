from types import SimpleNamespace

from app.geography import loader


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, state=None):
        self.state = state
        self.added: list[object] = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0

    def execute(self, stmt):
        return FakeResult(self.state)

    def add(self, obj):
        self.added.append(obj)
        if getattr(obj, "entity_type", None) == "geography":
            self.state = obj

    def flush(self):
        self.flush_count += 1

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        self.close_count += 1


def test_run_load_geography_commits_loaded_version(monkeypatch):
    db = FakeDB()
    dataset = SimpleNamespace(version="2026-05-27")

    monkeypatch.setattr(loader, "SessionLocal", lambda: db)
    monkeypatch.setattr(loader, "load_geography_dataset", lambda path: dataset)
    monkeypatch.setattr(
        loader,
        "apply_geography_dataset",
        lambda db_session, loaded_dataset: {
            "regions": 16,
            "provinces": 55,
            "communes": 346,
            "districts": 28,
            "circumscriptions": 16,
        },
    )

    result = loader.run_load_geography(dry_run=False)

    assert result["version"] == "2026-05-27"
    assert result["regions"] == 16
    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert db.close_count == 1
    assert db.state is not None
    assert db.state.entity_type == "geography"
    assert db.state.last_cursor == "2026-05-27"


def test_run_load_geography_rolls_back_dry_run(monkeypatch):
    db = FakeDB(state=SimpleNamespace(entity_type="geography"))
    dataset = SimpleNamespace(version="2026-05-27")

    monkeypatch.setattr(loader, "SessionLocal", lambda: db)
    monkeypatch.setattr(loader, "load_geography_dataset", lambda path: dataset)
    monkeypatch.setattr(
        loader,
        "apply_geography_dataset",
        lambda db_session, loaded_dataset: {
            "regions": 16,
            "provinces": 55,
            "communes": 346,
            "districts": 28,
            "circumscriptions": 16,
        },
    )

    result = loader.run_load_geography(dry_run=True)

    assert result["dry_run"] is True
    assert db.commit_count == 0
    assert db.rollback_count == 1
    assert db.close_count == 1

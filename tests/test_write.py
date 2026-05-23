from types import SimpleNamespace

from app.models.enums import VoteChoice
from app.services import write


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, *lookup_results):
        self.lookup_results = list(lookup_results)
        self.added: list[object] = []
        self.deleted: list[object] = []

    def execute(self, stmt):
        assert self.lookup_results, "unexpected db.execute() call"
        return FakeResult(self.lookup_results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)


def test_reconcile_votes_updates_adds_and_removes_votes(monkeypatch):
    db = FakeDB(10, 20, None)
    updated_votes: list[object] = []

    monkeypatch.setattr(
        write, "_touch_syncable", lambda db_session, vote: updated_votes.append(vote)
    )

    existing_vote = SimpleNamespace(legislator_id=10, vote=VoteChoice.AGAINST)
    stale_vote = SimpleNamespace(legislator_id=30, vote=VoteChoice.FOR)
    voting_session = SimpleNamespace(id=99, votes=[existing_vote, stale_vote])

    changed = write._reconcile_votes(
        db,
        voting_session,
        [
            {"_legislator_name": "Ada Demo", "vote": VoteChoice.FOR},
            {"_legislator_name": "Beto Demo", "vote": VoteChoice.ABSTAIN},
            {"_legislator_name": "Persona Desconocida", "vote": VoteChoice.AGAINST},
        ],
    )

    assert changed is True
    assert existing_vote.vote is VoteChoice.FOR
    assert updated_votes == [existing_vote]
    assert db.deleted == [stale_vote]
    assert len(db.added) == 1
    new_vote = db.added[0]
    assert new_vote.voting_session_id == 99
    assert new_vote.legislator_id == 20
    assert new_vote.vote is VoteChoice.ABSTAIN

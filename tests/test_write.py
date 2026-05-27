from types import SimpleNamespace

from app.models.core import Region
from app.models.enums import ChamberType, VoteChoice
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
        self.flush_count = 0

    def execute(self, stmt):
        assert self.lookup_results, "unexpected db.execute() call"
        return FakeResult(self.lookup_results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)

    def flush(self):
        self.flush_count += 1
        for index, obj in enumerate(self.added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = 1000 + index


def test_reconcile_votes_updates_adds_and_removes_votes(monkeypatch):
    db = FakeDB()
    updated_votes: list[object] = []

    monkeypatch.setattr(
        write, "_touch_syncable", lambda db_session, vote: updated_votes.append(vote)
    )
    monkeypatch.setattr(
        write,
        "_resolve_vote_legislator",
        lambda db_session, payload, chamber_type=None: {
            "Ada Demo": 10,
            "Beto Demo": 20,
        }.get(payload.get("_legislator_name")),
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


def test_resolve_vote_legislator_creates_placeholder_from_chamber_external_id():
    db = FakeDB(None, None)

    legislator_id = write._resolve_vote_legislator(
        db,
        {
            "legislator_external_id": "camara:803",
            "_legislator_name": "René Alinco Bustos",
            "legislator_first_name": "René",
            "legislator_last_name": "Alinco Bustos",
        },
        ChamberType.DEPUTIES,
    )

    assert legislator_id == 1001
    assert db.flush_count == 1
    assert len(db.added) == 1
    placeholder = db.added[0]
    assert placeholder.bcn_id == "camara:803"
    assert placeholder.first_name == "René"
    assert placeholder.last_name == "Alinco Bustos"
    assert placeholder.full_name == "René Alinco Bustos"
    assert placeholder.chamber_type is ChamberType.DEPUTIES
    assert placeholder.is_active is False


def test_get_or_create_circumscription_links_mapped_region(monkeypatch):
    monkeypatch.setattr(write, "CIRCUMSCRIPTION_REGION_MAP", {5: 12})
    monkeypatch.setattr(write, "_touch_syncable", lambda db_session, obj: None)

    region = Region(number=12, name="Region 12", capital="Capital")
    db = FakeDB(None, region)  # no existing circ, then region lookup

    circumscription = write._get_or_create_circumscription(db, 5, "Circ 5")

    assert circumscription is not None
    assert circumscription.number == 5
    assert region in circumscription.regions


def test_get_or_create_circumscription_skips_when_unmapped(monkeypatch):
    monkeypatch.setattr(write, "CIRCUMSCRIPTION_REGION_MAP", {})

    db = FakeDB(None)  # only the circ lookup; no region lookup expected

    circumscription = write._get_or_create_circumscription(db, 7, "Circ 7")

    assert circumscription is not None
    assert circumscription.number == 7
    assert list(circumscription.regions) == []

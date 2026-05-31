"""Unit tests for bloc affiliation (ADR-0006).

Covers the `PoliticalParty.current_bloc` property logic, the two write-service
helpers, and serialization of the new fields. Uses the existing `FakeDB` mock
pattern (see `test_write.py`) and `SimpleNamespace` ORM stand-ins (see
`test_api_routes.py`) — no PostgreSQL required.
"""

from datetime import date, timedelta
from types import SimpleNamespace

from app.models.enums import Bloc
from app.models.legislature import PoliticalParty
from app.schemas.legislators import LegislatorSummary, PartyBrief
from app.services import write
from test_write import FakeDB

TODAY = date.today()


def _affiliation(bloc: Bloc, start: date, end: date | None = None) -> SimpleNamespace:
    return SimpleNamespace(bloc=bloc, start_date=start, end_date=end)


def _current_bloc(affiliations: list[SimpleNamespace]) -> Bloc | None:
    # Invoke the property's underlying function against a stand-in object so we
    # exercise the real logic without SQLAlchemy instrumentation.
    return PoliticalParty.current_bloc.fget(
        SimpleNamespace(bloc_affiliations=affiliations)
    )


# ── current_bloc property ─────────────────────────────────────────────


def test_current_bloc_none_when_no_affiliations():
    assert _current_bloc([]) is None


def test_current_bloc_picks_open_ended_active_row():
    result = _current_bloc(
        [_affiliation(Bloc.OFICIALISMO, TODAY - timedelta(days=400))]
    )
    assert result is Bloc.OFICIALISMO


def test_current_bloc_ignores_closed_rows():
    result = _current_bloc(
        [
            _affiliation(
                Bloc.OPOSICION,
                TODAY - timedelta(days=800),
                TODAY - timedelta(days=400),
            ),
        ]
    )
    assert result is None


def test_current_bloc_excludes_future_rows():
    result = _current_bloc([_affiliation(Bloc.OFICIALISMO, TODAY + timedelta(days=10))])
    assert result is None


def test_current_bloc_prefers_latest_start_among_active():
    # A change of government: old oposición row closed yesterday, new oficialismo
    # row opened today. The latest active start wins.
    result = _current_bloc(
        [
            _affiliation(
                Bloc.OPOSICION, TODAY - timedelta(days=800), TODAY - timedelta(days=1)
            ),
            _affiliation(Bloc.OFICIALISMO, TODAY),
        ]
    )
    assert result is Bloc.OFICIALISMO


def test_current_bloc_end_date_today_is_exclusive():
    # end_date == today means the row ended; it is no longer active.
    result = _current_bloc(
        [_affiliation(Bloc.OFICIALISMO, TODAY - timedelta(days=30), TODAY)]
    )
    assert result is None


# ── upsert_bloc_affiliation ───────────────────────────────────────────


def test_upsert_bloc_affiliation_inserts_when_absent():
    db = FakeDB(None)  # the existence lookup returns nothing
    affiliation = write.upsert_bloc_affiliation(
        db, party_id=7, bloc=Bloc.OFICIALISMO, start_date=date(2026, 3, 11)
    )
    assert affiliation in db.added
    assert affiliation.party_id == 7
    assert affiliation.bloc is Bloc.OFICIALISMO
    assert affiliation.start_date == date(2026, 3, 11)
    assert affiliation.end_date is None
    assert db.flush_count == 1


def test_upsert_bloc_affiliation_accepts_string_bloc():
    db = FakeDB(None)
    affiliation = write.upsert_bloc_affiliation(
        db, party_id=1, bloc="oposicion", start_date=date(2026, 3, 11)
    )
    assert affiliation.bloc is Bloc.OPOSICION


def test_upsert_bloc_affiliation_updates_existing(monkeypatch):
    touched: list[object] = []
    monkeypatch.setattr(write, "_touch_syncable", lambda db, obj: touched.append(obj))
    existing = SimpleNamespace(
        party_id=7, bloc=Bloc.OPOSICION, start_date=date(2026, 3, 11), end_date=None
    )
    db = FakeDB(existing)
    result = write.upsert_bloc_affiliation(
        db,
        party_id=7,
        bloc=Bloc.OFICIALISMO,
        start_date=date(2026, 3, 11),
        end_date=date(2030, 3, 11),
    )
    assert result is existing
    assert existing.bloc is Bloc.OFICIALISMO
    assert existing.end_date == date(2030, 3, 11)
    assert touched == [existing]
    assert existing not in db.added


def test_upsert_bloc_affiliation_noop_when_unchanged(monkeypatch):
    touched: list[object] = []
    monkeypatch.setattr(write, "_touch_syncable", lambda db, obj: touched.append(obj))
    existing = SimpleNamespace(
        party_id=7, bloc=Bloc.OFICIALISMO, start_date=date(2026, 3, 11), end_date=None
    )
    db = FakeDB(existing)
    write.upsert_bloc_affiliation(
        db, party_id=7, bloc=Bloc.OFICIALISMO, start_date=date(2026, 3, 11)
    )
    assert touched == []


# ── update_legislator_default_bloc ────────────────────────────────────


def test_update_legislator_default_bloc_sets_value(monkeypatch):
    touched: list[object] = []
    monkeypatch.setattr(write, "_touch_syncable", lambda db, obj: touched.append(obj))
    legislator = SimpleNamespace(id=42, default_bloc=None)
    db = FakeDB(legislator)
    result = write.update_legislator_default_bloc(db, 42, Bloc.OPOSICION)
    assert result is legislator
    assert legislator.default_bloc is Bloc.OPOSICION
    assert touched == [legislator]


def test_update_legislator_default_bloc_clears_value(monkeypatch):
    monkeypatch.setattr(write, "_touch_syncable", lambda db, obj: None)
    legislator = SimpleNamespace(id=42, default_bloc=Bloc.OFICIALISMO)
    db = FakeDB(legislator)
    write.update_legislator_default_bloc(db, 42, None)
    assert legislator.default_bloc is None


def test_update_legislator_default_bloc_missing_returns_none():
    db = FakeDB(None)
    assert write.update_legislator_default_bloc(db, 999, Bloc.OFICIALISMO) is None


# ── serialization ─────────────────────────────────────────────────────


def test_party_brief_serializes_current_bloc():
    party = SimpleNamespace(
        id=1,
        name="Partido Demo",
        abbreviation="PD",
        color="#112233",
        current_bloc=Bloc.OFICIALISMO,
    )
    brief = PartyBrief.model_validate(party)
    assert brief.current_bloc is Bloc.OFICIALISMO
    assert brief.model_dump()["current_bloc"] == "oficialismo"


def test_legislator_summary_serializes_default_bloc():
    now = "2026-05-22T12:00:00"
    legislator = SimpleNamespace(
        id=20,
        bcn_id="senado:20",
        full_name="Ada Demo",
        chamber_type="senate",
        photo_thumbnail_url=None,
        party=None,
        district=None,
        circumscription=None,
        is_active=True,
        default_bloc=Bloc.OPOSICION,
        created_at=now,
        updated_at=now,
        sync_version=101,
    )
    summary = LegislatorSummary.model_validate(legislator)
    assert summary.default_bloc is Bloc.OPOSICION


def test_legislator_summary_default_bloc_defaults_to_none():
    # A legislator object without the attribute still validates (party members
    # and unaligned independents both leave default_bloc unset).
    now = "2026-05-22T12:00:00"
    legislator = SimpleNamespace(
        id=21,
        bcn_id=None,
        full_name="Beto Demo",
        chamber_type="deputies",
        photo_thumbnail_url=None,
        party=None,
        district=None,
        circumscription=None,
        is_active=True,
        created_at=now,
        updated_at=now,
        sync_version=102,
    )
    summary = LegislatorSummary.model_validate(legislator)
    assert summary.default_bloc is None

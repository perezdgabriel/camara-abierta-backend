from datetime import date, datetime
from types import SimpleNamespace

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


def test_parse_senado_vote_display_name_extracts_structured_name_parts():
    parsed = write._parse_senado_vote_display_name("Araya G., Pedro")

    assert parsed == {
        "first_name": "Pedro",
        "paternal_last_name": "Araya",
        "maternal_initial": "G",
    }


def test_senado_vote_name_matches_legislator_shorthand_display_name():
    legislator = SimpleNamespace(
        first_name="Pedro",
        last_name="Araya Guerrero",
        full_name="Pedro Araya Guerrero",
        chamber_type=ChamberType.SENATE,
    )

    assert (
        write._senado_vote_name_matches_legislator(
            "Araya G., Pedro",
            legislator,
        )
        is True
    )
    assert (
        write._senado_vote_name_matches_legislator(
            "Bianchi R., Karim",
            legislator,
        )
        is False
    )


def test_get_or_create_circumscription_does_not_fabricate_region_links():
    db = FakeDB(None)

    circumscription = write._get_or_create_circumscription(db, 7, "Circ 7")

    assert circumscription is not None
    assert circumscription.number == 7
    assert list(circumscription.regions) == []


def _enrichment_legislator(**overrides):
    """A SimpleNamespace pre-populated with every column enrich_legislator_profile touches."""
    base = dict(
        bcn_id="camara:1254",
        district_id=None,
        party_id=77,  # OpenData-sourced; must be left untouched
        photo_url=None,
        photo_thumbnail_url=None,
        profile_url=None,
        biography=None,
        bcn_uri=None,
        bcn_wiki_url=None,
        profession=None,
        twitter_handle=None,
        gender=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_enrich_legislator_profile_sets_district_and_photo_only(monkeypatch):
    monkeypatch.setattr(write, "_touch_syncable", lambda db_session, obj: None)

    legislator = _enrichment_legislator()
    district = SimpleNamespace(id=8, number=8)
    db = FakeDB(legislator, district)  # legislator lookup, then district lookup

    result = write.enrich_legislator_profile(
        db,
        "camara:1254",
        {
            "district_number": 8,
            "photo_url": "https://img/x.jpg",
            "profile_url": "https://camara.cl/x",
        },
    )

    assert result is legislator
    assert legislator.district_id == 8
    assert legislator.photo_url == "https://img/x.jpg"
    assert legislator.profile_url == "https://camara.cl/x"
    assert legislator.party_id == 77  # untouched (ADR-0001)
    assert db.flush_count == 1


def test_enrich_legislator_profile_returns_none_when_unmatched():
    db = FakeDB(None)  # no legislator with this bcn_id

    result = write.enrich_legislator_profile(db, "camara:9999", {"district_number": 5})

    assert result is None


def test_enrich_legislator_profile_writes_bcn_sourced_enrichment_fields(monkeypatch):
    monkeypatch.setattr(write, "_touch_syncable", lambda db_session, obj: None)

    legislator = _enrichment_legislator()
    db = FakeDB(legislator)  # no district lookup since district_number is omitted

    result = write.enrich_legislator_profile(
        db,
        "camara:1254",
        {
            "bcn_uri": "http://datos.bcn.cl/recurso/persona/4558",
            "bcn_wiki_url": "https://www.bcn.cl/historiapolitica/resenas/Carter",
            "profession": "Diseñador Industrial",
            "twitter_handle": "Alvaro_CarterF",
            "gender": "M",
            "photo_url": "https://www.bcn.cl/laborparlamentaria/imagen/4558.jpg",
        },
    )

    assert result is legislator
    assert legislator.bcn_uri.endswith("/persona/4558")
    assert legislator.bcn_wiki_url.startswith("https://www.bcn.cl/historiapolitica/")
    assert legislator.profession == "Diseñador Industrial"
    assert legislator.twitter_handle == "Alvaro_CarterF"
    assert legislator.gender == "M"
    assert legislator.photo_url.endswith("/4558.jpg")
    assert legislator.party_id == 77  # ADR-0001 invariant


def test_enrich_legislator_profile_truncates_to_column_max_lengths(monkeypatch):
    monkeypatch.setattr(write, "_touch_syncable", lambda db_session, obj: None)

    legislator = _enrichment_legislator()
    db = FakeDB(legislator)

    write.enrich_legislator_profile(
        db,
        "camara:1254",
        {
            "twitter_handle": "x" * 200,  # 50 char column
            "profession": "y" * 500,  # 200 char column
        },
    )

    assert len(legislator.twitter_handle) == 50
    assert len(legislator.profession) == 200


def test_upsert_parliamentary_appointment_creates_when_new(monkeypatch):
    monkeypatch.setattr(write, "_touch_syncable", lambda db_session, obj: None)
    chamber = SimpleNamespace(id=42)
    monkeypatch.setattr(
        write,
        "_get_or_create_chamber",
        lambda db_session, chamber_type: chamber,
    )

    db = FakeDB(None)  # no existing appointment

    appointment = write.upsert_parliamentary_appointment(
        db,
        legislator_id=7,
        bcn_appointment_uri="http://datos.bcn.cl/recurso/persona/4558/nombramiento/2",
        chamber_type=ChamberType.SENATE,
        start_date="2022-03-11",
        end_date="2030-03-11",
    )

    assert appointment in db.added
    assert appointment.legislator_id == 7
    assert appointment.chamber_id == 42
    assert appointment.start_date == "2022-03-11"
    assert appointment.end_date == "2030-03-11"


def test_upsert_parliamentary_appointment_updates_existing(monkeypatch):
    touched: list[object] = []
    monkeypatch.setattr(
        write, "_touch_syncable", lambda db_session, obj: touched.append(obj)
    )
    chamber = SimpleNamespace(id=42)
    monkeypatch.setattr(
        write,
        "_get_or_create_chamber",
        lambda db_session, chamber_type: chamber,
    )

    existing = SimpleNamespace(
        legislator_id=7,
        chamber_id=42,
        bcn_appointment_uri="http://x/y/z",
        start_date="2022-03-11",
        end_date="2026-03-11",  # outdated end date
    )
    db = FakeDB(existing)

    result = write.upsert_parliamentary_appointment(
        db,
        legislator_id=7,
        bcn_appointment_uri="http://x/y/z",
        chamber_type=ChamberType.SENATE,
        start_date="2022-03-11",
        end_date="2030-03-11",
    )

    assert result is existing
    assert existing.end_date == "2030-03-11"
    assert existing in touched
    assert db.added == []  # not re-added


def test_parse_datetime_round_trips_iso_datetime_string_as_naive_chile_time():
    # Voting-session times are naive Chile wall-clock end-to-end (no tz
    # arithmetic): upstream ``Fecha`` arrives without an offset, stored
    # naive on a ``TIMESTAMP WITHOUT TIME ZONE`` column, displayed verbatim.
    result = write._parse_datetime("2026-06-03T18:33:55")

    assert result == datetime(2026, 6, 3, 18, 33, 55)
    assert result.tzinfo is None


def test_parse_datetime_strips_explicit_offset_treating_wall_clock_as_chile():
    # Defensive: nothing upstream produces this today, but if it ever does,
    # the wall-clock value is what's user-facing — keep the hour, drop the tz.
    result = write._parse_datetime("2026-06-03T18:33:55-04:00")

    assert result == datetime(2026, 6, 3, 18, 33, 55)
    assert result.tzinfo is None


def test_parse_datetime_handles_date_only_strings_at_naive_midnight():
    # Date-only strings (legacy wspublico Senate failover, OpenData date
    # fallback) still produce midnight — naive now, not UTC.
    result = write._parse_datetime("2026-05-12")

    assert result == datetime(2026, 5, 12)
    assert result.tzinfo is None


def test_parse_datetime_leaves_naive_datetime_object_naive():
    naive = datetime(2026, 6, 3, 18, 33, 55)

    result = write._parse_datetime(naive)

    assert result == datetime(2026, 6, 3, 18, 33, 55)
    assert result.tzinfo is None


def test_parse_datetime_promotes_date_object_to_naive_midnight():
    result = write._parse_datetime(date(2026, 6, 3))

    assert result == datetime(2026, 6, 3)
    assert result.tzinfo is None


def test_parse_datetime_returns_sentinel_for_unparseable_input(caplog):
    # Regression: the legacy fallback was ``datetime.now()`` which made
    # corrupted rows look like fresh activity (e.g. when restsil returned
    # FECHA_VOTACION=None for old votes and we hadn't chained the HORA
    # fallback yet). The sentinel is impossible real data and sorts to the
    # bottom of every chronological view, plus we log loudly.
    caplog.set_level("WARNING", logger="app.services.write")
    result = write._parse_datetime("not-a-date")

    assert result == datetime(1, 1, 1)
    assert any(
        "unparseable upstream value" in record.message for record in caplog.records
    )

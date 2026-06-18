from datetime import date, datetime
from types import SimpleNamespace

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


# ── Reference-data helpers ───────────────────────────────────────────────


def test_get_or_create_circumscription_does_not_fabricate_region_links():
    db = FakeDB(None)

    circumscription = write._get_or_create_circumscription(db, 7, "Circ 7")

    assert circumscription is not None
    assert circumscription.number == 7
    assert list(circumscription.regions) == []


# ── enrich_legislator_profile (matches by bcn_uri now, ADR-0015) ─────────


def _enrichment_legislator(**overrides):
    """SimpleNamespace mirroring the new person-level Legislator shape."""
    base = dict(
        bcn_uri=None,
        photo_url=None,
        photo_thumbnail_url=None,
        profile_url=None,
        biography=None,
        bcn_wiki_url=None,
        profession=None,
        twitter_handle=None,
        gender=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_enrich_legislator_profile_returns_none_when_unmatched():
    # No legislator matches the bcn_uri AND no chamber_external_id provided.
    db = FakeDB(None)

    result = write.enrich_legislator_profile(
        db,
        bcn_uri="http://datos.bcn.cl/persona/9999",
        fields={"photo_url": "https://x"},
    )

    assert result is None


def test_enrich_legislator_profile_writes_bcn_sourced_enrichment_fields(monkeypatch):
    monkeypatch.setattr(write, "_touch_syncable", lambda db_session, obj: None)

    legislator = _enrichment_legislator()
    db = FakeDB(legislator)

    result = write.enrich_legislator_profile(
        db,
        bcn_uri="http://datos.bcn.cl/recurso/persona/4558",
        fields={
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


def test_enrich_legislator_profile_truncates_to_column_max_lengths(monkeypatch):
    monkeypatch.setattr(write, "_touch_syncable", lambda db_session, obj: None)

    legislator = _enrichment_legislator()
    db = FakeDB(legislator)

    write.enrich_legislator_profile(
        db,
        bcn_uri="http://x",
        fields={
            "twitter_handle": "x" * 200,  # 50 char column
            "profession": "y" * 500,  # 200 char column
        },
    )

    assert len(legislator.twitter_handle) == 50
    assert len(legislator.profession) == 200


# ── Vote resolver / orphan reconciliation (ADR-0015) ─────────────────────


def test_resolve_vote_legislator_returns_none_when_no_term_matches():
    # No LegislatorTerm covers the bridge for this date — orphan path.
    db = FakeDB(None)

    legislator_id = write._resolve_vote_legislator(db, "camara:803", date(2019, 5, 4))

    assert legislator_id is None
    # The new resolver never auto-creates placeholder legislators.
    assert db.added == []


def test_resolve_vote_legislator_returns_term_legislator_id_on_match():
    term = SimpleNamespace(legislator_id=42)
    db = FakeDB(term)

    legislator_id = write._resolve_vote_legislator(db, "camara:803", date(2019, 5, 4))

    assert legislator_id == 42


# ── _parse_date year plausibility ────────────────────────────────────────


def test_parse_date_round_trips_date_object():
    result = write._parse_date(date(2026, 5, 12))

    assert result == date(2026, 5, 12)


def test_parse_date_round_trips_dmy_string():
    result = write._parse_date("12/05/2026")

    assert result == date(2026, 5, 12)


def test_parse_date_repairs_common_millennium_typo(caplog):
    caplog.set_level("WARNING", logger="app.services.write")

    result = write._parse_date("2626-05-12")

    assert result == date(2026, 5, 12)
    assert any(
        "repaired implausible upstream year" in record.message
        for record in caplog.records
    )


def test_parse_date_rejects_implausible_year_without_known_repair(caplog):
    caplog.set_level("WARNING", logger="app.services.write")

    result = write._parse_date("2926-05-12")

    assert result is None
    assert any(
        "rejecting implausible upstream year" in record.message
        for record in caplog.records
    )


# ── _parse_datetime (preserved across the refactor) ───────────────────────


def test_parse_datetime_round_trips_iso_datetime_string_as_naive_chile_time():
    result = write._parse_datetime("2026-06-03T18:33:55")

    assert result == datetime(2026, 6, 3, 18, 33, 55)
    assert result.tzinfo is None


def test_parse_datetime_strips_explicit_offset_treating_wall_clock_as_chile():
    result = write._parse_datetime("2026-06-03T18:33:55-04:00")

    assert result == datetime(2026, 6, 3, 18, 33, 55)
    assert result.tzinfo is None


def test_parse_datetime_handles_date_only_strings_at_naive_midnight():
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


def test_parse_datetime_repairs_common_millennium_typo(caplog):
    caplog.set_level("WARNING", logger="app.services.write")

    result = write._parse_datetime("2626-06-03T18:33:55")

    assert result == datetime(2026, 6, 3, 18, 33, 55)
    assert any(
        "repaired implausible upstream year" in record.message
        for record in caplog.records
    )


def test_parse_datetime_returns_sentinel_for_unrepairable_implausible_year(caplog):
    caplog.set_level("WARNING", logger="app.services.write")

    result = write._parse_datetime("2926-06-03T18:33:55")

    assert result == datetime(1, 1, 1)
    assert any(
        "rejecting implausible upstream year" in record.message
        for record in caplog.records
    )


def test_parse_datetime_returns_sentinel_for_unparseable_input(caplog):
    caplog.set_level("WARNING", logger="app.services.write")
    result = write._parse_datetime("not-a-date")

    assert result == datetime(1, 1, 1)
    assert any(
        "unparseable upstream value" in record.message for record in caplog.records
    )

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.models.enums import CalendarEventKind
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


# ── Authorship matching (canonical-key) ──────────────────────────────────


def test_canonicalize_handles_upstream_comma_swap_and_accents():
    upstream = write._canonicalize_legislator_name("Núñez Urrutia, Paulina")
    db_form = write._canonicalize_legislator_name("Paulina Núñez Urrutia")
    assert upstream == db_form == "paulina nunez urrutia"


def test_canonicalize_collapses_double_spaces_and_strips_punctuation():
    upstream = write._canonicalize_legislator_name("Araya  Guerrero, Jaime")
    db_form = write._canonicalize_legislator_name("Jaime Araya Guerrero")
    assert upstream == db_form == "jaime araya guerrero"


def test_canonicalize_handles_apostrophes_and_particles():
    # O'Higgins-style apostrophe and the "Y" particle observed in upstream data
    assert (
        write._canonicalize_legislator_name("Cuello Peña Y Lillo, Luis")
        == write._canonicalize_legislator_name("Luis Cuello Peña y Lillo")
        == "luis cuello pena y lillo"
    )
    assert write._canonicalize_legislator_name(
        "O'Higgins, Bernardo"
    ) == write._canonicalize_legislator_name("Bernardo O'Higgins")


def test_canonicalize_returns_empty_for_blank_and_punct_only():
    assert write._canonicalize_legislator_name("") == ""
    assert write._canonicalize_legislator_name("   ,  ") == ""


class _FakeBill:
    def __init__(self, bulletin: str, authorships=None, origin=None):
        from app.models.enums import BillOrigin

        self.id = 100
        self.bulletin_number = bulletin
        self.authorships = list(authorships or [])
        self.origin = origin if origin is not None else BillOrigin.DEPUTIES


class _LegislatorRowsDB:
    """Fake DB whose execute() yields the seeded (id, full_name, last_name, first_name) rows."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.added: list[object] = []
        self.deleted: list[object] = []

    def execute(self, stmt):
        return iter(self._rows)

    def add(self, obj):
        self.added.append(obj)

    def delete(self, obj):
        self.deleted.append(obj)


def test_reconcile_authorships_matches_upstream_format_names():
    db = _LegislatorRowsDB(
        [
            (1, "Paulina Núñez Urrutia", "Núñez Urrutia", "Paulina"),
            (2, "Jaime Araya Guerrero", "Araya Guerrero", "Jaime"),
            (3, "Luis Cuello Peña y Lillo", "Cuello Peña y Lillo", "Luis"),
        ]
    )
    bill = _FakeBill(bulletin="100-06")

    changed = write._reconcile_authorships(
        db,
        bill,
        [
            {"name": "Núñez Urrutia, Paulina"},
            {"name": "Araya  Guerrero, Jaime"},
            {"name": "Cuello Peña Y Lillo, Luis"},
        ],
    )

    assert changed is True
    assert {a.legislator_id for a in db.added} == {1, 2, 3}
    assert all(a.bill_id == 100 and a.author_type == "author" for a in db.added)


def test_reconcile_authorships_warns_on_unmatched_name(caplog):
    caplog.set_level("WARNING", logger="app.services.write")
    db = _LegislatorRowsDB([(1, "Paulina Núñez Urrutia", "Núñez Urrutia", "Paulina")])
    bill = _FakeBill(bulletin="200-07")

    write._reconcile_authorships(
        db,
        bill,
        [
            {"name": "Núñez Urrutia, Paulina"},  # matches
            {"name": "Nadie Existe"},  # unmatched
        ],
    )

    assert {a.legislator_id for a in db.added} == {1}
    unmatched_warnings = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "Unmatched authorship name" in r.message
    ]
    assert len(unmatched_warnings) == 1
    assert "200-07" in unmatched_warnings[0].message
    assert "Nadie Existe" in unmatched_warnings[0].message


def test_reconcile_authorships_logs_collision_and_skips_both(caplog):
    caplog.set_level("ERROR", logger="app.services.write")
    # Two legislators normalize to the same key — both should be excluded
    # from the lookup, so even the literal upstream name won't match.
    db = _LegislatorRowsDB(
        [
            (1, "Paulina Núñez Urrutia", "Núñez Urrutia", "Paulina"),
            # same key after accent strip
            (2, "Paulina Nunez Urrutia", "Nunez Urrutia", "Paulina"),
        ]
    )
    bill = _FakeBill(bulletin="300-08")

    write._reconcile_authorships(db, bill, [{"name": "Núñez Urrutia, Paulina"}])

    assert db.added == []
    collision_errors = [
        r
        for r in caplog.records
        if r.levelname == "ERROR" and "canonical-key collision" in r.message
    ]
    assert len(collision_errors) == 1


def test_reconcile_authorships_matches_when_db_is_missing_a_middle_given_name():
    # DB row lacks "Ignacio" (upstream sources don't agree on how many given
    # names to carry) — exact full-name match misses, surname fallback should
    # still resolve it via the given-name subset check.
    db = _LegislatorRowsDB([(1, "Carlos Kuschel Silva", "Kuschel Silva", "Carlos")])
    bill = _FakeBill(bulletin="18218-25")

    write._reconcile_authorships(db, bill, [{"name": "Kuschel Silva, Carlos Ignacio"}])

    assert {a.legislator_id for a in db.added} == {1}


def test_reconcile_authorships_matches_when_upstream_uses_second_given_name():
    # DB leads with an unused first given name ("Coca") while upstream (and
    # common usage) only carries the second ("Ericka").
    db = _LegislatorRowsDB(
        [(1, "Coca Ericka Ñanco Vásquez", "Ñanco Vásquez", "Coca Ericka")]
    )
    bill = _FakeBill(bulletin="18228-04")

    write._reconcile_authorships(db, bill, [{"name": "Ñanco Vásquez, Ericka"}])

    assert {a.legislator_id for a in db.added} == {1}


def test_reconcile_authorships_surname_fallback_stays_unmatched_when_ambiguous(caplog):
    caplog.set_level("WARNING", logger="app.services.write")
    # Two legislators share a surname and both have a compound given name
    # that's subset-compatible with the upstream name — neither the exact
    # full-name match nor the fallback should guess between them.
    db = _LegislatorRowsDB(
        [
            (1, "Juan Ignacio Pérez Soto", "Pérez Soto", "Juan Ignacio"),
            (2, "Juan Carlos Pérez Soto", "Pérez Soto", "Juan Carlos"),
        ]
    )
    bill = _FakeBill(bulletin="18240-01")

    write._reconcile_authorships(db, bill, [{"name": "Pérez Soto, Juan"}])

    assert db.added == []
    assert any(
        r.levelname == "WARNING" and "Unmatched authorship name" in r.message
        for r in caplog.records
    )


def test_reconcile_authorships_deletes_no_longer_matched():
    existing = SimpleNamespace(legislator_id=1, author_type="author")
    bill = _FakeBill(bulletin="400-09", authorships=[existing])
    db = _LegislatorRowsDB([(1, "Paulina Núñez Urrutia", "Núñez Urrutia", "Paulina")])

    write._reconcile_authorships(db, bill, [])  # upstream now empty

    assert existing in db.deleted
    assert db.added == []


def test_reconcile_authorships_skips_executive_bills(caplog):
    from app.models.enums import BillOrigin

    bill = _FakeBill(bulletin="500-10", origin=BillOrigin.EXECUTIVE)
    db = _LegislatorRowsDB([])
    ministry_authors = [{"name": "Ministerio de Hacienda"}]

    changed = write._reconcile_authorships(db, bill, ministry_authors)

    assert changed is False
    assert db.added == []
    warnings = [r for r in caplog.records if "Unmatched authorship" in r.message]
    assert warnings == []


# ── upsert_calendar_event input validation ──────────────────────────────


def test_upsert_calendar_event_rejects_missing_kind():
    db = FakeDB()
    with pytest.raises(ValueError, match="kind"):
        write.upsert_calendar_event(
            db, {"starts_at": datetime(2026, 7, 1, 10, 0), "title": "x"}
        )


def test_upsert_calendar_event_rejects_non_datetime_starts_at():
    db = FakeDB()
    with pytest.raises(ValueError, match="starts_at"):
        write.upsert_calendar_event(
            db,
            {
                "kind": CalendarEventKind.SESION,
                "starts_at": "2026-07-01",
                "title": "x",
            },
        )


def test_upsert_calendar_event_rejects_blank_title():
    db = FakeDB()
    with pytest.raises(ValueError, match="title"):
        write.upsert_calendar_event(
            db,
            {
                "kind": CalendarEventKind.SESION,
                "starts_at": datetime(2026, 7, 1, 10, 0),
                "title": "   ",
            },
        )


def test_upsert_calendar_event_persists_bulletin_number_for_orphan_relink():
    """ADR-0017 §9: bulletin_number must survive the upsert even when the
    bill hasn't arrived yet, so upsert_bill's reconcile step can find it."""
    db = FakeDB()
    event = write.upsert_calendar_event(
        db,
        {
            "kind": CalendarEventKind.VOTACION,
            "starts_at": datetime(2026, 7, 1, 10, 0),
            "title": "Vota Boletín 100-06",
            "bulletin_number": "100-06",
        },
    )
    assert event.bulletin_number == "100-06"
    assert event.bill_id is None
    assert db.added == [event]


# ── _reconcile_orphan_calendar_events (ADR-0017 §9) ──────────────────────


class _CapturingDB:
    """Fake DB that only records executed Core statements for inspection."""

    def __init__(self):
        self.executed: list[object] = []

    def execute(self, stmt):
        self.executed.append(stmt)
        return None


def test_reconcile_orphan_calendar_events_targets_matching_bulletin():
    db = _CapturingDB()
    bill = SimpleNamespace(id=42, bulletin_number="100-06")

    write._reconcile_orphan_calendar_events(db, bill)

    assert len(db.executed) == 1
    compiled_sql = str(db.executed[0].compile(compile_kwargs={"literal_binds": True}))
    assert "calendar_events" in compiled_sql
    assert "bill_id=42" in compiled_sql
    assert "bulletin_number = '100-06'" in compiled_sql
    assert "bill_id IS NULL" in compiled_sql

from __future__ import annotations

from app.models.enums import ChamberType
from app.services.audit import mocion_authors as audit


def _row(
    *,
    bulletin: str = "100-06",
    title: str = "T",
    chamber: ChamberType | None = ChamberType.DEPUTIES,
    year: int = 2026,
    db_count: int = 0,
    unmatched: list[str] | None = None,
    upstream_count: int | None = None,
) -> audit.MocionRow:
    return audit.MocionRow(
        bill_id=1,
        bulletin=bulletin,
        title=title,
        origin_chamber=chamber,
        entry_year=year,
        db_author_count=db_count,
        upstream_xml_author_count=upstream_count,
        unmatched_names=list(unmatched or []),
    )


# ── Histogram ──────────────────────────────────────────────────────────


def test_histogram_buckets_by_chamber():
    rows = [
        _row(db_count=0, chamber=ChamberType.DEPUTIES),
        _row(db_count=1, chamber=ChamberType.DEPUTIES),
        _row(db_count=3, chamber=ChamberType.DEPUTIES),
        _row(db_count=5, chamber=ChamberType.SENATE),
        _row(db_count=11, chamber=ChamberType.SENATE),
        _row(db_count=0, chamber=None),
    ]
    hist = audit.AuditResult(rows).histogram()

    assert hist["0"] == {ChamberType.DEPUTIES: 1, None: 1}
    assert hist["1"] == {ChamberType.DEPUTIES: 1}
    assert hist["2-3"] == {ChamberType.DEPUTIES: 1}
    assert hist["4-5"] == {ChamberType.SENATE: 1}
    assert hist[">10"] == {ChamberType.SENATE: 1}
    assert hist["6-10"] == {}


def test_histogram_by_year_returns_sorted_dict_with_all_buckets():
    rows = [
        _row(year=2024, db_count=0),
        _row(year=2026, db_count=4),
        _row(year=2025, db_count=2),
    ]
    by_year = audit.AuditResult(rows).histogram_by_year()

    assert list(by_year.keys()) == [2024, 2025, 2026]
    assert by_year[2024]["0"] == 1
    assert by_year[2025]["2-3"] == 1
    assert by_year[2026]["4-5"] == 1
    assert by_year[2024]["6-10"] == 0  # all buckets present even if empty


# ── Implausible counts ─────────────────────────────────────────────────


def test_implausible_applies_chamber_specific_limits():
    rows = [
        _row(db_count=10, chamber=ChamberType.DEPUTIES),  # at limit, ok
        _row(db_count=11, chamber=ChamberType.DEPUTIES),  # over
        _row(db_count=5, chamber=ChamberType.SENATE),  # at limit, ok
        _row(db_count=6, chamber=ChamberType.SENATE),  # over
        _row(db_count=99, chamber=None),  # no limit known, not flagged
    ]
    flagged = audit.AuditResult(rows).implausible()
    assert [r.db_author_count for r in flagged] == [11, 6]


# ── Top unmatched aggregation ──────────────────────────────────────────


def test_top_unmatched_ranks_by_frequency_across_rows():
    rows = [
        _row(unmatched=["A. Perez", "B. Soto"]),
        _row(unmatched=["A. Perez"]),
        _row(unmatched=["A. Perez", "C. Diaz"]),
    ]
    top = audit.top_unmatched(rows, limit=2)
    assert top[0] == ("A. Perez", 3)
    assert ("B. Soto", 1) in top or ("C. Diaz", 1) in top
    assert len(top) == 2


# ── CSV writer ─────────────────────────────────────────────────────────


def test_write_csv_emits_expected_columns_and_blanks_for_no_reparse(tmp_path):
    rows = [
        _row(bulletin="100-06", title="Hello", db_count=0),
        _row(bulletin="200-07", title="World", db_count=3),
    ]
    result = audit.AuditResult(rows)
    out = tmp_path / "audit.csv"

    written = audit.write_csv(str(out), result, reparse_ran=False, db=None)

    assert written == 2
    content = out.read_text(encoding="utf-8")
    header, body, *_ = content.splitlines()
    assert header.split(",") == [
        "bulletin",
        "title",
        "origin_chamber",
        "entry_year",
        "db_author_count",
        "upstream_xml_author_count",
        "unmatched_names",
        "closest_match_for_first_unmatched",
    ]
    assert "100-06" in body
    # reparse columns left blank
    assert body.endswith(",,,")


def test_write_csv_with_reparse_includes_unmatched_names_and_upstream_count(tmp_path):
    rows = [
        _row(
            bulletin="100-06",
            db_count=0,
            upstream_count=3,
            unmatched=["A. Perez", "B. Soto"],
        ),
    ]
    result = audit.AuditResult(rows)
    out = tmp_path / "audit.csv"

    # db=None -> no closest-match suggestion lookup, blank for that column
    audit.write_csv(str(out), result, reparse_ran=True, db=None)

    rows_out = out.read_text(encoding="utf-8").splitlines()
    assert "A. Perez | B. Soto" in rows_out[1]
    assert ",3," in rows_out[1]


# ── reparse_subset ─────────────────────────────────────────────────────


class _LegislatorRowsDB:
    """Fake DB whose execute() yields the seeded (id, full_name) rows
    that `_build_legislator_lookup` consumes once at the top of
    `reparse_subset`. No per-name queries happen anymore.
    """

    def __init__(self, rows):
        self._rows = list(rows)

    def execute(self, stmt):
        return iter(self._rows)


def test_reparse_subset_fills_counts_and_unmatched_names():
    rows = [
        _row(bulletin="100-06", db_count=0),
        _row(bulletin="200-07", db_count=1),
    ]
    fetched = {
        "100-06": {
            "authors": [
                {"legislator": "Ada Lovelace"},
                {"legislator": "Grace Hopper"},
            ]
        },
        "200-07": {
            "authors": [
                {"legislator": "  "},  # blank → filtered
                {"legislator": "Margaret Hamilton"},
            ]
        },
    }
    # Only Ada is in the roster; the matcher's canonical-key dict will
    # therefore flag Grace and Margaret as unmatched.
    fake_db = _LegislatorRowsDB([(7, "Ada Lovelace")])

    audit.reparse_subset(fake_db, rows, fetcher=fetched.get)

    assert rows[0].upstream_xml_author_count == 2
    assert rows[0].unmatched_names == ["Grace Hopper"]
    assert rows[1].upstream_xml_author_count == 1
    assert rows[1].unmatched_names == ["Margaret Hamilton"]


def test_reparse_subset_uses_canonical_matcher_for_upstream_format():
    """End-to-end check: upstream "Apellido, Nombre" + DB "Nombre Apellido"
    must NOT show up as unmatched (the bug that prompted this rewrite).
    """
    rows = [_row(bulletin="100-06", db_count=0)]
    fetched = {
        "100-06": {
            "authors": [
                {"legislator": "Núñez Urrutia, Paulina"},
                {"legislator": "Araya  Guerrero, Jaime"},  # double space
            ]
        }
    }
    fake_db = _LegislatorRowsDB(
        [(1, "Paulina Núñez Urrutia"), (2, "Jaime Araya Guerrero")]
    )

    audit.reparse_subset(fake_db, rows, fetcher=fetched.get)

    assert rows[0].upstream_xml_author_count == 2
    assert rows[0].unmatched_names == []


def test_reparse_subset_skips_rows_when_fetcher_returns_none():
    rows = [_row(bulletin="missing")]
    fake_db = _LegislatorRowsDB([])

    audit.reparse_subset(fake_db, rows, fetcher=lambda _bulletin: None)

    assert rows[0].upstream_xml_author_count is None
    assert rows[0].unmatched_names == []


def test_reparse_subset_skips_rows_when_fetcher_raises():
    rows = [_row(bulletin="boom")]
    fake_db = _LegislatorRowsDB([])

    def _boom(_bulletin: str):
        raise RuntimeError("upstream down")

    audit.reparse_subset(fake_db, rows, fetcher=_boom)

    assert rows[0].upstream_xml_author_count is None
    assert rows[0].unmatched_names == []


# ── render_summary smoke test ──────────────────────────────────────────


def test_render_summary_mentions_key_sections_without_reparse():
    rows = [
        _row(bulletin="100-06", db_count=0, chamber=ChamberType.DEPUTIES),
        _row(bulletin="200-07", db_count=3, chamber=ChamberType.SENATE),
    ]
    text = audit.render_summary(audit.AuditResult(rows), reparse_ran=False)

    assert "Moción authorship audit" in text
    assert "Total mociones (origin=DEPUTIES): 2" in text
    assert "Zero-author mociones: 1" in text
    assert "100-06" in text
    assert "Author-count distribution by origin chamber" in text
    assert "Author-count distribution by entry year" in text
    # No reparse → hints toward --reparse
    assert "--reparse" in text


def test_render_summary_top_unmatched_shown_after_reparse():
    rows = [
        _row(
            bulletin="100-06",
            db_count=0,
            upstream_count=2,
            unmatched=["A. Perez"],
        ),
        _row(
            bulletin="200-07",
            db_count=1,
            upstream_count=2,
            unmatched=["A. Perez"],
        ),
    ]
    text = audit.render_summary(audit.AuditResult(rows), reparse_ran=True)
    assert "Top unmatched upstream names" in text
    assert "A. Perez" in text
    assert "Re-parse pass: re-fetched 2 mociones" in text

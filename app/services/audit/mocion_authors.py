"""Audit moción authorship coverage.

A moción is a bill with ``origin == BillOrigin.DEPUTIES``. The ingestor
matches upstream author names against ``Legislator.full_name`` with exact
case-insensitive equality (see ``_reconcile_authorships`` in
``app/services/write.py``) — any mismatch is silently dropped. This module
surfaces the damage and, with ``--reparse``, identifies the actual upstream
names that failed to match.

The DB-only pass is pure SQL. The ``--reparse`` pass takes a fetcher
callable so tests can swap the wspublico round-trip for a fake.
"""

from __future__ import annotations

import csv
import difflib
import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import BillOrigin, ChamberType
from app.models.legislature import Chamber, Legislator
from app.models.proyecto import Bill, BillAuthorship

logger = logging.getLogger(__name__)

# Chamber-of-Deputies mociones cap at 10 signers; Senate at 5.
# Anything above is a parser bug or a name collision.
# Key type allows None so lookups for rows with unknown chamber are typed.
CHAMBER_AUTHOR_LIMITS: dict[ChamberType | None, int] = {
    ChamberType.DEPUTIES: 10,
    ChamberType.SENATE: 5,
}

HISTOGRAM_BUCKETS: list[tuple[str, Callable[[int], bool]]] = [
    ("0", lambda n: n == 0),
    ("1", lambda n: n == 1),
    ("2-3", lambda n: 2 <= n <= 3),
    ("4-5", lambda n: 4 <= n <= 5),
    ("6-10", lambda n: 6 <= n <= 10),
    (">10", lambda n: n > 10),
]


@dataclass
class MocionRow:
    bill_id: int
    bulletin: str
    title: str
    origin_chamber: ChamberType | None
    entry_year: int
    db_author_count: int
    # Filled by the reparse pass when requested:
    upstream_xml_author_count: int | None = None
    unmatched_names: list[str] = field(default_factory=list)


@dataclass
class AuditResult:
    rows: list[MocionRow]

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def zero_author(self) -> list[MocionRow]:
        return [r for r in self.rows if r.db_author_count == 0]

    def implausible(self) -> list[MocionRow]:
        flagged: list[MocionRow] = []
        for row in self.rows:
            limit = CHAMBER_AUTHOR_LIMITS.get(row.origin_chamber)
            if limit is not None and row.db_author_count > limit:
                flagged.append(row)
        return flagged

    def histogram(self) -> dict[str, dict[ChamberType | None, int]]:
        """``{bucket: {chamber: count}}`` — chamber ``None`` is the unknown bucket."""
        result: dict[str, dict[ChamberType | None, int]] = {
            label: defaultdict(int) for label, _ in HISTOGRAM_BUCKETS
        }
        for row in self.rows:
            for label, predicate in HISTOGRAM_BUCKETS:
                if predicate(row.db_author_count):
                    result[label][row.origin_chamber] += 1
                    break
        return {label: dict(counts) for label, counts in result.items()}

    def histogram_by_year(self) -> dict[int, dict[str, int]]:
        result: dict[int, dict[str, int]] = defaultdict(
            lambda: {label: 0 for label, _ in HISTOGRAM_BUCKETS}
        )
        for row in self.rows:
            for label, predicate in HISTOGRAM_BUCKETS:
                if predicate(row.db_author_count):
                    result[row.entry_year][label] += 1
                    break
        return dict(sorted(result.items()))


def collect_mocion_rows(db: Session) -> list[MocionRow]:
    """Load every moción with its current DB author count."""
    author_count_subq = (
        select(BillAuthorship.bill_id, func.count().label("author_count"))
        .group_by(BillAuthorship.bill_id)
        .subquery()
    )
    stmt = (
        select(
            Bill.id,
            Bill.bulletin_number,
            Bill.title,
            Chamber.chamber_type,
            Bill.entry_date,
            func.coalesce(author_count_subq.c.author_count, 0),
        )
        .outerjoin(Chamber, Bill.origin_chamber_id == Chamber.id)
        .outerjoin(author_count_subq, Bill.id == author_count_subq.c.bill_id)
        .where(Bill.origin == BillOrigin.DEPUTIES)
        .order_by(Bill.entry_date.desc())
    )
    rows: list[MocionRow] = []
    for bill_id, bulletin, title, chamber_type, entry_date, author_count in db.execute(
        stmt
    ):
        rows.append(
            MocionRow(
                bill_id=bill_id,
                bulletin=bulletin,
                title=title or "",
                origin_chamber=chamber_type,
                entry_year=entry_date.year if entry_date else 0,
                db_author_count=int(author_count),
            )
        )
    return rows


def reparse_subset(
    db: Session,
    rows: Iterable[MocionRow],
    fetcher: Callable[[str], dict[str, Any] | None],
) -> None:
    """Mutates ``rows`` in place: fills ``upstream_xml_author_count`` and
    ``unmatched_names`` for each via ``fetcher``.

    ``fetcher(bulletin)`` returns the parsed wspublico bill dict (same shape
    ``SenadoClient.get_bill_by_bulletin`` returns) or ``None`` on failure.

    Uses the live ingestor's canonical-key matcher so the audit's
    "unmatched" verdict matches what `_reconcile_authorships` would do on
    a fresh write — no risk of the audit drifting from the matcher.
    """
    from app.services.write import _build_legislator_lookup, _match_authorship_name

    lookup = _build_legislator_lookup(db)

    for row in rows:
        try:
            bill = fetcher(row.bulletin)
        except Exception as exc:
            logger.warning("reparse failed for %s: %s", row.bulletin, exc)
            continue
        if bill is None:
            logger.warning("reparse returned no bill for %s", row.bulletin)
            continue
        names = [(a.get("legislator") or "").strip() for a in bill.get("authors", [])]
        names = [n for n in names if n]
        row.upstream_xml_author_count = len(names)
        for name in names:
            if _match_authorship_name(lookup, name) is None:
                row.unmatched_names.append(name)


def top_unmatched(rows: Iterable[MocionRow], limit: int = 20) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for row in rows:
        for name in row.unmatched_names:
            counter[name] += 1
    return counter.most_common(limit)


def suggest_closest(db: Session, name: str) -> str | None:
    candidates = [
        full_name for (full_name,) in db.execute(select(Legislator.full_name))
    ]
    matches = difflib.get_close_matches(name, candidates, n=1, cutoff=0.6)
    return matches[0] if matches else None


# ── Output ─────────────────────────────────────────────────────────────


def render_summary(result: AuditResult, reparse_ran: bool) -> str:
    lines: list[str] = []
    push = lines.append

    push("=" * 70)
    push("Moción authorship audit")
    push("=" * 70)
    push(f"Total mociones (origin=DEPUTIES): {result.total}")
    push("")

    zero = result.zero_author
    push(f"Zero-author mociones: {len(zero)}")
    if zero:
        push("  (first 10 shown)")
        for r in zero[:10]:
            chamber = r.origin_chamber.value if r.origin_chamber else "—"
            title = r.title[:60] + "…" if len(r.title) > 60 else r.title
            push(f"  - {r.bulletin}  [{chamber}, {r.entry_year}]  {title}")
        if len(zero) > 10:
            push(f"  ... and {len(zero) - 10} more")
    push("")

    implausible = result.implausible()
    push(f"Implausibly high author counts: {len(implausible)}")
    for r in implausible[:10]:
        limit = CHAMBER_AUTHOR_LIMITS.get(r.origin_chamber)
        chamber = r.origin_chamber.value if r.origin_chamber else "—"
        push(
            f"  - {r.bulletin}  [{chamber}, {r.entry_year}]  "
            f"{r.db_author_count} authors (limit {limit})"
        )
    push("")

    push("Author-count distribution by origin chamber:")
    push(f"  {'bucket':>6}  {'deputies':>10}  {'senate':>10}  {'unknown':>10}")
    hist = result.histogram()
    for label, _ in HISTOGRAM_BUCKETS:
        counts = hist[label]
        push(
            f"  {label:>6}  "
            f"{counts.get(ChamberType.DEPUTIES, 0):>10}  "
            f"{counts.get(ChamberType.SENATE, 0):>10}  "
            f"{counts.get(None, 0):>10}"
        )
    push("")

    push("Author-count distribution by entry year:")
    push(
        f"  {'year':>4}  " + "  ".join(f"{label:>5}" for label, _ in HISTOGRAM_BUCKETS)
    )
    for year, counts in result.histogram_by_year().items():
        push(
            f"  {year:>4}  "
            + "  ".join(f"{counts[label]:>5}" for label, _ in HISTOGRAM_BUCKETS)
        )
    push("")

    if reparse_ran:
        reparsed = [r for r in result.rows if r.upstream_xml_author_count is not None]
        with_unmatched = [r for r in reparsed if r.unmatched_names]
        push(f"Re-parse pass: re-fetched {len(reparsed)} mociones (0 or 1 DB authors)")
        push(f"  with at least one unmatched upstream name: {len(with_unmatched)}")
        push("")
        top = top_unmatched(result.rows, limit=20)
        if top:
            push("Top unmatched upstream names (across re-fetched mociones):")
            push(f"  {'count':>5}  name")
            for name, count in top:
                push(f"  {count:>5}  {name}")
            push("")
    else:
        zero_or_one = sum(1 for r in result.rows if r.db_author_count <= 1)
        if zero_or_one:
            push(
                f"To inspect the {zero_or_one} mociones with 0 or 1 authors, "
                "re-run with --reparse (re-fetches upstream XML; bounded subset)."
            )
            push("")

    return "\n".join(lines)


def write_csv(
    path: str,
    result: AuditResult,
    reparse_ran: bool,
    db: Session | None = None,
) -> int:
    columns = [
        "bulletin",
        "title",
        "origin_chamber",
        "entry_year",
        "db_author_count",
        "upstream_xml_author_count",
        "unmatched_names",
        "closest_match_for_first_unmatched",
    ]
    written = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for r in result.rows:
            first_unmatched = r.unmatched_names[0] if r.unmatched_names else ""
            closest = ""
            if reparse_ran and first_unmatched and db is not None:
                closest = suggest_closest(db, first_unmatched) or ""
            writer.writerow(
                {
                    "bulletin": r.bulletin,
                    "title": r.title,
                    "origin_chamber": (
                        r.origin_chamber.value if r.origin_chamber else ""
                    ),
                    "entry_year": r.entry_year,
                    "db_author_count": r.db_author_count,
                    "upstream_xml_author_count": (
                        r.upstream_xml_author_count
                        if r.upstream_xml_author_count is not None
                        else ""
                    ),
                    "unmatched_names": " | ".join(r.unmatched_names),
                    "closest_match_for_first_unmatched": closest,
                }
            )
            written += 1
    return written


# ── Top-level runner used by the CLI ──────────────────────────────────


def run(
    db: Session,
    *,
    reparse: bool,
    export_csv: str | None,
    fetcher: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Execute the audit. Prints a human report to stdout and returns a
    small summary dict for the CLI's JSON tail.

    ``fetcher`` defaults to a live ``SenadoClient`` round-trip — tests pass
    an alternative.
    """
    rows = collect_mocion_rows(db)
    result = AuditResult(rows=rows)

    if reparse:
        owned_client = None
        if fetcher is None:
            from app.ingestors.clients.senado import SenadoClient

            owned_client = SenadoClient()
            live_client = owned_client

            def _live_fetcher(bulletin: str) -> dict[str, Any] | None:
                return live_client.get_bill_by_bulletin(bulletin)

            fetcher = _live_fetcher

        subset = [r for r in rows if r.db_author_count <= 1]
        logger.info("re-parsing %d mociones with <=1 DB authors", len(subset))
        try:
            reparse_subset(db, subset, fetcher)
        finally:
            if owned_client is not None:
                owned_client.close()

    print(render_summary(result, reparse_ran=reparse))

    csv_rows = 0
    if export_csv:
        csv_rows = write_csv(export_csv, result, reparse_ran=reparse, db=db)
        print(f"CSV written: {export_csv} ({csv_rows} rows)")

    return {
        "total_mociones": result.total,
        "zero_author": len(result.zero_author),
        "implausible_high": len(result.implausible()),
        "reparsed": reparse,
        "csv_rows": csv_rows,
    }

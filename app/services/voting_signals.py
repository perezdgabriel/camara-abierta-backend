"""Behavior-revealing signals over voting sessions.

Computes the editorial signals shown on the ``/votaciones`` page of the web
frontend. Signal definitions and editorial meaning live in the web repo's
``CONTEXT.md``. This module is the single source of truth for the *thresholds*
and the *computation rules*.

Signals computed:
  - votación dividida (narrow margin, high participation)
  - bajo registro (share of legislators leaving no recorded vote above threshold)
  - quiebre de bloque (party cohesion below threshold)
  - divergencia entre cámaras (same bill, different result across chambers)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.enums import ChamberType, SignalType, VoteChoice, VotingResult
from app.models.legislature import Chamber, Legislator, LegislatorTerm, PoliticalParty
from app.models.votacion import (
    Vote,
    VotingSession,
    VotingSessionSignal,
    VotingWindowAggregate,
)

# ── Thresholds (v1: Python constants — see CONTEXT.md) ─────────────────────

DIVIDED_MARGIN_RATIO_MAX = 0.10
"""Threshold for *votación dividida*: |for-against|/(for+against) must be
below this for the signal to fire."""

DIVIDED_PARTICIPATION_MIN = 0.60
"""Minimum (for+against)/total_seats for *votación dividida* — guards against
narrow margins caused by no-votes (those are *bajo registro* territory)."""

NO_VOTE_RATE_MIN = 0.20
"""Threshold for *bajo registro*: no_votes/total_seats must exceed this."""

NO_VOTE_BASELINE_WINDOW_DAYS = 30
"""Window for computing the chamber's baseline no-vote rate."""

QUIEBRE_COHESION_MIN = 0.80
"""Threshold for *quiebre de bloque*: party cohesion below this fires."""

QUIEBRE_PARTY_MIN_MEMBERS = 5
"""Minimum number of party members voting in a session for the cohesion
check to apply. Filters out tiny parties whose internal "disagreement" is
statistical noise."""

DIVERGENCIA_SEVERITY = 0.5
"""Constant severity for *divergencia entre cámaras* — the signal either
applies or it doesn't; there's no degree."""

DEFAULT_WINDOW_DAYS = 30
"""Default rolling window for the /votaciones page."""

HIGHLIGHTED_GRID_LIMIT = 6
FALLBACK_HIGH_TURNOUT_LIMIT = 6


# ── Result type ────────────────────────────────────────────────────────────


@dataclass
class SignalResult:
    """In-memory representation of a fired signal before persistence."""

    signal_type: SignalType
    severity: float
    payload: dict[str, Any]


# ── Individual signal computations ─────────────────────────────────────────


def compute_votacion_dividida(session: VotingSession) -> SignalResult | None:
    """Narrow margin with high participation.

    Symmetric on approved/rejected: a 70-72 rejection fires the same as a
    73-71 approval. The narrowness is what matters editorially.
    """
    decisive = session.votes_for + session.votes_against
    if decisive == 0:
        return None
    total_seats = session.chamber.total_seats
    if total_seats <= 0:
        return None

    margin = abs(session.votes_for - session.votes_against)
    margin_ratio = margin / decisive
    participation = decisive / total_seats

    if margin_ratio >= DIVIDED_MARGIN_RATIO_MAX:
        return None
    if participation < DIVIDED_PARTICIPATION_MIN:
        return None

    return SignalResult(
        signal_type=SignalType.VOTACION_DIVIDIDA,
        # Severity rises as the margin shrinks (closer to tie = more divided)
        severity=1.0 - margin_ratio,
        payload={
            "margin": int(margin),
            "margin_ratio": round(margin_ratio, 4),
            "participation": round(participation, 4),
        },
    )


def compute_bajo_registro(db: Session, session: VotingSession) -> SignalResult | None:
    """No-vote rate above threshold.

    Baseline = mean ``no_votes`` across same-chamber sessions in the preceding
    30 days. Surfaced in the payload so the UI can show a comparison; not used
    as the firing threshold (we use a flat no-vote-rate cutoff to avoid the
    "everything looks normal" problem on a chamber with chronically low
    registration).
    """
    total_seats = session.chamber.total_seats
    if total_seats <= 0:
        return None

    no_vote_rate = session.no_votes / total_seats
    if no_vote_rate < NO_VOTE_RATE_MIN:
        return None

    window_start = session.voting_date - timedelta(days=NO_VOTE_BASELINE_WINDOW_DAYS)
    baseline = (
        db.query(func.avg(VotingSession.no_votes))
        .filter(
            VotingSession.chamber_id == session.chamber_id,
            VotingSession.voting_date >= window_start,
            VotingSession.voting_date < session.voting_date,
        )
        .scalar()
    )
    baseline_no_votes = float(baseline) if baseline is not None else 0.0

    return SignalResult(
        signal_type=SignalType.BAJO_REGISTRO,
        severity=no_vote_rate,
        payload={
            "no_votes": int(session.no_votes),
            "baseline_no_votes": round(baseline_no_votes, 2),
            "no_vote_rate": round(no_vote_rate, 4),
        },
    )


# ── Orchestration ──────────────────────────────────────────────────────────


def _legislator_brief_dict(legislator: Legislator) -> dict[str, Any]:
    party = legislator.current_party
    chamber = legislator.current_chamber_type
    return {
        "id": legislator.id,
        "full_name": legislator.full_name,
        "chamber_type": chamber.value if chamber is not None else None,
        "party": (
            {
                "id": party.id,
                "name": party.name,
                "abbreviation": party.abbreviation,
                "color": party.color,
            }
            if party is not None
            else None
        ),
    }


def _party_brief_dict(party: PoliticalParty) -> dict[str, Any]:
    return {
        "id": party.id,
        "name": party.name,
        "abbreviation": party.abbreviation,
        "color": party.color,
    }


def _bill_brief_dict(bill: Any) -> dict[str, Any]:
    return {
        "id": bill.id,
        "bulletin_number": bill.bulletin_number,
        "title": bill.title,
    }


def _voting_session_summary_dict(session: VotingSession) -> dict[str, Any]:
    chamber = session.chamber
    bill = session.bill
    return {
        "id": session.id,
        "bcn_id": session.bcn_id,
        "chamber": {
            "id": chamber.id,
            "chamber_type": chamber.chamber_type.value,
            "name": chamber.name,
        },
        "bill": _bill_brief_dict(bill) if bill is not None else None,
        "voting_date": session.voting_date.isoformat(),
        "voting_type": session.voting_type.value,
        "subject": session.subject,
        "result": session.result.value if session.result is not None else None,
        "votes_for": session.votes_for,
        "votes_against": session.votes_against,
        "abstentions": session.abstentions,
        "no_votes": session.no_votes,
        "quorum_type": session.quorum_type,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
        "sync_version": session.sync_version,
    }


def compute_quiebre_bloque(db: Session, session: VotingSession) -> SignalResult | None:
    """Party-line break: at least one party with ≥5 voting members has
    cohesion below the threshold.

    Party affiliation is determined *at the vote date* via ``LegislatorTerm``:
    a legislator's current party may differ from their party when this vote
    happened. The join filters terms by ``start_date <= voting_date`` and
    ``(end_date IS NULL OR end_date >= voting_date)``.
    """
    vote_date: date = session.voting_date.date()

    rows = (
        db.query(Vote.legislator_id, Vote.vote, LegislatorTerm.party_id)
        .join(
            LegislatorTerm,
            and_(
                LegislatorTerm.legislator_id == Vote.legislator_id,
                LegislatorTerm.start_date <= vote_date,
                or_(
                    LegislatorTerm.end_date.is_(None),
                    LegislatorTerm.end_date >= vote_date,
                ),
            ),
        )
        .filter(
            Vote.voting_session_id == session.id,
            Vote.vote.in_([VoteChoice.FOR, VoteChoice.AGAINST, VoteChoice.ABSTAIN]),
            LegislatorTerm.party_id.isnot(None),
        )
        .all()
    )

    if not rows:
        return None

    by_party: dict[int, list[tuple[int, VoteChoice]]] = defaultdict(list)
    for legislator_id, vote, party_id in rows:
        by_party[party_id].append((legislator_id, vote))

    party_ids = list(by_party.keys())
    parties_by_id = {
        p.id: p
        for p in db.query(PoliticalParty).filter(PoliticalParty.id.in_(party_ids)).all()
    }

    breaks: list[dict[str, Any]] = []
    min_cohesion = 1.0
    dissenter_ids: list[int] = []

    for party_id, voters in by_party.items():
        if len(voters) < QUIEBRE_PARTY_MIN_MEMBERS:
            continue
        choice_counts = Counter(v for _, v in voters)
        majority_choice, majority_count = choice_counts.most_common(1)[0]
        cohesion = majority_count / len(voters)
        if cohesion >= QUIEBRE_COHESION_MIN:
            continue
        party_dissenter_ids = [lid for lid, v in voters if v != majority_choice]
        dissenter_ids.extend(party_dissenter_ids)
        min_cohesion = min(min_cohesion, cohesion)

        party = parties_by_id.get(party_id)
        if party is None:
            continue
        dissenter_objs = (
            db.query(Legislator)
            .options(
                selectinload(Legislator.terms).joinedload(LegislatorTerm.party),
                selectinload(Legislator.terms).joinedload(LegislatorTerm.chamber),
            )
            .filter(Legislator.id.in_(party_dissenter_ids))
            .all()
            if party_dissenter_ids
            else []
        )
        breaks.append(
            {
                "party": _party_brief_dict(party),
                "voting_members": len(voters),
                "majority_choice": majority_choice.value,
                "cohesion": round(cohesion, 4),
                "dissenters": [_legislator_brief_dict(leg) for leg in dissenter_objs],
            }
        )

    if not breaks:
        return None

    return SignalResult(
        signal_type=SignalType.QUIEBRE_BLOQUE,
        # Severity rises as the worst cohesion drops further below the threshold.
        severity=1.0 - min_cohesion,
        payload={
            "parties_below_threshold": breaks,
            "threshold": QUIEBRE_COHESION_MIN,
        },
    )


def compute_divergencia_camaras(
    db: Session, session: VotingSession
) -> SignalResult | None:
    """Same bill, different result across the two chambers.

    Only flags the *later* of the two sessions to avoid double-firing on a
    bill that appears in both chambers.
    """
    if session.bill_id is None or session.result is None:
        return None

    counterpart = (
        db.query(VotingSession)
        .options(
            joinedload(VotingSession.chamber),
            joinedload(VotingSession.bill),
        )
        .filter(
            VotingSession.bill_id == session.bill_id,
            VotingSession.chamber_id != session.chamber_id,
            VotingSession.id != session.id,
            VotingSession.result.isnot(None),
        )
        .order_by(VotingSession.voting_date.desc())
        .first()
    )

    if counterpart is None:
        return None
    if session.voting_date <= counterpart.voting_date:
        return None  # only the later session fires
    if session.result == counterpart.result:
        return None

    return SignalResult(
        signal_type=SignalType.DIVERGENCIA_CAMARAS,
        severity=DIVERGENCIA_SEVERITY,
        payload={
            "counterpart_session": _voting_session_summary_dict(counterpart),
            "bill": _bill_brief_dict(counterpart.bill)
            if counterpart.bill is not None
            else _bill_brief_dict(session.bill),
        },
    )


def avg_cohesion(db: Session, window_days: int) -> float | None:
    """Mean party cohesion across (session, party) pairs in the window.

    Ignores parties with fewer than ``QUIEBRE_PARTY_MIN_MEMBERS`` voting in a
    given session — matches the *quiebre* filter for a consistent baseline.
    Returns ``None`` if there's no data in the window.
    """
    cutoff = _window_cutoff(window_days)

    rows = (
        db.query(
            VotingSession.id.label("session_id"),
            LegislatorTerm.party_id.label("party_id"),
            Vote.vote.label("vote"),
        )
        .join(Vote, Vote.voting_session_id == VotingSession.id)
        .join(
            LegislatorTerm,
            and_(
                LegislatorTerm.legislator_id == Vote.legislator_id,
                LegislatorTerm.start_date <= func.date(VotingSession.voting_date),
                or_(
                    LegislatorTerm.end_date.is_(None),
                    LegislatorTerm.end_date >= func.date(VotingSession.voting_date),
                ),
            ),
        )
        .filter(
            VotingSession.voting_date >= cutoff,
            Vote.vote.in_([VoteChoice.FOR, VoteChoice.AGAINST, VoteChoice.ABSTAIN]),
            LegislatorTerm.party_id.isnot(None),
        )
        .all()
    )

    if not rows:
        return None

    by_pair: dict[tuple[int, int], list[VoteChoice]] = defaultdict(list)
    for session_id, party_id, vote in rows:
        by_pair[(session_id, party_id)].append(vote)

    cohesions: list[float] = []
    for votes in by_pair.values():
        if len(votes) < QUIEBRE_PARTY_MIN_MEMBERS:
            continue
        counts = Counter(votes)
        _, top = counts.most_common(1)[0]
        cohesions.append(top / len(votes))

    if not cohesions:
        return None
    return sum(cohesions) / len(cohesions)


def compute_signals_for_session(
    db: Session, session: VotingSession
) -> list[SignalResult]:
    """Run every signal computation for one session."""
    results: list[SignalResult] = []
    dividida = compute_votacion_dividida(session)
    if dividida is not None:
        results.append(dividida)
    bajo_registro = compute_bajo_registro(db, session)
    if bajo_registro is not None:
        results.append(bajo_registro)
    quiebre = compute_quiebre_bloque(db, session)
    if quiebre is not None:
        results.append(quiebre)
    divergencia = compute_divergencia_camaras(db, session)
    if divergencia is not None:
        results.append(divergencia)
    return results


def persist_signals(
    db: Session, voting_session_id: int, signals: list[SignalResult]
) -> int:
    """Replace any prior signals for the session with the fresh set.

    Idempotent: safe to call repeatedly for the same session. Always deletes
    first, then inserts — even an empty signal list is meaningful (it clears
    stale rows after a threshold change).
    """
    db.query(VotingSessionSignal).filter(
        VotingSessionSignal.voting_session_id == voting_session_id
    ).delete(synchronize_session=False)

    for sig in signals:
        db.add(
            VotingSessionSignal(
                voting_session_id=voting_session_id,
                signal_type=sig.signal_type,
                severity=sig.severity,
                payload=sig.payload,
            )
        )
    db.flush()
    return len(signals)


def recompute_session_signals(db: Session, voting_session_id: int) -> int:
    """Top-level entry: load the session, compute, persist. Returns the
    number of signals fired (0 if the session is unknown or none fire).
    """
    session = (
        db.query(VotingSession)
        .options(joinedload(VotingSession.chamber))
        .filter(VotingSession.id == voting_session_id)
        .first()
    )
    if session is None:
        return 0
    signals = compute_signals_for_session(db, session)
    return persist_signals(db, voting_session_id, signals)


# ── Highlighted selection (for /voting-sessions/highlighted) ───────────────


def _window_cutoff(window_days: int) -> datetime:
    return datetime.now() - timedelta(days=window_days)


def select_highlighted(
    db: Session, window_days: int = DEFAULT_WINDOW_DAYS
) -> dict[str, Any]:
    """Pick the destacada hero, grid, and fallback for the page.

    Returns a dict shaped like the ``HighlightedResponse`` schema:
      - ``primary``: top-severity signal in the window (or ``None``)
      - ``grid``: next signals by severity (capped)
      - ``fallback_high_turnout``: always present; used by the UI when
        ``primary`` is ``None`` to show high-turnout sessions instead
    """
    cutoff = _window_cutoff(window_days)
    rows: list[VotingSessionSignal] = (
        db.query(VotingSessionSignal)
        .join(VotingSession, VotingSession.id == VotingSessionSignal.voting_session_id)
        .options(
            joinedload(VotingSessionSignal.voting_session).joinedload(
                VotingSession.chamber
            ),
            joinedload(VotingSessionSignal.voting_session).joinedload(
                VotingSession.bill
            ),
        )
        .filter(VotingSession.voting_date >= cutoff)
        .order_by(VotingSessionSignal.severity.desc())
        .limit(1 + HIGHLIGHTED_GRID_LIMIT)
        .all()
    )

    primary = rows[0] if rows else None
    grid = rows[1 : 1 + HIGHLIGHTED_GRID_LIMIT]

    fallback = (
        db.query(VotingSession)
        .options(joinedload(VotingSession.chamber), joinedload(VotingSession.bill))
        .filter(VotingSession.voting_date >= cutoff)
        .order_by((VotingSession.votes_for + VotingSession.votes_against).desc())
        .limit(FALLBACK_HIGH_TURNOUT_LIMIT)
        .all()
    )

    return {
        "primary": primary,
        "grid": grid,
        "fallback_high_turnout": fallback,
    }


# ── Helpers for the aggregates service ─────────────────────────────────────


def count_active_signals(db: Session, window_days: int) -> int:
    """Number of distinct voting sessions in the window that have at least
    one fired signal. Used by ``VotingWindowAggregate.signals_active``.
    """
    cutoff = _window_cutoff(window_days)
    return (
        db.query(func.count(func.distinct(VotingSessionSignal.voting_session_id)))
        .join(
            VotingSession,
            VotingSession.id == VotingSessionSignal.voting_session_id,
        )
        .filter(VotingSession.voting_date >= cutoff)
        .scalar()
        or 0
    )


def approval_rate(db: Session, window_days: int) -> float:
    """Share of decided sessions in the window that were approved."""
    cutoff = _window_cutoff(window_days)
    decided = (
        db.query(func.count(VotingSession.id))
        .filter(
            VotingSession.voting_date >= cutoff,
            VotingSession.result.isnot(None),
        )
        .scalar()
        or 0
    )
    if decided == 0:
        return 0.0
    approved = (
        db.query(func.count(VotingSession.id))
        .filter(
            VotingSession.voting_date >= cutoff,
            VotingSession.result == VotingResult.APPROVED,
        )
        .scalar()
        or 0
    )
    return approved / decided


def avg_participation(db: Session, window_days: int) -> float:
    """Average ``(votes_for + votes_against + abstentions) / total_seats``
    across sessions in the window. Excludes paired, dispensed, and no-vote rows.
    """
    cutoff = _window_cutoff(window_days)
    rows = (
        db.query(
            VotingSession.votes_for,
            VotingSession.votes_against,
            VotingSession.abstentions,
            Chamber.total_seats,
        )
        .join(Chamber, Chamber.id == VotingSession.chamber_id)
        .filter(
            VotingSession.voting_date >= cutoff,
            Chamber.total_seats > 0,
        )
        .all()
    )
    if not rows:
        return 0.0
    ratios = [
        (for_ + against + abstain) / seats for for_, against, abstain, seats in rows
    ]
    return sum(ratios) / len(ratios)


def session_volume(db: Session, window_days: int) -> int:
    cutoff = _window_cutoff(window_days)
    return (
        db.query(func.count(VotingSession.id))
        .filter(VotingSession.voting_date >= cutoff)
        .scalar()
        or 0
    )


# ── Window aggregate (writes VotingWindowAggregate) ────────────────────────


def build_window_aggregate_payload(db: Session, window_days: int) -> dict[str, Any]:
    """Compute the JSON payload stored in ``VotingWindowAggregate.payload``."""
    cohesion_val = avg_cohesion(db, window_days)
    return {
        "approval_rate": round(approval_rate(db, window_days), 4),
        "avg_cohesion": (round(cohesion_val, 4) if cohesion_val is not None else None),
        "avg_participation": round(avg_participation(db, window_days), 4),
        "volume": session_volume(db, window_days),
        "signals_active": count_active_signals(db, window_days),
    }


def refresh_window_aggregate(
    db: Session, window_days: int = DEFAULT_WINDOW_DAYS
) -> VotingWindowAggregate:
    """Replace the cached aggregate row for ``window_days`` with a fresh
    computation. Returns the persisted row.
    """
    payload = build_window_aggregate_payload(db, window_days)
    row = (
        db.query(VotingWindowAggregate)
        .filter(VotingWindowAggregate.window_days == window_days)
        .first()
    )
    if row is None:
        row = VotingWindowAggregate(window_days=window_days, payload=payload)
        db.add(row)
    else:
        row.payload = payload
    db.flush()
    return row


def get_window_aggregate(
    db: Session, window_days: int = DEFAULT_WINDOW_DAYS
) -> VotingWindowAggregate | None:
    return (
        db.query(VotingWindowAggregate)
        .filter(VotingWindowAggregate.window_days == window_days)
        .first()
    )


# ── Backfill and seed (CLI-facing) ─────────────────────────────────────────


def backfill_signals(db: Session, since: date | None = None) -> dict[str, int]:
    """Recompute signals for every voting session since ``since``.

    Run once after deploy to populate the table for historical data, and
    again whenever a threshold constant changes. Callers are responsible
    for committing.
    """
    query = db.query(VotingSession.id).order_by(VotingSession.voting_date.asc())
    if since is not None:
        cutoff = datetime.combine(since, time.min)
        query = query.filter(VotingSession.voting_date >= cutoff)
    session_ids = [row.id for row in query.all()]

    fired_total = 0
    for sid in session_ids:
        fired_total += recompute_session_signals(db, sid)
    return {"sessions_scanned": len(session_ids), "signals_fired": fired_total}


def seed_signal_fixtures(db: Session, base_date: date | None = None) -> dict[str, Any]:
    """Insert hand-crafted ``VotingSession`` rows that exercise each signal.

    Used for local-dev visual review of the destacada hero variants. Skips
    silently if the chamber roster isn't seeded yet. Returns the ids of the
    rows created and the signals fired so callers can confirm.

    PR-1: covers votación dividida + bajo registro. PR-2 will extend this
    function with quiebre + divergencia fixtures.
    """
    base = base_date or (date.today() - timedelta(days=1))

    deputies = (
        db.query(Chamber).filter(Chamber.chamber_type == ChamberType.DEPUTIES).first()
    )
    if deputies is None:
        return {"skipped": "deputies chamber not seeded", "created": []}

    created: list[dict[str, Any]] = []

    dividida = VotingSession(
        bcn_id=f"fixture-dividida-{base.isoformat()}",
        chamber_id=deputies.id,
        voting_date=datetime.combine(base, time(15, 30)),
        subject="Fixture · votación dividida — reforma de pensiones",
        result=VotingResult.APPROVED,
        votes_for=78,
        votes_against=73,
        abstentions=2,
        no_votes=2,
    )
    db.add(dividida)
    db.flush()
    fired = recompute_session_signals(db, dividida.id)
    created.append(
        {
            "signal": "votacion_dividida",
            "voting_session_id": dividida.id,
            "signals_fired": fired,
        }
    )

    bajo_registro = VotingSession(
        bcn_id=f"fixture-bajo-registro-{base.isoformat()}",
        chamber_id=deputies.id,
        voting_date=datetime.combine(base, time(11, 0)),
        subject="Fixture · bajo registro — modificación al código tributario",
        result=VotingResult.REJECTED,
        votes_for=45,
        votes_against=60,
        abstentions=5,
        no_votes=45,
    )
    db.add(bajo_registro)
    db.flush()
    fired = recompute_session_signals(db, bajo_registro.id)
    created.append(
        {
            "signal": "bajo_registro",
            "voting_session_id": bajo_registro.id,
            "signals_fired": fired,
        }
    )

    divergencia_extra = _seed_divergencia_fixture(db, base, deputies)
    if divergencia_extra is not None:
        created.append(divergencia_extra)

    # Quiebre de bloque requires a party with ≥5 voting members and
    # LegislatorTerm rows covering the vote date. That setup is environment-
    # dependent; the cleanest way to see this signal locally is to run
    # `voting-signals backfill` against real ingested data.

    return {
        "created": created,
        "note": (
            "Run `python -m app.cli voting-signals backfill` to surface "
            "quiebre_bloque on real ingested data."
        ),
    }


def _seed_divergencia_fixture(
    db: Session, base: date, deputies: Chamber
) -> dict[str, Any] | None:
    """Create two sessions on the same bill with different results."""
    from app.models.proyecto import Bill

    senate = (
        db.query(Chamber).filter(Chamber.chamber_type == ChamberType.SENATE).first()
    )
    if senate is None:
        return None

    bill = db.query(Bill).first()
    if bill is None:
        return None

    earlier = VotingSession(
        bcn_id=f"fixture-divergencia-deputies-{base.isoformat()}",
        chamber_id=deputies.id,
        bill_id=bill.id,
        voting_date=datetime.combine(base - timedelta(days=10), time(10, 0)),
        subject=f"Fixture · divergencia (Cámara) — {bill.title[:60]}",
        result=VotingResult.APPROVED,
        votes_for=88,
        votes_against=30,
        abstentions=4,
        no_votes=33,
    )
    later = VotingSession(
        bcn_id=f"fixture-divergencia-senate-{base.isoformat()}",
        chamber_id=senate.id,
        bill_id=bill.id,
        voting_date=datetime.combine(base, time(16, 0)),
        subject=f"Fixture · divergencia (Senado) — {bill.title[:60]}",
        result=VotingResult.REJECTED,
        votes_for=18,
        votes_against=26,
        abstentions=2,
        no_votes=4,
    )
    db.add_all([earlier, later])
    db.flush()
    fired = recompute_session_signals(db, later.id)
    # Also recompute the earlier one (won't fire — it's the older session).
    recompute_session_signals(db, earlier.id)
    return {
        "signal": "divergencia_camaras",
        "voting_session_id": later.id,
        "counterpart_voting_session_id": earlier.id,
        "signals_fired": fired,
    }

"""Per-legislator voting stats: base aggregates + inclinación de voto + disciplina.

Populates the (otherwise dormant) ``LegislatorVotingStats`` table. Three things
per legislator, refreshed out-of-band by a Celery beat task in the mold of
``voting_signals`` / ``VotingWindowAggregate``:

  - **base stats** (career-wide): totals/record-rate/participation, the batched
    form of ``legislators.get_legislator_voting_summary``.
  - **inclinación de voto** (current period): the bloc whose modal vote the
    legislator matched most often across *contested, decisive* sessions — those
    where oficialismo and oposición took opposite sides and the legislator voted
    for/against. Seats an independent in the simulator only above a margin.
  - **disciplina partidaria** (current period): how often a party member voted
    with their own party's modal.

The math lives in pure functions (unit-tested in ``tests/test_legislator_stats``);
the DB fetch is thin (integration-tested). A bloc's position in a past session is
the modal of its members at the session date — party-at-date (``LegislatorTerm``,
as ``compute_quiebre_bloque`` does) mapped through each party's *current* bloc,
since ADR-0014 exposes only ``current_bloc``. See web ``CONTEXT.md``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Iterable

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, selectinload

from app.models.enums import Bloc, VoteChoice
from app.models.legislature import (
    LegislativePeriod,
    Legislator,
    LegislatorTerm,
    PoliticalParty,
)
from app.models.votacion import LegislatorVotingStats, Vote, VotingSession

# ── Thresholds (Python constants, beside the voting_signals thresholds) ──────

LEAN_MIN_CONTESTED = 4
"""Minimum contested, decisive sessions before an inclinación is reported at
all (below this: *datos insuficientes*)."""

LEAN_SEAT_MARGIN = 0.60
"""Share of contested sessions on the leading bloc required to *seat* an
independent in the simulator. Below it the lean is shown but the seat stays
*sin alinear*."""

DISCIPLINE_MIN_SESSIONS = 4
"""Minimum decided sessions (party had a clear modal) before a disciplina
rate is reported."""

PARTY_MIN_MEMBERS = 5
"""Minimum party members voting in a session for that party's modal to count —
mirrors ``voting_signals.QUIEBRE_PARTY_MIN_MEMBERS``."""

DECISIVE: tuple[VoteChoice, ...] = (VoteChoice.FOR, VoteChoice.AGAINST)
"""Votes that pick a side — the only ones the bloc modal and the lean count."""

PARTY_CHOICES: tuple[VoteChoice, ...] = (
    VoteChoice.FOR,
    VoteChoice.AGAINST,
    VoteChoice.ABSTAIN,
)
"""Votes the party modal / disciplina consider — mirrors the cohesión vote set."""


# ── Pure tallying logic ──────────────────────────────────────────────────────


def modal_choice(
    choices: Iterable[VoteChoice], allowed: tuple[VoteChoice, ...]
) -> VoteChoice | None:
    """The single most common choice among ``allowed``; ``None`` if there are
    no qualifying votes or the top two tie (no clear position)."""
    counts = Counter(c for c in choices if c in allowed)
    if not counts:
        return None
    ranked = counts.most_common()
    top_choice, top_n = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top_n:
        return None
    return top_choice


@dataclass(frozen=True)
class LeanResult:
    bloc: Bloc | None  # None when contested sessions split evenly
    agreed: int  # matches with the leading bloc (K)
    contested: int  # contested decisive sessions the legislator voted in (N)
    seats: bool  # clears LEAN_SEAT_MARGIN → may seed the simulator


def compute_lean(
    sessions: Iterable[tuple[VoteChoice | None, VoteChoice | None, VoteChoice]],
) -> LeanResult | None:
    """Inclinación de voto from ``(oficialismo_modal, oposicion_modal, choice)``
    triples. Counts only *contested* sessions (both modals present and opposed)
    where the legislator cast a *decisive* vote. ``None`` below the minimum
    sample (*datos insuficientes*)."""
    agreed_ofi = 0
    agreed_opo = 0
    for ofi_modal, opo_modal, choice in sessions:
        if ofi_modal is None or opo_modal is None or ofi_modal == opo_modal:
            continue  # not contested → reveals nothing
        if choice not in DECISIVE:
            continue  # abstention/absence picks no side
        if choice == ofi_modal:
            agreed_ofi += 1
        elif choice == opo_modal:
            agreed_opo += 1

    contested = agreed_ofi + agreed_opo
    if contested < LEAN_MIN_CONTESTED:
        return None
    if agreed_ofi == agreed_opo:
        return LeanResult(
            bloc=None, agreed=agreed_ofi, contested=contested, seats=False
        )
    if agreed_ofi > agreed_opo:
        bloc, agreed = Bloc.OFICIALISMO, agreed_ofi
    else:
        bloc, agreed = Bloc.OPOSICION, agreed_opo
    return LeanResult(
        bloc=bloc,
        agreed=agreed,
        contested=contested,
        seats=(agreed / contested) >= LEAN_SEAT_MARGIN,
    )


@dataclass(frozen=True)
class DisciplineResult:
    rate: float  # fraction 0..1 of decided sessions voted with the party
    with_party: int
    decided: int


def compute_discipline(
    sessions: Iterable[tuple[VoteChoice | None, VoteChoice]],
) -> DisciplineResult | None:
    """Disciplina partidaria from ``(party_modal, choice)`` pairs. Counts
    sessions where the party had a clear modal and the legislator voted
    for/against/abstain. ``None`` below the minimum sample."""
    matches = 0
    decided = 0
    for party_modal, choice in sessions:
        if party_modal is None:
            continue
        if choice not in PARTY_CHOICES:
            continue
        decided += 1
        if choice == party_modal:
            matches += 1
    if decided < DISCIPLINE_MIN_SESSIONS:
        return None
    return DisciplineResult(rate=matches / decided, with_party=matches, decided=decided)


# ── DB orchestration ─────────────────────────────────────────────────────────


def _start_of_day(value: date) -> datetime:
    return datetime.combine(value, time.min)


def _current_period_start(db: Session) -> date | None:
    """Start date of the legislative period in effect today (latest period whose
    ``start_date`` is on or before today). ``None`` if no period is seeded."""
    today = date.today()
    period = (
        db.query(LegislativePeriod)
        .filter(LegislativePeriod.start_date <= today)
        .order_by(LegislativePeriod.start_date.desc())
        .first()
    )
    return period.start_date if period else None


def _base_stats(db: Session) -> dict[int, dict]:
    """Career-wide per-legislator aggregates, batched (the set form of
    ``legislators.get_legislator_voting_summary``)."""

    def _sum(choice: VoteChoice):
        return func.coalesce(func.sum(case((Vote.vote == choice, 1), else_=0)), 0)

    # Orphan votes (legislator_id IS NULL, per ADR-0015) carry a chamber
    # bridge but no resolved legislator yet — the reconciler attaches them
    # when the matching LegislatorTerm arrives. They are excluded from
    # per-legislator stats; the next refresh after resolution picks them up.
    rows = (
        db.query(
            Vote.legislator_id.label("legislator_id"),
            func.count(Vote.id).label("total"),
            _sum(VoteChoice.FOR).label("votes_for"),
            _sum(VoteChoice.AGAINST).label("votes_against"),
            _sum(VoteChoice.ABSTAIN).label("abstentions"),
            _sum(VoteChoice.NO_VOTE).label("no_votes"),
        )
        .filter(Vote.legislator_id.isnot(None))
        .group_by(Vote.legislator_id)
        .all()
    )

    stats: dict[int, dict] = {}
    for row in rows:
        total = int(row.total or 0)
        votes_for = int(row.votes_for or 0)
        votes_against = int(row.votes_against or 0)
        abstentions = int(row.abstentions or 0)
        no_votes = int(row.no_votes or 0)
        record_rate = round((total - no_votes) / total * 100, 2) if total else 0.0
        participation = (
            round((votes_for + votes_against + abstentions) / total * 100, 2)
            if total
            else 0.0
        )
        stats[int(row.legislator_id)] = {
            "total_sessions": total,
            "votes_for": votes_for,
            "votes_against": votes_against,
            "abstentions": abstentions,
            "no_votes": no_votes,
            "record_rate": record_rate,
            "participation_rate": participation,
        }
    return stats


def _period_votes(
    db: Session, period_start: date | None
) -> list[tuple[int, int, int | None, VoteChoice]]:
    """``(session_id, legislator_id, party_id_at_date, vote)`` for every
    for/against/abstain vote since ``period_start``. Party-at-date via a LEFT
    join on ``LegislatorTerm`` so independents (no term) come through with a
    null party."""
    query = (
        db.query(
            Vote.voting_session_id,
            Vote.legislator_id,
            LegislatorTerm.party_id,
            Vote.vote,
        )
        .join(VotingSession, VotingSession.id == Vote.voting_session_id)
        .outerjoin(
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
        # Orphan votes (legislator_id IS NULL, per ADR-0015) carry no
        # attributable legislator and would pollute the per-legislator
        # aggregations downstream. The reconciler claims them when the
        # matching LegislatorTerm arrives; the next refresh picks them up.
        .filter(Vote.legislator_id.isnot(None))
        .filter(Vote.vote.in_(PARTY_CHOICES))
    )
    if period_start is not None:
        query = query.filter(VotingSession.voting_date >= _start_of_day(period_start))
    return [
        (session_id, legislator_id, party_id, vote)
        for session_id, legislator_id, party_id, vote in query.all()
    ]


def _bloc_maps(db: Session) -> tuple[dict[int, Bloc | None], dict[int, Bloc | None]]:
    """``(party_id → current_bloc, legislator_id → default_bloc)``."""
    parties = (
        db.query(PoliticalParty)
        .options(selectinload(PoliticalParty.bloc_affiliations))
        .all()
    )
    party_bloc = {p.id: p.current_bloc for p in parties}
    default_bloc = {
        lid: bloc
        for lid, bloc in db.query(Legislator.id, Legislator.default_bloc).all()
    }
    return party_bloc, default_bloc


def refresh_legislator_voting_stats(db: Session) -> int:
    """Recompute and upsert one ``LegislatorVotingStats`` row per legislator with
    votes. Replaces prior values wholesale. Caller commits. Returns the row count.
    """
    base = _base_stats(db)
    period_start = _current_period_start(db)
    party_bloc, default_bloc = _bloc_maps(db)
    today = date.today()
    current_party = {
        lid: pid
        for lid, pid in db.query(LegislatorTerm.legislator_id, LegislatorTerm.party_id)
        .filter(
            LegislatorTerm.start_date <= today,
            or_(LegislatorTerm.end_date.is_(None), LegislatorTerm.end_date >= today),
        )
        .order_by(LegislatorTerm.legislator_id, LegislatorTerm.start_date.desc())
        .all()
    }

    def voter_bloc(legislator_id: int, party_id: int | None) -> Bloc | None:
        if party_id is not None:
            return party_bloc.get(party_id)
        return default_bloc.get(legislator_id)

    # Group the period votes once.
    bloc_choices: dict[int, dict[Bloc, list[VoteChoice]]] = defaultdict(
        lambda: defaultdict(list)
    )
    party_choices: dict[int, dict[int, list[VoteChoice]]] = defaultdict(
        lambda: defaultdict(list)
    )
    leg_votes: dict[int, list[tuple[int, int | None, VoteChoice]]] = defaultdict(list)
    seen: set[tuple[int, int]] = set()
    for session_id, legislator_id, party_id, vote in _period_votes(db, period_start):
        key = (session_id, legislator_id)
        if key in seen:  # guard against overlapping LegislatorTerm rows
            continue
        seen.add(key)
        leg_votes[legislator_id].append((session_id, party_id, vote))
        bloc = voter_bloc(legislator_id, party_id)
        if bloc is not None:
            bloc_choices[session_id][bloc].append(vote)
        if party_id is not None:
            party_choices[session_id][party_id].append(vote)

    # Per-session modals.
    ofi_modal: dict[int, VoteChoice | None] = {}
    opo_modal: dict[int, VoteChoice | None] = {}
    for session_id, by_bloc in bloc_choices.items():
        ofi_modal[session_id] = modal_choice(
            by_bloc.get(Bloc.OFICIALISMO, []), DECISIVE
        )
        opo_modal[session_id] = modal_choice(by_bloc.get(Bloc.OPOSICION, []), DECISIVE)
    party_modal: dict[tuple[int, int], VoteChoice | None] = {}
    for session_id, by_party in party_choices.items():
        for party_id, choices in by_party.items():
            party_modal[(session_id, party_id)] = (
                modal_choice(choices, PARTY_CHOICES)
                if len(choices) >= PARTY_MIN_MEMBERS
                else None
            )

    # Per-legislator lean + discipline.
    leans: dict[int, LeanResult | None] = {}
    disciplines: dict[int, DisciplineResult | None] = {}
    for legislator_id, votes in leg_votes.items():
        leans[legislator_id] = compute_lean(
            (ofi_modal.get(s), opo_modal.get(s), v) for s, _p, v in votes
        )
        if current_party.get(legislator_id) is not None:
            disciplines[legislator_id] = compute_discipline(
                (party_modal.get((s, p)), v) for s, p, v in votes if p is not None
            )

    # Upsert.
    existing = {r.legislator_id: r for r in db.query(LegislatorVotingStats).all()}
    now = datetime.now()
    for legislator_id, stats in base.items():
        row = existing.get(legislator_id)
        if row is None:
            row = LegislatorVotingStats(legislator_id=legislator_id)
            db.add(row)
        row.total_sessions = stats["total_sessions"]
        row.votes_for = stats["votes_for"]
        row.votes_against = stats["votes_against"]
        row.abstentions = stats["abstentions"]
        row.no_votes = stats["no_votes"]
        row.record_rate = stats["record_rate"]
        row.participation_rate = stats["participation_rate"]

        lean = leans.get(legislator_id)
        row.inferred_bloc = lean.bloc if lean else None
        row.lean_agreed = lean.agreed if lean else 0
        row.lean_contested = lean.contested if lean else 0
        row.lean_seats = bool(lean.seats) if lean else False

        discipline = disciplines.get(legislator_id)
        row.discipline_rate = round(discipline.rate * 100, 2) if discipline else None
        row.discipline_with = discipline.with_party if discipline else 0
        row.discipline_decided = discipline.decided if discipline else 0

        row.stats_updated_at = now

    db.flush()
    return len(base)

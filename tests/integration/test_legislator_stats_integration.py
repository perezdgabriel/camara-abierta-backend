"""DB-dependent tests for ``refresh_legislator_voting_stats``.

Lives under ``tests/integration/`` because the ORM relies on a PostgreSQL
sequence for ``sync_version`` that the in-memory SQLite default can't compile.
Run with ``just test-integration``. The pure tallying logic is covered in
``tests/test_legislator_stats.py``. See ADR-0014.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.orm import Session

from app.models.enums import Bloc, ChamberType, VoteChoice, VotingResult, VotingType
from app.models.legislature import (
    BlocAffiliation,
    Chamber,
    LegislativePeriod,
    Legislator,
    LegislatorTerm,
    PoliticalParty,
)
from app.models.votacion import LegislatorVotingStats, Vote, VotingSession
from app.schemas.legislators import LegislatorSummary
from app.services import legislator_stats as ls

pytestmark = pytest.mark.integration

PERIOD_START = date(2026, 3, 11)


# ── Builders ────────────────────────────────────────────────────────────────


def _make_chamber(db: Session, seats: int = 155) -> Chamber:
    chamber = Chamber(
        chamber_type=ChamberType.DEPUTIES, name="Cámara", total_seats=seats
    )
    db.add(chamber)
    db.flush()
    return chamber


def _make_period(db: Session) -> LegislativePeriod:
    period = LegislativePeriod(
        number=56, start_date=PERIOD_START, end_date=date(2030, 3, 11)
    )
    db.add(period)
    db.flush()
    return period


def _make_party(db: Session, name: str, abbr: str, bloc: Bloc) -> PoliticalParty:
    party = PoliticalParty(name=name, abbreviation=abbr, color="#123456")
    db.add(party)
    db.flush()
    db.add(
        BlocAffiliation(
            party_id=party.id, bloc=bloc, start_date=PERIOD_START, end_date=None
        )
    )
    db.flush()
    return party


def _make_member(
    db: Session,
    *,
    chamber: Chamber,
    period: LegislativePeriod,
    party: PoliticalParty,
    name: str,
) -> Legislator:
    leg = Legislator(
        first_name=name,
        last_name="Demo",
        full_name=f"{name} Demo",
        chamber_type=chamber.chamber_type,
        party_id=party.id,
    )
    db.add(leg)
    db.flush()
    db.add(
        LegislatorTerm(
            legislator_id=leg.id,
            period_id=period.id,
            chamber_id=chamber.id,
            party_id=party.id,
            start_date=PERIOD_START,
            end_date=None,
        )
    )
    db.flush()
    return leg


def _make_independent(db: Session, *, chamber: Chamber, name: str) -> Legislator:
    leg = Legislator(
        first_name=name,
        last_name="Indep",
        full_name=f"{name} Indep",
        chamber_type=chamber.chamber_type,
        party_id=None,
    )
    db.add(leg)
    db.flush()
    return leg


def _session(db: Session, chamber: Chamber, day: int) -> VotingSession:
    row = VotingSession(
        chamber_id=chamber.id,
        voting_type=VotingType.GENERAL,
        subject=f"Contested session {day}",
        voting_date=datetime(2026, 5, day, 15, 0),
        result=VotingResult.APPROVED,
        votes_for=6,
        votes_against=7,
    )
    db.add(row)
    db.flush()
    return row


def _cast(db: Session, session: VotingSession, legs, vote: VoteChoice) -> None:
    for leg in legs:
        db.add(Vote(voting_session_id=session.id, legislator_id=leg.id, vote=vote))
    db.flush()


def _stats(db: Session, legislator_id: int) -> LegislatorVotingStats:
    return (
        db.query(LegislatorVotingStats)
        .filter(LegislatorVotingStats.legislator_id == legislator_id)
        .one()
    )


# ── Tests ─────────────────────────────────────────────────────────────────


class TestRefreshLegislatorVotingStats:
    def _scenario(self, db: Session, *, n_contested: int):
        """Oficialismo (FOR) vs oposición (AGAINST) across ``n_contested``
        sessions; one independent votes AGAINST every time."""
        chamber = _make_chamber(db)
        period = _make_period(db)
        ofi = _make_party(db, "Partido Gobierno", "PG", Bloc.OFICIALISMO)
        opo = _make_party(db, "Partido Oposición", "PO", Bloc.OPOSICION)
        ofi_members = [
            _make_member(db, chamber=chamber, period=period, party=ofi, name=f"G{i}")
            for i in range(6)
        ]
        opo_members = [
            _make_member(db, chamber=chamber, period=period, party=opo, name=f"O{i}")
            for i in range(6)
        ]
        ada = _make_independent(db, chamber=chamber, name="Ada")
        for day in range(1, n_contested + 1):
            session = _session(db, chamber, day)
            _cast(db, session, ofi_members, VoteChoice.FOR)
            _cast(db, session, opo_members, VoteChoice.AGAINST)
            _cast(db, session, [ada], VoteChoice.AGAINST)
        return chamber, ofi_members, opo_members, ada

    def test_seeds_independent_and_scores_members(self, db_session: Session):
        _chamber, ofi_members, _opo, ada = self._scenario(db_session, n_contested=5)

        count = ls.refresh_legislator_voting_stats(db_session)
        assert count == 13  # 12 members + 1 independent

        # Independent leans oposición strongly enough to seat in the simulator.
        ada_stats = _stats(db_session, ada.id)
        assert ada_stats.inferred_bloc is Bloc.OPOSICION
        assert ada_stats.lean_contested == 5
        assert ada_stats.lean_agreed == 5
        assert ada_stats.lean_seats is True
        assert ada_stats.total_sessions == 5  # base stats populated
        # No party → no discipline.
        assert ada.party_discipline is None
        assert ada.voting_lean["bloc"] is Bloc.OPOSICION
        assert ada.voting_lean["seats"] is True
        # The actual API path: the property's dict must validate into the nested
        # schema via from_attributes.
        summary = LegislatorSummary.model_validate(ada)
        assert summary.voting_lean is not None
        assert summary.voting_lean.bloc is Bloc.OPOSICION
        assert summary.voting_lean.seats is True

        # An oficialismo member matched their own bloc modal every contested
        # session → leans oficialismo; party discipline is 100%.
        member_stats = _stats(db_session, ofi_members[0].id)
        assert member_stats.inferred_bloc is Bloc.OFICIALISMO
        assert member_stats.discipline_decided == 5
        assert float(member_stats.discipline_rate) == 100.0
        assert ofi_members[0].party_discipline["decided"] == 5
        assert ofi_members[0].party_discipline["rate"] == 100.0

    def test_insufficient_sample_leaves_lean_null(self, db_session: Session):
        # 3 contested sessions < LEAN_MIN_CONTESTED → datos insuficientes.
        _chamber, _ofi, _opo, ada = self._scenario(db_session, n_contested=3)

        ls.refresh_legislator_voting_stats(db_session)

        ada_stats = _stats(db_session, ada.id)
        assert ada_stats.inferred_bloc is None
        assert ada_stats.lean_contested == 0
        assert ada_stats.lean_seats is False
        assert ada.voting_lean is None  # property hides insufficient data

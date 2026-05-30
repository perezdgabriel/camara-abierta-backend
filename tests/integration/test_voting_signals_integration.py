"""DB-dependent threshold tests for the voting signals.

Lives under ``tests/integration/`` because the ORM relies on a PostgreSQL
sequence for ``sync_version`` that the in-memory SQLite default can't
compile. Run with ``just test-integration``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.enums import (
    BillOrigin,
    ChamberType,
    SignalType,
    VoteChoice,
    VotingResult,
    VotingType,
)
from app.models.legislature import (
    Chamber,
    LegislativePeriod,
    Legislator,
    LegislatorTerm,
    PoliticalParty,
)
from app.models.proyecto import Bill
from app.models.votacion import Vote, VotingSession
from app.services import voting_signals as vs

pytestmark = pytest.mark.integration


# ── Builders ───────────────────────────────────────────────────────────────


def _make_chamber(
    db: Session, chamber_type: ChamberType, name: str, total_seats: int = 155
) -> Chamber:
    chamber = Chamber(chamber_type=chamber_type, name=name, total_seats=total_seats)
    db.add(chamber)
    db.flush()
    return chamber


def _make_session(
    db: Session,
    chamber: Chamber,
    *,
    voting_date: datetime,
    subject: str = "Test session",
    votes_for: int = 0,
    votes_against: int = 0,
    abstentions: int = 0,
    absences: int = 0,
    result: VotingResult | None = VotingResult.APPROVED,
    bill_id: int | None = None,
) -> VotingSession:
    row = VotingSession(
        chamber_id=chamber.id,
        bill_id=bill_id,
        voting_type=VotingType.GENERAL,
        subject=subject,
        voting_date=voting_date,
        result=result,
        votes_for=votes_for,
        votes_against=votes_against,
        abstentions=abstentions,
        absences=absences,
    )
    db.add(row)
    db.flush()
    return row


def _make_party(db: Session, name: str, abbr: str) -> PoliticalParty:
    p = PoliticalParty(name=name, abbreviation=abbr, color="#123456")
    db.add(p)
    db.flush()
    return p


def _make_period(db: Session) -> LegislativePeriod:
    p = LegislativePeriod(
        number=56, start_date=date(2026, 3, 11), end_date=date(2030, 3, 11)
    )
    db.add(p)
    db.flush()
    return p


def _make_legislator_with_term(
    db: Session,
    *,
    chamber: Chamber,
    period: LegislativePeriod,
    party: PoliticalParty,
    first_name: str,
    last_name: str = "Demo",
) -> Legislator:
    leg = Legislator(
        first_name=first_name,
        last_name=last_name,
        full_name=f"{first_name} {last_name}",
        chamber_type=chamber.chamber_type,
        party_id=party.id,
    )
    db.add(leg)
    db.flush()
    term = LegislatorTerm(
        legislator_id=leg.id,
        period_id=period.id,
        chamber_id=chamber.id,
        party_id=party.id,
        start_date=date(2026, 3, 11),
        end_date=None,
    )
    db.add(term)
    db.flush()
    return leg


def _cast_votes(
    db: Session,
    session: VotingSession,
    legislators: list[Legislator],
    vote: VoteChoice,
) -> None:
    for leg in legislators:
        db.add(
            Vote(
                voting_session_id=session.id,
                legislator_id=leg.id,
                vote=vote,
            )
        )
    db.flush()


# ── compute_alto_ausentismo ────────────────────────────────────────────────


class TestAltoAusentismo:
    def test_fires_above_absence_threshold(self, db_session: Session):
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara", 155)
        session = _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 15, 11, 0),
            absences=45,  # 45/155 ≈ 29% > 20%
            votes_for=45,
            votes_against=60,
            abstentions=5,
            result=VotingResult.REJECTED,
        )
        result = vs.compute_alto_ausentismo(db_session, session)
        assert result is not None
        assert result.signal_type is SignalType.ALTO_AUSENTISMO
        assert result.payload["absences"] == 45
        assert result.payload["absence_rate"] > vs.ABSENCE_RATE_MIN

    def test_does_not_fire_below_threshold(self, db_session: Session):
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara", 155)
        session = _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 15, 11, 0),
            absences=20,  # 20/155 ≈ 13% < 20%
            votes_for=70,
            votes_against=60,
            abstentions=5,
        )
        assert vs.compute_alto_ausentismo(db_session, session) is None

    def test_baseline_computed_from_preceding_window(self, db_session: Session):
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara", 155)
        for d in (
            datetime(2026, 5, 1, 10, 0),
            datetime(2026, 5, 5, 10, 0),
        ):
            _make_session(db_session, deputies, voting_date=d, absences=10)
        _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 8, 10, 0),
            absences=20,
        )
        current = _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 15, 11, 0),
            absences=50,
            votes_for=50,
            votes_against=50,
            abstentions=5,
            result=VotingResult.REJECTED,
        )
        result = vs.compute_alto_ausentismo(db_session, current)
        assert result is not None
        # baseline = (10 + 10 + 20) / 3 ≈ 13.33
        assert result.payload["baseline_absences"] == pytest.approx(13.33, abs=0.01)


# ── compute_quiebre_bloque ─────────────────────────────────────────────────


class TestQuiebreBloque:
    def test_fires_when_party_below_cohesion_threshold(self, db_session: Session):
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara")
        period = _make_period(db_session)
        party = _make_party(db_session, "Partido Test", "PT")
        members = [
            _make_legislator_with_term(
                db_session,
                chamber=deputies,
                period=period,
                party=party,
                first_name=f"Leg{i}",
            )
            for i in range(10)
        ]
        session = _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 15, 11, 0),
            votes_for=7,
            votes_against=3,
        )
        # 7 FOR, 3 AGAINST → cohesion = 0.7 (< 0.80)
        _cast_votes(db_session, session, members[:7], VoteChoice.FOR)
        _cast_votes(db_session, session, members[7:], VoteChoice.AGAINST)

        result = vs.compute_quiebre_bloque(db_session, session)
        assert result is not None
        breaks = result.payload["parties_below_threshold"]
        assert len(breaks) == 1
        assert breaks[0]["party"]["abbreviation"] == "PT"
        assert breaks[0]["cohesion"] == pytest.approx(0.7, abs=0.001)
        assert len(breaks[0]["dissenters"]) == 3

    def test_does_not_fire_at_threshold(self, db_session: Session):
        # 8 of 10 voting majority = 0.80 cohesion → not strictly below.
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara")
        period = _make_period(db_session)
        party = _make_party(db_session, "Partido Test", "PT")
        members = [
            _make_legislator_with_term(
                db_session,
                chamber=deputies,
                period=period,
                party=party,
                first_name=f"Leg{i}",
            )
            for i in range(10)
        ]
        session = _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 15, 11, 0),
            votes_for=8,
            votes_against=2,
        )
        _cast_votes(db_session, session, members[:8], VoteChoice.FOR)
        _cast_votes(db_session, session, members[8:], VoteChoice.AGAINST)
        assert vs.compute_quiebre_bloque(db_session, session) is None

    def test_ignores_parties_below_member_floor(self, db_session: Session):
        # Party with 4 voting members never fires.
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara")
        period = _make_period(db_session)
        party = _make_party(db_session, "Partido Pequeño", "PP")
        members = [
            _make_legislator_with_term(
                db_session,
                chamber=deputies,
                period=period,
                party=party,
                first_name=f"Leg{i}",
            )
            for i in range(4)
        ]
        session = _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 15, 11, 0),
            votes_for=2,
            votes_against=2,
        )
        _cast_votes(db_session, session, members[:2], VoteChoice.FOR)
        _cast_votes(db_session, session, members[2:], VoteChoice.AGAINST)
        assert vs.compute_quiebre_bloque(db_session, session) is None


# ── compute_divergencia_camaras ────────────────────────────────────────────


class TestDivergenciaCamaras:
    def _make_bill(self, db: Session) -> Bill:
        bill = Bill(
            bulletin_number="99999-99",
            title="Proyecto test divergencia",
            origin=BillOrigin.EXECUTIVE,
            entry_date=date(2026, 4, 1),
        )
        db.add(bill)
        db.flush()
        return bill

    def test_fires_on_later_session_with_different_result(self, db_session: Session):
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara")
        senate = _make_chamber(db_session, ChamberType.SENATE, "Senado", 50)
        bill = self._make_bill(db_session)

        earlier = _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 1, 10, 0),
            bill_id=bill.id,
            result=VotingResult.APPROVED,
            votes_for=88,
            votes_against=30,
        )
        later = _make_session(
            db_session,
            senate,
            voting_date=datetime(2026, 5, 15, 10, 0),
            bill_id=bill.id,
            result=VotingResult.REJECTED,
            votes_for=18,
            votes_against=26,
        )

        assert vs.compute_divergencia_camaras(db_session, later) is not None
        # The earlier session must NOT fire (only the later one).
        assert vs.compute_divergencia_camaras(db_session, earlier) is None

    def test_does_not_fire_when_results_match(self, db_session: Session):
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara")
        senate = _make_chamber(db_session, ChamberType.SENATE, "Senado", 50)
        bill = self._make_bill(db_session)

        _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 1, 10, 0),
            bill_id=bill.id,
            result=VotingResult.APPROVED,
        )
        later = _make_session(
            db_session,
            senate,
            voting_date=datetime(2026, 5, 15, 10, 0),
            bill_id=bill.id,
            result=VotingResult.APPROVED,
        )
        assert vs.compute_divergencia_camaras(db_session, later) is None

    def test_does_not_fire_when_no_bill_id(self, db_session: Session):
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara")
        session = _make_session(
            db_session,
            deputies,
            voting_date=datetime(2026, 5, 15, 10, 0),
            bill_id=None,
        )
        assert vs.compute_divergencia_camaras(db_session, session) is None


# ── approval_rate aggregate ────────────────────────────────────────────────


class TestApprovalRate:
    def test_excludes_undecided_sessions(self, db_session: Session):
        deputies = _make_chamber(db_session, ChamberType.DEPUTIES, "Cámara")
        recent = datetime.now() - timedelta(days=2)
        for r in (
            VotingResult.APPROVED,
            VotingResult.APPROVED,
            VotingResult.REJECTED,
        ):
            _make_session(db_session, deputies, voting_date=recent, result=r)
        _make_session(db_session, deputies, voting_date=recent, result=None)

        rate = vs.approval_rate(db_session, window_days=30)
        assert rate == pytest.approx(2 / 3)

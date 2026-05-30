"""Pure-function threshold tests for ``compute_votacion_dividida``.

DB-dependent signal tests (alto ausentismo, quiebre, divergencia) live in
``tests/integration/test_voting_signals_integration.py`` because the ORM uses
PostgreSQL sequences that SQLite can't compile.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.models.enums import SignalType, VotingResult
from app.services import voting_signals as vs


def _session(
    *,
    for_: int,
    against: int,
    abstentions: int = 0,
    absences: int = 0,
    total_seats: int = 155,
    result: VotingResult | None = VotingResult.APPROVED,
) -> SimpleNamespace:
    """Build the minimum object shape consumed by ``compute_votacion_dividida``."""
    return SimpleNamespace(
        votes_for=for_,
        votes_against=against,
        abstentions=abstentions,
        absences=absences,
        result=result,
        chamber=SimpleNamespace(total_seats=total_seats),
    )


class TestVotacionDividida:
    def test_fires_below_margin_threshold(self):
        # 78-73 → margin 5, ratio 5/151 ≈ 3.3% (below 10%);
        # participation 151/155 ≈ 97% (above 60%).
        result = vs.compute_votacion_dividida(_session(for_=78, against=73))
        assert result is not None
        assert result.signal_type is SignalType.VOTACION_DIVIDIDA
        assert result.payload["margin"] == 5

    def test_does_not_fire_at_margin_threshold(self):
        # 110-90 → margin 20, ratio 20/200 = 0.10 exactly. Threshold is strict <.
        assert vs.compute_votacion_dividida(_session(for_=110, against=90)) is None

    def test_does_not_fire_below_participation_floor(self):
        # Margin tight (36-34) but participation 70/155 ≈ 45% < 60%.
        assert vs.compute_votacion_dividida(_session(for_=36, against=34)) is None

    def test_fires_just_above_participation_floor(self):
        # Margin tight (48-46) and participation 94/155 ≈ 60.6% > 60%.
        result = vs.compute_votacion_dividida(_session(for_=48, against=46))
        assert result is not None

    def test_symmetric_on_approved_and_rejected(self):
        # A 78-73 approval and a 73-78 rejection both fire identically.
        approved = vs.compute_votacion_dividida(
            _session(for_=78, against=73, result=VotingResult.APPROVED)
        )
        rejected = vs.compute_votacion_dividida(
            _session(for_=73, against=78, result=VotingResult.REJECTED)
        )
        assert approved is not None
        assert rejected is not None
        assert approved.payload["margin"] == rejected.payload["margin"]

    def test_returns_none_when_no_decisive_votes(self):
        assert (
            vs.compute_votacion_dividida(_session(for_=0, against=0, abstentions=100))
            is None
        )

    def test_returns_none_when_chamber_seats_zero(self):
        assert (
            vs.compute_votacion_dividida(_session(for_=78, against=73, total_seats=0))
            is None
        )

    def test_severity_grows_as_margin_shrinks(self):
        wider = vs.compute_votacion_dividida(_session(for_=75, against=70))
        tighter = vs.compute_votacion_dividida(_session(for_=80, against=79))
        assert wider is not None and tighter is not None
        assert tighter.severity > wider.severity

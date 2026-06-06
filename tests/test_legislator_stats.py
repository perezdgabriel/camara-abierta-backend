"""Pure-function tests for the inclinación de voto + disciplina partidaria math.

DB-dependent orchestration (``refresh_legislator_voting_stats``) lives in
``tests/integration/test_legislator_stats_integration.py`` because the ORM uses
a PostgreSQL sequence that the in-memory SQLite default can't compile. These
tests cover the pure tallying logic only — no DB. See web ``CONTEXT.md``
("Inclinación de voto", "Disciplina partidaria") and ADR-0007.
"""

from __future__ import annotations

from app.models.enums import Bloc, VoteChoice
from app.services import legislator_stats as ls

FOR = VoteChoice.FOR
AGAINST = VoteChoice.AGAINST
ABSTAIN = VoteChoice.ABSTAIN
ABSENT = VoteChoice.ABSENT


class TestModalChoice:
    def test_empty_is_none(self):
        assert ls.modal_choice([], ls.DECISIVE) is None

    def test_clear_modal(self):
        assert ls.modal_choice([FOR, FOR, AGAINST], ls.DECISIVE) is FOR

    def test_tie_is_none(self):
        # No clear position when the top two choices share the count.
        assert ls.modal_choice([FOR, AGAINST], ls.DECISIVE) is None

    def test_ignores_disallowed_choices(self):
        # Abstain/absent don't pick a side; excluded from the decisive modal.
        assert ls.modal_choice([FOR, FOR, ABSTAIN, ABSENT], ls.DECISIVE) is FOR

    def test_abstain_counts_for_party_modal(self):
        # PARTY_CHOICES includes abstain (disciplina mirrors cohesión).
        assert ls.modal_choice([ABSTAIN, ABSTAIN, FOR], ls.PARTY_CHOICES) is ABSTAIN


def _contested(ofi: VoteChoice, opo: VoteChoice, choice: VoteChoice):
    return (ofi, opo, choice)


class TestComputeLean:
    def test_below_minimum_sample_is_none(self):
        # Only 3 contested decisive sessions (< LEAN_MIN_CONTESTED) → datos insuficientes.
        sessions = [_contested(FOR, AGAINST, AGAINST)] * 3
        assert ls.compute_lean(sessions) is None

    def test_clear_opposition_lean_seats(self):
        # Voted against (with the oposición modal) in 8 of 10 contested sessions.
        sessions = [_contested(FOR, AGAINST, AGAINST)] * 8 + [
            _contested(FOR, AGAINST, FOR)
        ] * 2
        result = ls.compute_lean(sessions)
        assert result is not None
        assert result.bloc is Bloc.OPOSICION
        assert result.agreed == 8
        assert result.contested == 10
        assert result.seats is True

    def test_weak_lean_does_not_seat(self):
        # 5 vs 4 → leans oficialismo but 5/9 ≈ 0.556 < 0.60 margin → shown, not seated.
        sessions = [_contested(FOR, AGAINST, FOR)] * 5 + [
            _contested(FOR, AGAINST, AGAINST)
        ] * 4
        result = ls.compute_lean(sessions)
        assert result is not None
        assert result.bloc is Bloc.OFICIALISMO
        assert result.contested == 9
        assert result.seats is False

    def test_exact_margin_seats(self):
        # 6 of 10 = 0.60 exactly → seats (threshold is inclusive ≥).
        sessions = [_contested(FOR, AGAINST, FOR)] * 6 + [
            _contested(FOR, AGAINST, AGAINST)
        ] * 4
        result = ls.compute_lean(sessions)
        assert result is not None
        assert result.seats is True

    def test_tie_has_no_bloc(self):
        sessions = [_contested(FOR, AGAINST, FOR)] * 5 + [
            _contested(FOR, AGAINST, AGAINST)
        ] * 5
        result = ls.compute_lean(sessions)
        assert result is not None
        assert result.bloc is None
        assert result.contested == 10
        assert result.seats is False

    def test_non_contested_sessions_ignored(self):
        # Blocs on the same side (both FOR) reveal nothing → excluded from N.
        contested = [_contested(FOR, AGAINST, AGAINST)] * 5
        unanimous = [_contested(FOR, FOR, FOR)] * 20
        result = ls.compute_lean(contested + unanimous)
        assert result is not None
        assert result.contested == 5
        assert result.bloc is Bloc.OPOSICION

    def test_legislator_abstention_not_counted(self):
        # An abstention in a contested session picks no side → not in the denominator.
        sessions = [_contested(FOR, AGAINST, AGAINST)] * 5 + [
            _contested(FOR, AGAINST, ABSTAIN)
        ] * 5
        result = ls.compute_lean(sessions)
        assert result is not None
        assert result.contested == 5
        assert result.agreed == 5

    def test_missing_bloc_modal_skips_session(self):
        # A bloc with no clear position (None modal) → session not contested.
        sessions = [_contested(FOR, AGAINST, AGAINST)] * 5 + [
            (None, AGAINST, AGAINST)
        ] * 5
        result = ls.compute_lean(sessions)
        assert result is not None
        assert result.contested == 5


class TestComputeDiscipline:
    def test_below_minimum_is_none(self):
        sessions = [(FOR, FOR)] * 3
        assert ls.compute_discipline(sessions) is None

    def test_rate_with_party(self):
        # Voted with the party modal in 9 of 10 decided sessions.
        sessions = [(FOR, FOR)] * 9 + [(FOR, AGAINST)]
        result = ls.compute_discipline(sessions)
        assert result is not None
        assert result.with_party == 9
        assert result.decided == 10
        assert abs(result.rate - 0.9) < 1e-9

    def test_party_without_modal_skipped(self):
        # Sessions where the party had no clear modal (None) drop out of the denominator.
        sessions = [(FOR, FOR)] * 5 + [(None, AGAINST)] * 5
        result = ls.compute_discipline(sessions)
        assert result is not None
        assert result.decided == 5

    def test_absent_legislator_choice_skipped(self):
        # The legislator's absences/dispensations aren't disciplinable.
        sessions = [(FOR, FOR)] * 5 + [(FOR, ABSENT)] * 5
        result = ls.compute_discipline(sessions)
        assert result is not None
        assert result.decided == 5
        assert result.with_party == 5

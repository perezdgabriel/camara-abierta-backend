"""Vote-time party / chamber resolution on voting-session detail.

Covers `Legislator.party_on` / `chamber_type_on` and the service-side
`build_vote_details` reshape introduced for the "Vote-time party" CONTEXT.md
entry (regression for old sessions rendering closed terms as IND).
"""

from datetime import date, datetime
from types import SimpleNamespace

from app.models.enums import ChamberType, VoteChoice
from app.models.legislature import (
    Chamber,
    Legislator,
    LegislatorTerm,
    PoliticalParty,
)
from app.services.voting import build_vote_details


def _party(party_id: int, abbreviation: str) -> PoliticalParty:
    return PoliticalParty(
        id=party_id,
        name=f"Partido {abbreviation}",
        abbreviation=abbreviation,
        color="#112233",
    )


def _chamber(chamber_id: int, chamber_type: ChamberType) -> Chamber:
    return Chamber(id=chamber_id, chamber_type=chamber_type, name=chamber_type.value)


def _term(
    *,
    chamber: Chamber,
    party: PoliticalParty | None,
    start: date,
    end: date | None,
) -> LegislatorTerm:
    term = LegislatorTerm(
        period_id=1,
        chamber_id=chamber.id,
        party_id=party.id if party else None,
        start_date=start,
        end_date=end,
    )
    term.chamber = chamber
    term.party = party
    return term


def test_party_on_returns_term_covering_date():
    deputies = _chamber(1, ChamberType.DEPUTIES)
    ps = _party(10, "PS")
    leg = Legislator(id=100, first_name="A", last_name="B", full_name="A B")
    leg.terms = [
        _term(
            chamber=deputies, party=ps, start=date(2022, 3, 11), end=date(2026, 3, 11)
        ),
    ]

    assert leg.party_on(date(2025, 7, 21)) is ps
    assert leg.chamber_type_on(date(2025, 7, 21)) is ChamberType.DEPUTIES


def test_party_on_returns_none_when_no_term_covers_date():
    deputies = _chamber(1, ChamberType.DEPUTIES)
    ps = _party(10, "PS")
    leg = Legislator(id=100, first_name="A", last_name="B", full_name="A B")
    # Term ended before the date being queried.
    leg.terms = [
        _term(
            chamber=deputies, party=ps, start=date(2018, 3, 11), end=date(2022, 3, 11)
        ),
    ]

    assert leg.party_on(date(2025, 7, 21)) is None
    assert leg.chamber_type_on(date(2025, 7, 21)) is None


def test_party_on_picks_the_term_active_on_that_date_over_today():
    """Regression for /votaciones/9495: closed term must still surface its party."""
    deputies = _chamber(1, ChamberType.DEPUTIES)
    senate = _chamber(2, ChamberType.SENATE)
    ud = _party(10, "UDI")
    rn = _party(11, "RN")
    leg = Legislator(id=100, first_name="A", last_name="B", full_name="A B")
    # Old deputy stint (UDI) then current senator stint (RN).
    leg.terms = [
        _term(chamber=senate, party=rn, start=date(2026, 3, 11), end=None),
        _term(
            chamber=deputies, party=ud, start=date(2022, 3, 11), end=date(2026, 3, 11)
        ),
    ]

    # Vote on 2025-07-21 → still in the deputy stint, party should be UDI.
    assert leg.party_on(date(2025, 7, 21)) is ud
    assert leg.chamber_type_on(date(2025, 7, 21)) is ChamberType.DEPUTIES
    # Vote on 2026-06-21 → current senate stint, party should be RN.
    assert leg.party_on(date(2026, 6, 21)) is rn
    assert leg.chamber_type_on(date(2026, 6, 21)) is ChamberType.SENATE


def test_party_on_party_change_within_chamber():
    deputies = _chamber(1, ChamberType.DEPUTIES)
    udi = _party(10, "UDI")
    ind = _party(11, "IND-party")  # represents a real registered party, not literal IND
    leg = Legislator(id=100, first_name="A", last_name="B", full_name="A B")
    leg.terms = [
        _term(
            chamber=deputies, party=ind, start=date(2024, 1, 1), end=date(2026, 3, 11)
        ),
        _term(
            chamber=deputies, party=udi, start=date(2022, 3, 11), end=date(2023, 12, 31)
        ),
    ]

    assert leg.party_on(date(2022, 6, 1)) is udi
    assert leg.party_on(date(2025, 7, 21)) is ind


def _vote_session_at(
    voting_date: datetime, votes: list, voting_id: int = 30
) -> SimpleNamespace:
    return SimpleNamespace(id=voting_id, voting_date=voting_date, votes=votes)


def _vote(
    *,
    vote_id: int,
    legislator: Legislator | None,
    external_id: str,
    choice: VoteChoice = VoteChoice.FOR,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=vote_id,
        vote=choice,
        legislator=legislator,
        legislator_external_id=external_id,
    )


def test_build_vote_details_resolves_party_at_vote_date_for_closed_term():
    """The bug: a deputy whose 2022–2026 term has since closed renders as IND on
    /votaciones/9495 (2025-07-21). After the fix, the term's party shows."""
    deputies = _chamber(1, ChamberType.DEPUTIES)
    ps = _party(10, "PS")
    leg = Legislator(id=100, first_name="A", last_name="B", full_name="A B")
    leg.terms = [
        _term(
            chamber=deputies, party=ps, start=date(2022, 3, 11), end=date(2026, 3, 11)
        ),
    ]
    session = _vote_session_at(
        voting_date=datetime(2025, 7, 21, 19, 21),
        votes=[_vote(vote_id=1, legislator=leg, external_id="camara:100")],
    )

    [detail] = build_vote_details(session)

    assert detail.legislator is not None
    assert detail.legislator.party is not None
    assert detail.legislator.party.abbreviation == "PS"
    assert detail.legislator.chamber_type is ChamberType.DEPUTIES


def test_build_vote_details_handles_party_change_between_vote_and_today():
    deputies = _chamber(1, ChamberType.DEPUTIES)
    udi = _party(10, "UDI")
    fa = _party(11, "FA")
    leg = Legislator(id=100, first_name="A", last_name="B", full_name="A B")
    leg.terms = [
        _term(
            chamber=deputies, party=fa, start=date(2024, 1, 1), end=date(2026, 3, 11)
        ),
        _term(
            chamber=deputies, party=udi, start=date(2022, 3, 11), end=date(2023, 12, 31)
        ),
    ]
    # Vote during the UDI stint.
    session = _vote_session_at(
        voting_date=datetime(2022, 9, 1, 11, 0),
        votes=[_vote(vote_id=1, legislator=leg, external_id="camara:100")],
    )

    [detail] = build_vote_details(session)

    assert detail.legislator is not None
    assert detail.legislator.party is not None
    assert detail.legislator.party.abbreviation == "UDI"


def test_build_vote_details_passes_orphans_through_with_null_legislator():
    session = _vote_session_at(
        voting_date=datetime(2025, 7, 21, 19, 21),
        votes=[_vote(vote_id=1, legislator=None, external_id="camara:999")],
    )

    [detail] = build_vote_details(session)

    assert detail.legislator is None
    assert detail.legislator_external_id == "camara:999"


def test_build_vote_details_returns_no_party_when_no_term_covers_date():
    """No term covers 2025-07-21 → frontend renders as IND (correctly)."""
    deputies = _chamber(1, ChamberType.DEPUTIES)
    ps = _party(10, "PS")
    leg = Legislator(id=100, first_name="A", last_name="B", full_name="A B")
    leg.terms = [
        _term(
            chamber=deputies, party=ps, start=date(2014, 3, 11), end=date(2018, 3, 11)
        ),
    ]
    session = _vote_session_at(
        voting_date=datetime(2025, 7, 21, 19, 21),
        votes=[_vote(vote_id=1, legislator=leg, external_id="camara:100")],
    )

    [detail] = build_vote_details(session)

    assert detail.legislator is not None
    assert detail.legislator.party is None
    assert detail.legislator.chamber_type is None

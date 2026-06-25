"""Integration tests for ``upsert_voting_session`` + ``_reconcile_votes``.

Focus: the Senate-side ``NO_VOTE`` synthesis. The upstream restsil feed only
emits per-senator rows in SI/NO/ABSTENCION/PAREO buckets; senators who did
not vote leave no row at all. The reconciler materialises one ``NO_VOTE``
row per senator whose ``LegislatorTerm`` covers ``voting_date`` and who is
absent from the upstream list — see ``app/services/write.py`` and
``CONTEXT.md`` entry "No vota".
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy.orm import Session

from app.models.enums import ChamberType, VoteChoice, VotingType
from app.models.legislature import (
    Chamber,
    LegislativePeriod,
    Legislator,
    LegislatorTerm,
)
from app.models.votacion import Vote, VotingSession
from app.services.write import upsert_voting_session

pytestmark = pytest.mark.integration


def _reload_votes(db: Session, voting_session_id: int) -> list[Vote]:
    """Re-read votes from the DB.

    ``upsert_voting_session`` returns a ``VotingSession`` whose ``.votes``
    relationship was selectinload-ed *before* ``_reconcile_votes`` appended
    new rows, so the cached list is stale. The DB is correct — re-query.
    """
    return db.query(Vote).filter(Vote.voting_session_id == voting_session_id).all()


def _make_senator(
    db: Session,
    chamber: Chamber,
    period: LegislativePeriod,
    *,
    parlid: str,
    full_name: str,
) -> Legislator:
    leg = Legislator(
        first_name=full_name.split()[0],
        last_name=" ".join(full_name.split()[1:]) or full_name,
        full_name=full_name,
    )
    db.add(leg)
    db.flush()
    db.add(
        LegislatorTerm(
            legislator_id=leg.id,
            period_id=period.id,
            chamber_id=chamber.id,
            chamber_external_id=f"senado:{parlid}",
            start_date=date(2026, 3, 11),
            end_date=None,
        )
    )
    db.flush()
    return leg


def test_senate_session_synthesises_no_vote_rows_for_missing_senators(
    db_session: Session,
):
    senate = Chamber(chamber_type=ChamberType.SENATE, name="Senado", total_seats=50)
    db_session.add(senate)
    db_session.flush()
    period = LegislativePeriod(
        number=56, start_date=date(2026, 3, 11), end_date=date(2030, 3, 11)
    )
    db_session.add(period)
    db_session.flush()

    voter = _make_senator(db_session, senate, period, parlid="1", full_name="Ada Demo")
    silent_1 = _make_senator(
        db_session, senate, period, parlid="2", full_name="Beto Demo"
    )
    silent_2 = _make_senator(
        db_session, senate, period, parlid="3", full_name="Cami Demo"
    )

    voting_session = upsert_voting_session(
        db_session,
        {
            "bcn_id": "senado:vot:test-1",
            "_chamber_type": ChamberType.SENATE,
            "voting_type": VotingType.GENERAL,
            "subject": "Test session",
            "voting_date": datetime(2026, 6, 1, 11, 0).isoformat(),
            "votes_for": 1,
            "votes_against": 0,
            "abstentions": 0,
            "individual_votes": [
                {
                    "legislator_external_id": "senado:1",
                    "vote": VoteChoice.FOR,
                },
            ],
        },
    )
    db_session.flush()

    votes_by_external = {
        v.legislator_external_id: v
        for v in _reload_votes(db_session, voting_session.id)
    }
    assert votes_by_external["senado:1"].vote == VoteChoice.FOR
    assert votes_by_external["senado:1"].legislator_id == voter.id

    assert votes_by_external["senado:2"].vote == VoteChoice.NO_VOTE
    assert votes_by_external["senado:2"].legislator_id == silent_1.id

    assert votes_by_external["senado:3"].vote == VoteChoice.NO_VOTE
    assert votes_by_external["senado:3"].legislator_id == silent_2.id

    db_session.refresh(voting_session)
    assert voting_session.no_votes == 2


def test_senate_synthesis_skips_senators_without_covering_term(db_session: Session):
    senate = Chamber(chamber_type=ChamberType.SENATE, name="Senado", total_seats=50)
    db_session.add(senate)
    db_session.flush()
    period = LegislativePeriod(
        number=56, start_date=date(2026, 3, 11), end_date=date(2030, 3, 11)
    )
    db_session.add(period)
    db_session.flush()

    _make_senator(db_session, senate, period, parlid="1", full_name="Ada Demo")

    out_of_window = Legislator(
        first_name="Past", last_name="Senator", full_name="Past Senator"
    )
    db_session.add(out_of_window)
    db_session.flush()
    db_session.add(
        LegislatorTerm(
            legislator_id=out_of_window.id,
            period_id=period.id,
            chamber_id=senate.id,
            chamber_external_id="senado:99",
            start_date=date(2018, 3, 11),
            end_date=date(2026, 3, 10),
        )
    )
    db_session.flush()

    voting_session = upsert_voting_session(
        db_session,
        {
            "bcn_id": "senado:vot:test-2",
            "_chamber_type": ChamberType.SENATE,
            "voting_type": VotingType.GENERAL,
            "subject": "Test session 2",
            "voting_date": datetime(2026, 6, 1, 11, 0).isoformat(),
            "votes_for": 1,
            "votes_against": 0,
            "abstentions": 0,
            "individual_votes": [
                {
                    "legislator_external_id": "senado:1",
                    "vote": VoteChoice.FOR,
                },
            ],
        },
    )
    db_session.flush()

    bridges = {
        v.legislator_external_id for v in _reload_votes(db_session, voting_session.id)
    }
    assert bridges == {"senado:1"}


def test_chamber_session_does_not_synthesise(db_session: Session):
    deputies = Chamber(
        chamber_type=ChamberType.DEPUTIES, name="Cámara", total_seats=155
    )
    db_session.add(deputies)
    db_session.flush()
    period = LegislativePeriod(
        number=56, start_date=date(2026, 3, 11), end_date=date(2030, 3, 11)
    )
    db_session.add(period)
    db_session.flush()

    voter = Legislator(first_name="Ada", last_name="Demo", full_name="Ada Demo")
    silent = Legislator(first_name="Beto", last_name="Demo", full_name="Beto Demo")
    db_session.add_all([voter, silent])
    db_session.flush()
    for leg, bridge in [(voter, "camara:1"), (silent, "camara:2")]:
        db_session.add(
            LegislatorTerm(
                legislator_id=leg.id,
                period_id=period.id,
                chamber_id=deputies.id,
                chamber_external_id=bridge,
                start_date=date(2026, 3, 11),
                end_date=None,
            )
        )
    db_session.flush()

    voting_session = upsert_voting_session(
        db_session,
        {
            "bcn_id": "camara:vot:test-1",
            "_chamber_type": ChamberType.DEPUTIES,
            "voting_type": VotingType.GENERAL,
            "subject": "Test chamber",
            "voting_date": datetime(2026, 6, 1, 11, 0).isoformat(),
            "votes_for": 1,
            "votes_against": 0,
            "abstentions": 0,
            "individual_votes": [
                {
                    "legislator_external_id": "camara:1",
                    "vote": VoteChoice.FOR,
                },
            ],
        },
    )
    db_session.flush()

    bridges = {
        v.legislator_external_id for v in _reload_votes(db_session, voting_session.id)
    }
    assert bridges == {"camara:1"}
    db_session.refresh(voting_session)
    assert voting_session.no_votes == 0


def test_senate_synthesis_recorded_no_vote_count_drives_no_votes_column(
    db_session: Session,
):
    senate = Chamber(chamber_type=ChamberType.SENATE, name="Senado", total_seats=50)
    db_session.add(senate)
    db_session.flush()
    period = LegislativePeriod(
        number=56, start_date=date(2026, 3, 11), end_date=date(2030, 3, 11)
    )
    db_session.add(period)
    db_session.flush()

    _make_senator(db_session, senate, period, parlid="1", full_name="Ada Demo")
    _make_senator(db_session, senate, period, parlid="2", full_name="Beto Demo")
    _make_senator(db_session, senate, period, parlid="3", full_name="Cami Demo")
    _make_senator(db_session, senate, period, parlid="4", full_name="Dani Demo")

    voting_session = upsert_voting_session(
        db_session,
        {
            "bcn_id": "senado:vot:test-3",
            "_chamber_type": ChamberType.SENATE,
            "voting_type": VotingType.GENERAL,
            "subject": "Test count",
            "voting_date": datetime(2026, 6, 1, 11, 0).isoformat(),
            "votes_for": 1,
            "votes_against": 1,
            "abstentions": 0,
            "individual_votes": [
                {"legislator_external_id": "senado:1", "vote": VoteChoice.FOR},
                {"legislator_external_id": "senado:2", "vote": VoteChoice.AGAINST},
            ],
        },
    )
    db_session.flush()

    materialised_no_votes = (
        db_session.query(Vote)
        .filter(
            Vote.voting_session_id == voting_session.id,
            Vote.vote == VoteChoice.NO_VOTE,
        )
        .count()
    )
    assert materialised_no_votes == 2
    db_session.refresh(voting_session)
    assert voting_session.no_votes == 2
    # Sanity: reload-based count matches the synthesised expectation.
    reloaded = (
        db_session.query(VotingSession)
        .filter(VotingSession.id == voting_session.id)
        .one()
    )
    assert reloaded.no_votes == 2

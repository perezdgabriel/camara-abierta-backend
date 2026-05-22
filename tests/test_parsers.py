from app.ingestors.parsers.bills import BillParser
from app.ingestors.parsers.votes import VoteParser
from app.models.enums import (
    BillOrigin,
    BillStatus,
    ChamberType,
    StageType,
    UrgencyType,
    VoteChoice,
    VotingResult,
    VotingType,
)


def test_bill_parser_maps_canonical_enums_and_discards_blank_authors():
    payload = BillParser.parse_bill(
        {
            "bulletin": "555-06",
            "title": "  Proyecto de prueba  ",
            "entry_date": "2026-05-10",
            "initiative": "Mensaje",
            "origin_chamber": "Senado",
            "status": "Publicado",
            "current_urgency": "Discusión inmediata",
            "authors": [
                {"legislator": "Ada Demo"},
                {"legislator": "  "},
            ],
            "materias": [" Transparencia ", ""],
            "tramitaciones": [
                {
                    "stage": "Primer trámite constitucional",
                    "date": "2026-05-11",
                    "chamber": "Senado",
                    "description": "Ingreso",
                    "session": "1",
                }
            ],
            "informes": [],
            "comparados": [],
            "oficios": [],
            "votaciones": [],
        }
    )

    assert payload["origin_type"] is BillOrigin.EXECUTIVE
    assert payload["_origin_chamber_type"] is ChamberType.SENATE
    assert payload["status"] is BillStatus.PUBLISHED
    assert payload["_current_urgency_type"] is UrgencyType.IMMEDIATE
    assert payload["authors"] == [{"name": "Ada Demo"}]
    assert payload["topics"] == ["Transparencia"]
    assert payload["stages"][0]["stage_type"] is StageType.FIRST_CONSTITUTIONAL_TRAMITE
    assert payload["stages"][0]["_chamber_type"] is ChamberType.SENATE


def test_vote_parser_maps_votes_and_result_to_canonical_enums():
    payload = VoteParser.parse_senate_vote(
        {
            "voting_type": "Discusión general",
            "session": "42",
            "subject": "Votación del proyecto",
            "date": "2026-05-12",
            "votes_for": 20,
            "votes_against": 10,
            "abstentions": 1,
            "paired": 2,
            "quorum": "simple",
            "detail": [
                {"legislator_name": "Ada Demo", "vote": "Sí"},
                {"legislator_name": "Beto Demo", "vote": "Desconocido"},
            ],
        },
        bulletin="555-06",
    )

    assert payload["bcn_id"] == "senado:vot:555-06:42"
    assert payload["_chamber_type"] is ChamberType.SENATE
    assert payload["voting_type"] is VotingType.GENERAL
    assert payload["result"] is VotingResult.APPROVED
    assert payload["individual_votes"][0]["vote"] is VoteChoice.FOR
    assert payload["individual_votes"][1]["vote"] is VoteChoice.ABSENT

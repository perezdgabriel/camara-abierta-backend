from app.models.enums import ChamberType, VoteChoice, VotingResult, VotingType

SENADO_VOTE_MAP = {
    "Si": VoteChoice.FOR,
    "Sí": VoteChoice.FOR,
    "No": VoteChoice.AGAINST,
    "Abstencion": VoteChoice.ABSTAIN,
    "Abstención": VoteChoice.ABSTAIN,
    "Pareo": VoteChoice.PAIRED,
}

VOTING_TYPE_MAP = {
    "Discusion general": VotingType.GENERAL,
    "Discusión general": VotingType.GENERAL,
    "Discusion en general": VotingType.GENERAL,
    "Discusión en general": VotingType.GENERAL,
    "Discusion particular": VotingType.PARTICULAR,
    "Discusión particular": VotingType.PARTICULAR,
    "Discusion en particular": VotingType.PARTICULAR,
    "Discusión en particular": VotingType.PARTICULAR,
    "Discusion unica": VotingType.SINGLE,
    "Discusión única": VotingType.SINGLE,
    "Votacion unica": VotingType.SINGLE,
    "Votación única": VotingType.SINGLE,
}


class VoteParser:
    @staticmethod
    def parse_senate_vote(raw: dict, bulletin: str = "") -> dict:
        voting_type = VOTING_TYPE_MAP.get(raw.get("voting_type", ""), VotingType.OTHER)
        session_ref = raw.get("session", "")
        ext_id = f"senado:vot:{bulletin}:{session_ref}"
        votes_for = int(raw.get("votes_for", 0) or 0)
        votes_against = int(raw.get("votes_against", 0) or 0)
        if votes_for > votes_against:
            result = VotingResult.APPROVED
        elif votes_against > votes_for:
            result = VotingResult.REJECTED
        elif votes_for == votes_against and votes_for > 0:
            result = VotingResult.TIE
        else:
            result = None
        return {
            "bcn_id": ext_id,
            "_chamber_type": ChamberType.SENATE,
            "voting_type": voting_type,
            "subject": raw.get("subject", ""),
            "voting_date": raw.get("date"),
            "result": result,
            "votes_for": votes_for,
            "votes_against": votes_against,
            "abstentions": raw.get("abstentions", 0),
            "paired": raw.get("paired", 0),
            "quorum": raw.get("quorum", ""),
            "individual_votes": [
                {
                    "_legislator_name": vote.get("legislator_name", ""),
                    "vote": SENADO_VOTE_MAP.get(
                        vote.get("vote", ""), VoteChoice.ABSENT
                    ),
                }
                for vote in raw.get("detail", [])
            ],
        }

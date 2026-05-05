SENADO_VOTE_MAP = {
    "Si": "for",
    "Sí": "for",
    "No": "against",
    "Abstencion": "abstain",
    "Abstención": "abstain",
    "Pareo": "paired",
}

VOTING_TYPE_MAP = {
    "Discusion general": "general",
    "Discusión general": "general",
    "Discusion en general": "general",
    "Discusión en general": "general",
    "Discusion particular": "particular",
    "Discusión particular": "particular",
    "Discusion en particular": "particular",
    "Discusión en particular": "particular",
    "Discusion unica": "single",
    "Discusión única": "single",
    "Votacion unica": "single",
    "Votación única": "single",
}


class VoteParser:
    @staticmethod
    def parse_senate_vote(raw: dict, bulletin: str = "") -> dict:
        voting_type = VOTING_TYPE_MAP.get(raw.get("voting_type", ""), "other")
        session_ref = raw.get("session", "")
        ext_id = f"senado:vot:{bulletin}:{session_ref}"
        votes_for = int(raw.get("votes_for", 0) or 0)
        votes_against = int(raw.get("votes_against", 0) or 0)
        if votes_for > votes_against:
            result = "approved"
        elif votes_against > votes_for:
            result = "rejected"
        elif votes_for == votes_against and votes_for > 0:
            result = "tie"
        else:
            result = ""
        return {
            "bcn_id": ext_id,
            "_chamber_type": "senate",
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
                    "vote": SENADO_VOTE_MAP.get(vote.get("vote", ""), "absent"),
                }
                for vote in raw.get("detail", [])
            ],
        }
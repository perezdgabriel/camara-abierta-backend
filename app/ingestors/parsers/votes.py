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

CAMARA_VOTE_MAP = {
    "afirmativo": VoteChoice.FOR,
    "en contra": VoteChoice.AGAINST,
    "abstencion": VoteChoice.ABSTAIN,
    "abstención": VoteChoice.ABSTAIN,
    "dispensado": VoteChoice.DISPENSED,
}

CAMARA_RESULT_MAP = {
    "aprobado": VotingResult.APPROVED,
    "rechazado": VotingResult.REJECTED,
    "empatado": VotingResult.TIE,
}


class VoteParser:
    @staticmethod
    def _normalize(value: str | None) -> str:
        return (value or "").strip().lower()

    @staticmethod
    def _parse_chamber_vote_choice(label: str | None, code: int | None) -> VoteChoice:
        normalized = VoteParser._normalize(label)
        if normalized in CAMARA_VOTE_MAP:
            return CAMARA_VOTE_MAP[normalized]
        if code == 1:
            return VoteChoice.FOR
        if code == 0:
            return VoteChoice.AGAINST
        if code == 2:
            return VoteChoice.ABSTAIN
        return VoteChoice.ABSENT

    @staticmethod
    def _parse_chamber_voting_type(label: str | None) -> VotingType:
        normalized = VoteParser._normalize(label)
        if not normalized:
            return VotingType.OTHER
        if "general" in normalized:
            return VotingType.GENERAL
        if "particular" in normalized:
            return VotingType.PARTICULAR
        if "unica" in normalized or "única" in normalized:
            return VotingType.SINGLE
        return VotingType.OTHER

    @staticmethod
    def _parse_result(
        label: str | None, votes_for: int, votes_against: int
    ) -> VotingResult | None:
        normalized = VoteParser._normalize(label)
        if normalized in CAMARA_RESULT_MAP:
            return CAMARA_RESULT_MAP[normalized]
        if votes_for > votes_against:
            return VotingResult.APPROVED
        if votes_against > votes_for:
            return VotingResult.REJECTED
        if votes_for == votes_against and votes_for > 0:
            return VotingResult.TIE
        return None

    @staticmethod
    def _build_chamber_legislator_name(raw: dict) -> str:
        first_name = (raw.get("first_name") or "").strip()
        last_name_parts = [
            (raw.get("last_name_father") or "").strip(),
            (raw.get("last_name_mother") or "").strip(),
        ]
        last_name = " ".join(part for part in last_name_parts if part)
        return " ".join(part for part in [first_name, last_name] if part).strip()

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
            "session_ref": session_ref,
            "voting_type": voting_type,
            "stage_label": (raw.get("stage") or "").strip() or None,
            "subject": raw.get("subject", ""),
            "voting_date": raw.get("date"),
            "result": result,
            "votes_for": votes_for,
            "votes_against": votes_against,
            "abstentions": raw.get("abstentions", 0),
            "paired_count": int(raw.get("paired", 0) or 0),
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

    @staticmethod
    def parse_restsil_senate_vote(raw: dict) -> dict:
        """Normalize one ``buscarVotaciones`` row into the standard sync payload.

        This is the **primary** Senate-vote parser per ADR-0009. Unlike the
        legacy ``parse_senate_vote`` it does *not* take ``bulletin`` as input
        — the upstream row carries ``BOLETIN`` directly — and it produces:

        - ``bcn_id = senado:vot:{ID_VOTACION}`` (stable upstream id).
        - ``legislator_external_id = senado:{PARLID}`` per individual vote,
          letting ``_resolve_vote_legislator`` hit ``Legislator.bcn_id``
          directly instead of brittle name matching.
        - ``voting_type`` inferred from ``TEMA`` substrings (the upstream
          dropped ``TIPOVOTACION`` from this endpoint).
        - ``stage_label`` and ``bill_stage_id`` left null — ``ETAPA`` is also
          absent from this endpoint.
        """
        vote_id = raw.get("ID_VOTACION")
        bulletin = (raw.get("BOLETIN") or "").strip()
        subject = (raw.get("TEMA") or "").strip()
        votes_for = int(raw.get("SI", 0) or 0)
        votes_against = int(raw.get("NO", 0) or 0)
        abstentions = int(raw.get("ABS", 0) or 0)
        paired = int(raw.get("PAREO", 0) or 0)

        if votes_for > votes_against:
            result = VotingResult.APPROVED
        elif votes_against > votes_for:
            result = VotingResult.REJECTED
        elif votes_for == votes_against and votes_for > 0:
            result = VotingResult.TIE
        else:
            result = None

        session_ref = raw.get("NUMERO_SESION")
        return {
            "bcn_id": f"senado:vot:{vote_id}",
            "_chamber_type": ChamberType.SENATE,
            "bill_bulletin": bulletin or None,
            "session_ref": str(session_ref) if session_ref is not None else None,
            "voting_type": VoteParser._parse_chamber_voting_type(subject),
            "stage_label": None,
            "subject": subject,
            # ``FECHA_VOTACION`` is populated on modern rows (second
            # precision); old historical votes (e.g. ID_VOTACION 5532, from
            # 2014) carry only the minute-precision ``HORA`` field instead.
            # ``_parse_restsil_datetime`` accepts both formats — chain the
            # fallback here so we don't lose the upstream date for the
            # old rows.
            "voting_date": VoteParser._parse_restsil_datetime(
                raw.get("FECHA_VOTACION") or raw.get("HORA")
            ),
            "result": result,
            "votes_for": votes_for,
            "votes_against": votes_against,
            "abstentions": abstentions,
            "paired_count": paired,
            "quorum": (raw.get("QUORUM") or "").strip(),
            "individual_votes": VoteParser._parse_restsil_individual_votes(
                raw.get("VOTACIONES") or {}
            ),
        }

    @staticmethod
    def _parse_restsil_datetime(value: str | None) -> str | None:
        """``FECHA_VOTACION`` is ``DD-MM-YYYY HH:MM:SS``. Returns ISO string."""
        if not value:
            return None
        import re

        match = re.match(
            r"(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2}):(\d{2})",
            value.strip(),
        )
        if match:
            return (
                f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
                f"T{match.group(4)}:{match.group(5)}:{match.group(6)}"
            )
        # ``HORA`` field has only minute precision and uses ``DD/MM/YYYY``
        match = re.match(
            r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})",
            value.strip(),
        )
        if match:
            return (
                f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
                f"T{match.group(4)}:{match.group(5)}:00"
            )
        return None

    @staticmethod
    def _parse_restsil_individual_votes(votaciones: dict) -> list[dict]:
        """Flatten ``VOTACIONES.SI/NO/ABSTENCION/PAREO`` into the standard list.

        Each bucket is either a list of per-legislator dicts or an integer 0
        (the upstream emits an int when the bucket is empty). PARLID becomes
        ``legislator_external_id = senado:{PARLID}``; resolution then goes
        through the existing ``_resolve_vote_legislator`` bcn_id branch.
        """
        buckets: list[tuple[str, VoteChoice]] = [
            ("SI", VoteChoice.FOR),
            ("NO", VoteChoice.AGAINST),
            ("ABSTENCION", VoteChoice.ABSTAIN),
            ("PAREO", VoteChoice.PAIRED),
        ]
        out: list[dict] = []
        for key, choice in buckets:
            entries = votaciones.get(key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                parlid = entry.get("PARLID")
                if parlid is None:
                    continue
                first_name = (entry.get("NOMBRE") or "").strip()
                last_father = (entry.get("APELLIDO_PATERNO") or "").strip()
                last_mother = (entry.get("APELLIDO_MATERNO") or "").strip()
                last_name = " ".join(
                    part for part in [last_father, last_mother] if part
                )
                full_name = " ".join(
                    part for part in [first_name, last_name] if part
                ).strip()
                out.append(
                    {
                        "legislator_external_id": f"senado:{parlid}",
                        "_legislator_name": full_name,
                        "legislator_first_name": first_name,
                        "legislator_last_name": last_name,
                        "vote": choice,
                    }
                )
        return out

    @staticmethod
    def parse_chamber_vote(raw: dict, bulletin: str = "") -> dict:
        votes_for = int(raw.get("votes_for", 0) or 0)
        votes_against = int(raw.get("votes_against", 0) or 0)
        abstentions = int(raw.get("abstentions", 0) or 0)
        dispensed_count = int(raw.get("dispensed_count", 0) or 0)
        subject = (raw.get("article_text") or raw.get("description") or "").strip()
        voting_id = int(raw.get("id", 0) or 0)

        return {
            "bcn_id": f"camara:vot:{voting_id}",
            "_chamber_type": ChamberType.DEPUTIES,
            "bill_bulletin": bulletin or None,
            "voting_type": VoteParser._parse_chamber_voting_type(
                raw.get("voting_type")
            ),
            "subject": subject,
            "voting_date": raw.get("date"),
            "result": VoteParser._parse_result(
                raw.get("result"), votes_for, votes_against
            ),
            "votes_for": votes_for,
            "votes_against": votes_against,
            "abstentions": abstentions,
            "dispensed_count": dispensed_count,
            "absences": int(raw.get("absences", 0) or 0),
            "quorum": raw.get("quorum", ""),
            "article_text": (raw.get("article_text") or "").strip() or None,
            "constitutional_procedure_id": raw.get("constitutional_procedure_id"),
            "constitutional_procedure_label": (
                raw.get("constitutional_procedure") or ""
            ).strip()
            or None,
            "regulatory_procedure_id": raw.get("regulatory_procedure_id"),
            "regulatory_procedure_label": (
                raw.get("regulatory_procedure") or ""
            ).strip()
            or None,
            "individual_votes": [
                {
                    "legislator_external_id": (
                        f"camara:{vote.get('deputy_id')}"
                        if vote.get("deputy_id") is not None
                        else None
                    ),
                    "_legislator_name": VoteParser._build_chamber_legislator_name(vote),
                    "legislator_first_name": (vote.get("first_name") or "").strip(),
                    "legislator_last_name": " ".join(
                        part
                        for part in [
                            (vote.get("last_name_father") or "").strip(),
                            (vote.get("last_name_mother") or "").strip(),
                        ]
                        if part
                    ).strip(),
                    "vote": VoteParser._parse_chamber_vote_choice(
                        vote.get("vote"), vote.get("vote_code")
                    ),
                }
                for vote in raw.get("individual_votes", [])
            ],
        }

from datetime import datetime

from pydantic import Field

from app.models.enums import ChamberType, VoteChoice, VotingResult, VotingType
from app.schemas.common import CountResponse, ORMModel


class PartyBrief(ORMModel):
    id: int
    name: str
    abbreviation: str
    color: str | None = None


class ChamberBrief(ORMModel):
    id: int
    chamber_type: ChamberType
    name: str


class BillBrief(ORMModel):
    id: int
    bulletin_number: str
    title: str


class LegislatorBrief(ORMModel):
    id: int
    full_name: str
    chamber_type: ChamberType
    party: PartyBrief | None = None


class VoteDetail(ORMModel):
    id: int
    vote: VoteChoice
    legislator: LegislatorBrief


class VotingSessionSummary(ORMModel):
    id: int
    bcn_id: str | None = None
    chamber: ChamberBrief
    bill: BillBrief | None = None
    voting_date: datetime
    voting_type: VotingType
    subject: str
    result: VotingResult | None = None
    votes_for: int
    votes_against: int
    abstentions: int
    absences: int
    quorum_type: str | None = None
    created_at: datetime
    updated_at: datetime
    sync_version: int


class VotingSessionDetail(VotingSessionSummary):
    dispensed_count: int
    paired_count: int
    session_ref: str | None = None
    stage_label: str | None = None
    article_text: str | None = None
    constitutional_procedure_id: int | None = None
    constitutional_procedure_label: str | None = None
    regulatory_procedure_id: int | None = None
    regulatory_procedure_label: str | None = None
    votes: list[VoteDetail] = Field(default_factory=list)


class VotingSessionsResponse(CountResponse[VotingSessionSummary]):
    data: list[VotingSessionSummary] = Field(default_factory=list)

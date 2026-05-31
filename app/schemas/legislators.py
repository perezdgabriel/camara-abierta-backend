from datetime import date, datetime

from pydantic import Field

from app.models.enums import Bloc, ChamberType, CommitteeType, VoteChoice
from app.models.enums import VotingResult as VotingResultEnum
from app.schemas.common import CountResponse, ORMModel


class TopicBrief(ORMModel):
    id: int
    name: str
    slug: str
    icon: str | None = None


class PartyBrief(ORMModel):
    id: int
    name: str
    abbreviation: str
    color: str | None = None
    # Editorial structural alignment as of today; null when unassigned.
    # Read from PoliticalParty.current_bloc (eager-load bloc_affiliations).
    current_bloc: Bloc | None = None


class DistrictBrief(ORMModel):
    id: int
    number: int
    name: str


class CircumscriptionBrief(ORMModel):
    id: int
    number: int
    name: str


class ChamberBrief(ORMModel):
    id: int
    chamber_type: ChamberType
    name: str


class CommitteeBrief(ORMModel):
    id: int
    name: str
    committee_type: CommitteeType
    chamber: ChamberBrief | None = None


class LegislatorVotingStatsSummary(ORMModel):
    total_sessions: int
    votes_for: int
    votes_against: int
    abstentions: int
    absences: int
    attendance_percentage: float
    participation_rate: float
    stats_updated_at: datetime


class LegislatorTermItem(ORMModel):
    id: int
    start_date: date
    end_date: date | None = None
    end_reason: str | None = None
    chamber: ChamberBrief
    party: PartyBrief | None = None


class CommitteeMembershipItem(ORMModel):
    id: int
    role: str
    start_date: date
    end_date: date | None = None
    committee: CommitteeBrief


class LegislatorSummary(ORMModel):
    id: int
    bcn_id: str | None = None
    full_name: str
    chamber_type: ChamberType
    photo_thumbnail_url: str | None = None
    party: PartyBrief | None = None
    district: DistrictBrief | None = None
    circumscription: CircumscriptionBrief | None = None
    is_active: bool
    # Editorial bloc override, used mainly to align independents in the majority
    # simulator. Null for party members (they inherit party.current_bloc) and for
    # unaligned independents (the "sin alinear" tray). See ADR-0006.
    default_bloc: Bloc | None = None
    created_at: datetime
    updated_at: datetime
    sync_version: int


class LegislatorDetail(LegislatorSummary):
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    photo_url: str | None = None
    profile_url: str | None = None
    biography: str | None = None
    terms: list[LegislatorTermItem] = Field(default_factory=list)
    committee_memberships: list[CommitteeMembershipItem] = Field(default_factory=list)
    voting_stats: LegislatorVotingStatsSummary | None = None


class LegislatorsResponse(CountResponse[LegislatorSummary]):
    data: list[LegislatorSummary] = Field(default_factory=list)


# ── Voting aggregation (computed from the votes table) ────────────────


class LegislatorVotingSummary(ORMModel):
    total_sessions: int
    votes_for: int
    votes_against: int
    abstentions: int
    absences: int
    attendance_percentage: float
    participation_rate: float


class VotingRecordItem(ORMModel):
    id: int
    voting_session_id: int
    vote: VoteChoice
    date: date
    subject: str
    result: VotingResultEnum | None = None


class TopicAffinityItem(ORMModel):
    topic: TopicBrief
    for_: int = Field(alias="for", serialization_alias="for")
    against: int
    abstain: int


class LegislatorVotingResponse(ORMModel):
    summary: LegislatorVotingSummary
    record: list[VotingRecordItem] = Field(default_factory=list)
    topic_affinity: list[TopicAffinityItem] = Field(default_factory=list)

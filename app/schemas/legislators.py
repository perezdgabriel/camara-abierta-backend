from datetime import date, datetime

from pydantic import Field

from app.models.enums import Bloc, ChamberType, CommitteeType, VoteChoice
from app.models.enums import VotingResult as VotingResultEnum
from app.schemas.common import CountResponse, ORMModel

# ``Legislator`` no longer carries chamber_type / party / district /
# circumscription / is_active as stored columns (ADR-0015). Pydantic reads
# from the ``current_*`` and ``is_active`` properties via ``validation_alias``
# so the JSON contract (chamber_type / party / district / circumscription /
# is_active) is preserved.


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


class PeriodBrief(ORMModel):
    """A 4-year ``LegislativePeriod`` projected to the API: id, sequential number,
    and the half-open ``[start_date, end_date)`` window. See ADR-0016."""

    id: int
    number: int
    start_date: date
    end_date: date


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
    period: PeriodBrief
    district: DistrictBrief | None = None
    circumscription: CircumscriptionBrief | None = None


class CommitteeMembershipItem(ORMModel):
    id: int
    role: str
    start_date: date
    end_date: date | None = None
    committee: CommitteeBrief


class VotingLean(ORMModel):
    """Inclinación de voto: the bloc whose modal vote the legislator matched most
    often across contested, decisive sessions of the current period. ``bloc`` is
    null on an exact split; ``seats`` marks a lean strong enough to seed an
    independent in the simulator. See ADR-0014."""

    bloc: Bloc | None = None
    agreed: int
    contested: int
    seats: bool


class PartyDiscipline(ORMModel):
    """Disciplina partidaria: how often a party member voted with their party's
    modal this period. ``rate`` is a percentage (0–100). Party members only."""

    rate: float | None = None
    with_party: int
    decided: int


class LegislatorSummary(ORMModel):
    id: int
    bcn_id: str | None = Field(
        default=None, validation_alias="current_chamber_external_id"
    )
    full_name: str
    chamber_type: ChamberType | None = Field(
        default=None, validation_alias="current_chamber_type"
    )
    photo_thumbnail_url: str | None = None
    party: PartyBrief | None = Field(default=None, validation_alias="current_party")
    district: DistrictBrief | None = Field(
        default=None, validation_alias="current_district"
    )
    circumscription: CircumscriptionBrief | None = Field(
        default=None, validation_alias="current_circumscription"
    )
    is_active: bool
    # Editorial bloc override, used mainly to align independents in the majority
    # simulator. Null for party members (they inherit party.current_bloc) and for
    # unaligned independents (the "sin alinear" tray). See ADR-0014.
    default_bloc: Bloc | None = None
    # Observed lean from voting behavior (computed, not editorial — see ADR-0014).
    # The simulator reads it from the list endpoint to seed independents.
    voting_lean: VotingLean | None = None
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
    # Every ``LegislativePeriod`` whose date range overlaps any of the
    # legislator's terms, sorted most-recent first. Used by the UI to render
    # the "Trayectoria parlamentaria" section grouped by period — a senate
    # mandate spanning two periods shows the same term under both.
    periods: list[PeriodBrief] = Field(default_factory=list)
    committee_memberships: list[CommitteeMembershipItem] = Field(default_factory=list)
    voting_stats: LegislatorVotingStatsSummary | None = None
    # Per-person party-unity rate this period; null for independents. See ADR-0014.
    party_discipline: PartyDiscipline | None = None


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


class LegislatorAuthoredBillsResponse(ORMModel):
    items: list["BillSummary"] = Field(default_factory=list)
    total: int


# Imported here (after the response class) to keep the proyectos→legislators
# schema dependency one-way; forward-ref'd via the string above so the import
# can sit at the bottom of the module without a cycle at module-load time.
from app.schemas.proyectos import BillSummary  # noqa: E402

LegislatorAuthoredBillsResponse.model_rebuild()

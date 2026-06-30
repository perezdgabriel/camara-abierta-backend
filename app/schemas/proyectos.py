from datetime import date, datetime

from pydantic import Field

from app.models.enums import (
    BillOrigin,
    BillStatus,
    BillSummaryStatus,
    BillType,
    ChamberType,
    StageType,
    UrgencyType,
    VotingType,
)
from app.models.enums import (
    VotingResult as VotingResultEnum,
)
from app.schemas.common import CountResponse, ORMModel

# ── Nested read-only objects ──────────────────────────────────────────


class TopicBrief(ORMModel):
    id: int
    name: str
    slug: str
    icon: str | None = None


class ChamberBrief(ORMModel):
    id: int
    chamber_type: ChamberType
    name: str


class CommitteeBrief(ORMModel):
    id: int
    name: str


class PartyBrief(ORMModel):
    id: int
    name: str
    abbreviation: str
    color: str | None = None


class LegislatorBrief(ORMModel):
    """Lightweight legislator reference for bill-author rows.

    ``chamber_type`` / ``party`` are entry-date values resolved by
    :func:`app.services.proyectos.build_author_briefs` from the
    ``LegislatorTerm`` covering ``Bill.entry_date``. Defaults to ``None``
    so a route that forgets the helper degrades visibly instead of
    silently picking up today's term. Mirrors the voting precedent (ADR-0015).
    """

    id: int
    full_name: str
    chamber_type: ChamberType | None = None
    photo_thumbnail_url: str | None = None
    party: PartyBrief | None = None


class Author(ORMModel):
    id: int
    author_type: str
    legislator: LegislatorBrief


class Stage(ORMModel):
    id: int
    stage_type: StageType
    chamber: ChamberBrief | None = None
    committee: CommitteeBrief | None = None
    start_date: date
    end_date: date | None = None
    result: str | None = None
    description: str | None = None
    is_current: bool


class Document(ORMModel):
    id: int
    document_type: str
    title: str
    description: str | None = None
    document_url: str | None = None
    document_date: date | None = None
    bill_stage_id: int | None = None


class Event(ORMModel):
    id: int
    event_date: date
    title: str
    description: str | None = None
    chamber: ChamberBrief | None = None
    bill_stage_id: int | None = None


class Urgency(ORMModel):
    id: int
    urgency_type: UrgencyType
    chamber: ChamberBrief
    entry_date: date
    withdrawal_date: date | None = None
    deadline_date: date | None = None
    is_active: bool


class SponsoringMinistry(ORMModel):
    id: int
    source_id: int | None = None
    name: str | None = None


class VotesSummary(ORMModel):
    """Denormalised vote tally from a bill's most recent voting session."""

    for_: int = Field(alias="for", serialization_alias="for")
    against: int
    abstain: int


class VotingResult(ORMModel):
    id: int
    bcn_id: str | None = None
    chamber: ChamberBrief
    voting_date: datetime
    voting_type: VotingType
    subject: str
    result: VotingResultEnum | None = None
    votes_for: int
    votes_against: int
    abstentions: int
    dispensed_count: int
    no_votes: int
    paired_count: int
    quorum_type: str | None = None
    session_ref: str | None = None
    stage_label: str | None = None
    bill_stage_id: int | None = None
    article_text: str | None = None
    constitutional_procedure_id: int | None = None
    constitutional_procedure_label: str | None = None
    regulatory_procedure_id: int | None = None
    regulatory_procedure_label: str | None = None


# ── List / summary schema ─────────────────────────────────────────────


class BillSummary(ORMModel):
    """Compact bill for list views — no nested lifecycle data."""

    id: int
    bulletin_number: str
    title: str
    bill_type: BillType
    origin: BillOrigin
    status: BillStatus
    entry_date: date
    last_activity_date: date
    publication_date: date | None = None
    law_number: str | None = None
    origin_chamber: ChamberBrief | None = None
    current_chamber: ChamberBrief | None = None
    current_committee: CommitteeBrief | None = None
    topics: list[TopicBrief] = Field(default_factory=list)
    # Denormalised convenience fields populated by the service layer
    active_urgency_type: UrgencyType | None = None
    current_stage_type: StageType | None = None
    votes_summary: VotesSummary | None = None
    created_at: datetime
    updated_at: datetime
    sync_version: int


# ── AI summary layers ─────────────────────────────────────────────────


class ProposalSummaryContent(ORMModel):
    propose: str
    affected_groups: list[str] = Field(default_factory=list)
    why_it_matters: str
    key_objections: list[str] = Field(default_factory=list)


class AmendmentsSummaryContent(ORMModel):
    changes: list[str] = Field(default_factory=list)


class ProposalSummary(ORMModel):
    status: BillSummaryStatus
    content: ProposalSummaryContent | None = None
    generated_at: datetime
    prompt_version: str | None = None
    model_name: str | None = None


class AmendmentsSummary(ORMModel):
    status: BillSummaryStatus
    content: AmendmentsSummaryContent | None = None
    generated_at: datetime
    prompt_version: str | None = None
    model_name: str | None = None


class BillStatusLine(ORMModel):
    """Deterministic, no-LLM 'where it is now' line. Composed in the API."""

    plain_text: str
    current_status: BillStatus
    current_stage_type: StageType | None = None
    current_committee_name: str | None = None
    last_activity_date: date


class BillAISummary(ORMModel):
    proposal: ProposalSummary | None = None
    amendments: AmendmentsSummary | None = None
    status_line: BillStatusLine


# ── Detail schema ─────────────────────────────────────────────────────


class BillDetail(BillSummary):
    """Full bill lifecycle: stages, events, documents, votes, authors."""

    ai_summary: BillAISummary
    full_text_url: str | None = None
    sponsoring_ministries: list[SponsoringMinistry] = Field(default_factory=list)
    # Routes must pre-resolve authors via
    # ``app.services.proyectos.build_author_briefs`` so each author's
    # chamber/party reflects the term covering ``Bill.entry_date``.
    authors: list[Author] = Field(default_factory=list)
    stages: list[Stage] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    urgencies: list[Urgency] = Field(default_factory=list)
    voting_sessions: list[VotingResult] = Field(default_factory=list)


# ── Response envelopes ────────────────────────────────────────────────


class BillsResponse(CountResponse[BillSummary]):
    data: list[BillSummary] = Field(default_factory=list)

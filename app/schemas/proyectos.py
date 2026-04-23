from datetime import date, datetime

from pydantic import Field

from app.schemas.common import CountResponse, ORMModel


# ── Nested read-only objects ──────────────────────────────────────────

class TopicBrief(ORMModel):
    id: int
    name: str
    slug: str
    icon: str | None = None


class ChamberBrief(ORMModel):
    id: int
    chamber_type: str
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
    id: int
    full_name: str
    chamber_type: str
    photo_thumbnail_url: str | None = None
    party: PartyBrief | None = None


class Author(ORMModel):
    id: int
    author_type: str
    legislator: LegislatorBrief


class Stage(ORMModel):
    id: int
    stage_type: str
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
    urgency_type: str
    chamber: ChamberBrief
    entry_date: date
    withdrawal_date: date | None = None
    deadline_date: date | None = None
    is_active: bool


class VotingResult(ORMModel):
    id: int
    bcn_id: str | None = None
    chamber: ChamberBrief
    voting_date: datetime
    voting_type: str
    subject: str
    result: str | None = None
    votes_for: int
    votes_against: int
    abstentions: int
    absences: int
    quorum_type: str | None = None


# ── List / summary schema ─────────────────────────────────────────────

class BillSummary(ORMModel):
    """Compact bill for list views — no nested lifecycle data."""
    id: int
    bulletin_number: str
    title: str
    bill_type: str
    origin: str
    status: str
    entry_date: date
    publication_date: date | None = None
    law_number: str | None = None
    origin_chamber: ChamberBrief | None = None
    current_chamber: ChamberBrief | None = None
    current_committee: CommitteeBrief | None = None
    topics: list[TopicBrief] = Field(default_factory=list)
    # Denormalised convenience fields populated by the service layer
    active_urgency_type: str | None = None
    current_stage_type: str | None = None
    created_at: datetime
    updated_at: datetime
    sync_version: int


# ── Detail schema ─────────────────────────────────────────────────────

class BillDetail(BillSummary):
    """Full bill lifecycle: stages, events, documents, votes, authors."""
    summary: str | None = None
    ai_summary: str | None = None
    full_text_url: str | None = None
    authors: list[Author] = Field(default_factory=list)
    stages: list[Stage] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)
    urgencies: list[Urgency] = Field(default_factory=list)
    voting_sessions: list[VotingResult] = Field(default_factory=list)


# ── Response envelopes ────────────────────────────────────────────────

class BillsResponse(CountResponse[BillSummary]):
    data: list[BillSummary] = Field(default_factory=list)

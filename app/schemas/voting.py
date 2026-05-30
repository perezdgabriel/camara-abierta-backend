from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import (
    ChamberType,
    SignalType,
    VoteChoice,
    VotingResult,
    VotingType,
)
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


class SignalRef(ORMModel):
    """Lightweight signal marker embedded in list responses.

    Carries only what archive UIs need to render tags (type + severity for
    ordering). The full signal ``payload`` ships only on the detail response
    via ``BareSignal`` to keep list payloads lean.
    """

    signal_type: SignalType
    severity: float


class BareSignal(ORMModel):
    """Full signal payload embedded in the detail response.

    Distinct from ``VotingSignal`` (used by ``/voting-sessions/highlighted``)
    which embeds the ``voting_session`` — here the session is the parent, so
    we omit it to avoid recursive nesting.
    """

    signal_type: SignalType
    severity: float
    payload: dict[str, Any]


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
    signals: list[SignalRef] = Field(default_factory=list)
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
    # Override: detail responses include full signal payloads.
    signals: list[BareSignal] = Field(default_factory=list)  # type: ignore[assignment]
    votes: list[VoteDetail] = Field(default_factory=list)


class VotingSessionsResponse(CountResponse[VotingSessionSummary]):
    data: list[VotingSessionSummary] = Field(default_factory=list)


# ── Behavior-revealing signals ─────────────────────────────────────────────


class VotingSignal(ORMModel):
    """One fired signal on a single voting session.

    ``payload`` is signal-type-specific; the frontend has fully typed
    discriminated-union payloads keyed on ``signal_type``. The API keeps it
    as a free-form object for v1 to avoid coupling schema versioning to four
    parallel payload types.
    """

    signal_type: SignalType
    severity: float
    session: VotingSessionSummary = Field(..., alias="voting_session")
    payload: dict[str, Any]

    model_config = {"from_attributes": True, "populate_by_name": True}


class VotingAggregates(ORMModel):
    window_days: int
    computed_at: datetime
    approval_rate: float
    avg_cohesion: float | None = None
    avg_attendance: float
    volume: int
    signals_active: int


class HighlightedResponse(ORMModel):
    """Driver payload for the ``/votaciones`` editorial section.

    ``primary`` is the hero; ``grid`` is the strip below it; ``fallback_high_turnout``
    is always populated so the UI can render *Mayor convocatoria* when no
    signal fires in the window.
    """

    primary: VotingSignal | None = None
    grid: list[VotingSignal] = Field(default_factory=list)
    fallback_high_turnout: list[VotingSessionSummary] = Field(default_factory=list)

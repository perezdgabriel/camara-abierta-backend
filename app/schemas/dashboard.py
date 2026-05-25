from datetime import date

from pydantic import Field

from app.schemas.common import ORMModel
from app.schemas.proyectos import (
    BillSummary,
    ChamberBrief,
    PartyBrief,
    TopicBrief,
)


class DashboardStats(ORMModel):
    bills_active: int
    bills_with_urgency: int
    voted_this_week: int
    enacted_this_year: int


class EventBillBrief(ORMModel):
    id: int
    bulletin_number: str
    title: str


class RecentEvent(ORMModel):
    id: int
    event_date: date
    title: str
    chamber: ChamberBrief | None = None
    bill: EventBillBrief | None = None


class TopicCount(ORMModel):
    topic: TopicBrief
    count: int


class PartyComposition(ORMModel):
    party: PartyBrief
    count: int


class DashboardResponse(ORMModel):
    stats: DashboardStats
    recent_events: list[RecentEvent] = Field(default_factory=list)
    topic_distribution: list[TopicCount] = Field(default_factory=list)
    chamber_composition: list[PartyComposition] = Field(default_factory=list)
    featured_bills: list[BillSummary] = Field(default_factory=list)

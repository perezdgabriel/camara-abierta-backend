from datetime import datetime

from app.models.enums import (
    CalendarEventKind,
    CalendarEventSource,
    ChamberType,
)
from app.schemas.common import CountResponse, ORMModel


class CalendarBillBrief(ORMModel):
    id: int
    bulletin_number: str
    title: str | None = None


class CalendarLegislatorBrief(ORMModel):
    id: int
    full_name: str
    photo_thumbnail_url: str | None = None


class CalendarCommitteeBrief(ORMModel):
    id: int
    name: str


class CalendarEventOut(ORMModel):
    id: int
    kind: CalendarEventKind
    starts_at: datetime
    ends_at: datetime | None = None
    title: str
    description: str | None = None
    location: str | None = None
    chamber_type: ChamberType | None = None
    bill: CalendarBillBrief | None = None
    legislator: CalendarLegislatorBrief | None = None
    committee: CalendarCommitteeBrief | None = None
    source: CalendarEventSource
    external_ref: str | None = None


CalendarEventsResponse = CountResponse[CalendarEventOut]

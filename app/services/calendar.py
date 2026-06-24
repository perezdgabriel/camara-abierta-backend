from collections.abc import Sequence
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.enums import CalendarEventKind, ChamberType
from app.models.legislature import CalendarEvent

DEFAULT_OFFSET = 0
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_WINDOW_DAYS = 14


def list_events(
    db: Session,
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    kinds: list[CalendarEventKind] | None = None,
    chamber_type: ChamberType | None = None,
    offset: int = DEFAULT_OFFSET,
    limit: int = DEFAULT_LIMIT,
) -> tuple[int, Sequence[CalendarEvent]]:
    """List forward-looking calendar events within a date window.

    Default window is today through today + 14 days. ``start_date`` and
    ``end_date`` are inclusive day boundaries interpreted as UTC midnight on
    both ends (the calendar is loose enough that timezone drift at the
    boundary is acceptable; tightening to America/Santiago is a follow-up).
    """

    today = date.today()
    window_start = start_date or today
    window_end = end_date or (today + timedelta(days=DEFAULT_WINDOW_DAYS))
    start_dt = datetime.combine(window_start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(
        window_end + timedelta(days=1), time.min, tzinfo=timezone.utc
    )

    filters = [
        CalendarEvent.deleted_at.is_(None),
        CalendarEvent.starts_at >= start_dt,
        CalendarEvent.starts_at < end_dt,
    ]
    if kinds:
        filters.append(CalendarEvent.kind.in_(kinds))
    if chamber_type is not None:
        filters.append(CalendarEvent.chamber_type == chamber_type)

    total = db.execute(
        select(func.count()).select_from(CalendarEvent).where(*filters)
    ).scalar_one()

    rows = (
        db.execute(
            select(CalendarEvent)
            .options(
                joinedload(CalendarEvent.bill),
                joinedload(CalendarEvent.legislator),
                joinedload(CalendarEvent.committee),
            )
            .where(*filters)
            .order_by(CalendarEvent.starts_at.asc(), CalendarEvent.id.asc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .unique()
        .all()
    )
    return total, rows

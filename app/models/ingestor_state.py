from datetime import date

from sqlalchemy import Date, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import BigIntPrimaryKeyMixin, TimestampMixin


class IngestorState(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ingestor_state"

    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    last_sync_date: Mapped[date | None] = mapped_column(Date)
    last_cursor: Mapped[str | None] = mapped_column(Text)

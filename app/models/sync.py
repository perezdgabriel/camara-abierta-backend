from datetime import datetime

from sqlalchemy import BigInteger, DateTime, JSON, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import BigIntPrimaryKeyMixin, TimestampMixin


class ClientSyncState(BigIntPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "client_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "device_id", "entity_type", name="uq_client_sync_states_device_entity"
        ),
    )

    device_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    last_sync_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    last_sync_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChangeLog(BigIntPrimaryKeyMixin, Base):
    __tablename__ = "change_logs"

    entity_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    sync_version: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    changed_fields: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

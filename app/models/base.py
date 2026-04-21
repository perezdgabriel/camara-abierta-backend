from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Sequence, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

global_sync_version_seq = Sequence("global_sync_version_seq", metadata=Base.metadata)


class BigIntPrimaryKeyMixin:
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )


class SyncVersionMixin:
    sync_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        index=True,
        server_default=global_sync_version_seq.next_value(),
    )


class SyncableMixin(BigIntPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, SyncVersionMixin):
    pass

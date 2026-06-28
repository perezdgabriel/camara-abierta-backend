from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SyncableMixin
from app.models.core import Topic
from app.models.enums import BillOrigin, BillStatus, BillType, StageType, UrgencyType
from app.models.legislature import Chamber, Committee, Legislator

if TYPE_CHECKING:
    from app.models.votacion import VotingSession

bill_topics = Table(
    "bill_topics",
    Base.metadata,
    Column("bill_id", ForeignKey("bills.id", ondelete="CASCADE"), primary_key=True),
    Column("topic_id", ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True),
)


class Bill(SyncableMixin, Base):
    __tablename__ = "bills"

    bcn_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    bulletin_number: Mapped[str] = mapped_column(
        String(50), nullable=False, unique=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    bill_type: Mapped[BillType] = mapped_column(
        SqlEnum(BillType, name="bill_type", native_enum=False, validate_strings=True),
        nullable=False,
        default=BillType.PROJECT,
    )
    origin: Mapped[BillOrigin] = mapped_column(
        SqlEnum(
            BillOrigin, name="bill_origin", native_enum=False, validate_strings=True
        ),
        nullable=False,
    )
    origin_chamber_id: Mapped[int | None] = mapped_column(
        ForeignKey("chambers.id", ondelete="RESTRICT")
    )
    status: Mapped[BillStatus] = mapped_column(
        SqlEnum(
            BillStatus, name="bill_status", native_enum=False, validate_strings=True
        ),
        nullable=False,
        default=BillStatus.PENDING,
    )
    current_chamber_id: Mapped[int | None] = mapped_column(
        ForeignKey("chambers.id", ondelete="SET NULL")
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    publication_date: Mapped[date | None] = mapped_column(Date)
    law_number: Mapped[str | None] = mapped_column(String(50))
    current_committee_id: Mapped[int | None] = mapped_column(
        ForeignKey("committees.id", ondelete="SET NULL")
    )
    full_text_url: Mapped[str | None] = mapped_column(String(500))
    full_text: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    ai_summary_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    origin_chamber: Mapped[Chamber | None] = relationship(
        back_populates="originated_bills", foreign_keys=[origin_chamber_id]
    )
    current_chamber: Mapped[Chamber | None] = relationship(
        back_populates="current_bills", foreign_keys=[current_chamber_id]
    )
    current_committee: Mapped[Committee | None] = relationship(
        back_populates="current_bills"
    )
    topics: Mapped[list[Topic]] = relationship(
        secondary=bill_topics, back_populates="bills"
    )
    authorships: Mapped[list["BillAuthorship"]] = relationship(back_populates="bill")
    stages: Mapped[list["BillStage"]] = relationship(back_populates="bill")
    urgencies: Mapped[list["BillUrgency"]] = relationship(back_populates="bill")
    documents: Mapped[list["BillDocument"]] = relationship(back_populates="bill")
    events: Mapped[list["BillEvent"]] = relationship(back_populates="bill")
    sponsoring_ministries: Mapped[list["BillSponsoringMinistry"]] = relationship(
        back_populates="bill"
    )
    voting_sessions: Mapped[list["VotingSession"]] = relationship(back_populates="bill")

    def __str__(self) -> str:
        title = self.title if len(self.title) <= 80 else f"{self.title[:77]}..."
        return f"Boletin {self.bulletin_number} - {title}"


class BillSponsoringMinistry(SyncableMixin, Base):
    __tablename__ = "bill_sponsoring_ministries"

    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int | None] = mapped_column()
    name: Mapped[str | None] = mapped_column(String(200))

    bill: Mapped[Bill] = relationship(back_populates="sponsoring_ministries")

    def __str__(self) -> str:
        return self.name or f"Ministerio patrocinante {self.source_id}"


class BillAuthorship(SyncableMixin, Base):
    __tablename__ = "bill_authorships"
    __table_args__ = (
        UniqueConstraint(
            "bill_id", "legislator_id", name="uq_bill_authorships_bill_legislator"
        ),
    )

    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False
    )
    legislator_id: Mapped[int] = mapped_column(
        ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False
    )
    author_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="author"
    )

    bill: Mapped[Bill] = relationship(back_populates="authorships")
    legislator: Mapped[Legislator] = relationship(back_populates="authored_bills")

    def __str__(self) -> str:
        return f"{self.author_type} - legislador {self.legislator_id}"


class BillStage(SyncableMixin, Base):
    __tablename__ = "bill_stages"

    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False
    )
    stage_type: Mapped[StageType] = mapped_column(
        SqlEnum(StageType, name="stage_type", native_enum=False, validate_strings=True),
        nullable=False,
    )
    chamber_id: Mapped[int | None] = mapped_column(
        ForeignKey("chambers.id", ondelete="SET NULL")
    )
    committee_id: Mapped[int | None] = mapped_column(
        ForeignKey("committees.id", ondelete="SET NULL")
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    result: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    bill: Mapped[Bill] = relationship(back_populates="stages")
    chamber: Mapped[Chamber | None] = relationship()
    committee: Mapped[Committee | None] = relationship()
    documents: Mapped[list["BillDocument"]] = relationship(back_populates="bill_stage")
    events: Mapped[list["BillEvent"]] = relationship(back_populates="bill_stage")
    voting_sessions: Mapped[list["VotingSession"]] = relationship(
        back_populates="bill_stage"
    )

    def __str__(self) -> str:
        stage_type = self.stage_type or "Tramite"
        date_label = self.start_date.isoformat() if self.start_date else "sin fecha"
        return f"{stage_type} - {date_label}"


class BillUrgency(SyncableMixin, Base):
    __tablename__ = "bill_urgencies"

    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False
    )
    urgency_type: Mapped[UrgencyType] = mapped_column(
        SqlEnum(
            UrgencyType, name="urgency_type", native_enum=False, validate_strings=True
        ),
        nullable=False,
    )
    chamber_id: Mapped[int] = mapped_column(
        ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    withdrawal_date: Mapped[date | None] = mapped_column(Date)
    deadline_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    bill: Mapped[Bill] = relationship(back_populates="urgencies")
    chamber: Mapped[Chamber] = relationship()

    def __str__(self) -> str:
        return f"{self.urgency_type} - {self.entry_date.isoformat()}"


class BillDocument(SyncableMixin, Base):
    __tablename__ = "bill_documents"

    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False
    )
    bill_stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("bill_stages.id", ondelete="SET NULL")
    )
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    document_url: Mapped[str | None] = mapped_column(String(500))
    document_date: Mapped[date | None] = mapped_column(Date)

    bill: Mapped[Bill] = relationship(back_populates="documents")
    bill_stage: Mapped[BillStage | None] = relationship(back_populates="documents")

    def __str__(self) -> str:
        return self.title


class BillEvent(SyncableMixin, Base):
    __tablename__ = "bill_events"

    bill_id: Mapped[int] = mapped_column(
        ForeignKey("bills.id", ondelete="CASCADE"), nullable=False
    )
    bill_stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("bill_stages.id", ondelete="SET NULL")
    )
    chamber_id: Mapped[int | None] = mapped_column(
        ForeignKey("chambers.id", ondelete="SET NULL")
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    bill: Mapped[Bill] = relationship(back_populates="events")
    bill_stage: Mapped[BillStage | None] = relationship(back_populates="events")
    chamber: Mapped[Chamber | None] = relationship()

    def __str__(self) -> str:
        return f"{self.event_date.isoformat()} - {self.title}"

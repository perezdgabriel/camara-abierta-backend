from __future__ import annotations

from datetime import datetime

from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SyncableMixin
from app.models.enums import VoteChoice, VotingResult, VotingType
from app.models.legislature import Chamber, LegislativeSession, Legislator
from app.models.proyecto import Bill, BillStage


class VotingSession(SyncableMixin, Base):
    __tablename__ = "voting_sessions"

    bcn_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    chamber_id: Mapped[int] = mapped_column(
        ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("legislative_sessions.id", ondelete="SET NULL")
    )
    bill_id: Mapped[int | None] = mapped_column(
        ForeignKey("bills.id", ondelete="SET NULL")
    )
    bill_stage_id: Mapped[int | None] = mapped_column(
        ForeignKey("bill_stages.id", ondelete="SET NULL")
    )
    voting_type: Mapped[VotingType] = mapped_column(
        SqlEnum(
            VotingType, name="voting_type", native_enum=False, validate_strings=True
        ),
        nullable=False,
        default=VotingType.GENERAL,
    )
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    voting_date: Mapped[datetime] = mapped_column(nullable=False)
    result: Mapped[VotingResult | None] = mapped_column(
        SqlEnum(
            VotingResult, name="voting_result", native_enum=False, validate_strings=True
        )
    )
    votes_for: Mapped[int] = mapped_column(nullable=False, default=0)
    votes_against: Mapped[int] = mapped_column(nullable=False, default=0)
    abstentions: Mapped[int] = mapped_column(nullable=False, default=0)
    absences: Mapped[int] = mapped_column(nullable=False, default=0)
    quorum_required: Mapped[int | None] = mapped_column()
    quorum_type: Mapped[str | None] = mapped_column(String(100))

    chamber: Mapped[Chamber] = relationship()
    session: Mapped[LegislativeSession | None] = relationship(
        back_populates="voting_sessions"
    )
    bill: Mapped[Bill | None] = relationship(back_populates="voting_sessions")
    bill_stage: Mapped[BillStage | None] = relationship(
        back_populates="voting_sessions"
    )
    votes: Mapped[list["Vote"]] = relationship(back_populates="voting_session")

    def __str__(self) -> str:
        subject = self.subject if len(self.subject) <= 80 else f"{self.subject[:77]}..."
        return f"{self.voting_date.date().isoformat()} - {subject}"


class Vote(SyncableMixin, Base):
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint(
            "voting_session_id", "legislator_id", name="uq_votes_session_legislator"
        ),
    )

    voting_session_id: Mapped[int] = mapped_column(
        ForeignKey("voting_sessions.id", ondelete="CASCADE"), nullable=False
    )
    legislator_id: Mapped[int] = mapped_column(
        ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False
    )
    vote: Mapped[VoteChoice] = mapped_column(
        SqlEnum(
            VoteChoice, name="vote_choice", native_enum=False, validate_strings=True
        ),
        nullable=False,
    )

    voting_session: Mapped[VotingSession] = relationship(back_populates="votes")
    legislator: Mapped[Legislator] = relationship(back_populates="votes")

    def __str__(self) -> str:
        return f"{self.vote} - legislador {self.legislator_id}"


class LegislatorVotingStats(SyncableMixin, Base):
    __tablename__ = "legislator_voting_stats"

    legislator_id: Mapped[int] = mapped_column(
        ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    total_sessions: Mapped[int] = mapped_column(nullable=False, default=0)
    votes_for: Mapped[int] = mapped_column(nullable=False, default=0)
    votes_against: Mapped[int] = mapped_column(nullable=False, default=0)
    abstentions: Mapped[int] = mapped_column(nullable=False, default=0)
    absences: Mapped[int] = mapped_column(nullable=False, default=0)
    attendance_percentage: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0
    )
    participation_rate: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0
    )
    stats_updated_at: Mapped[datetime] = mapped_column(nullable=False)

    legislator: Mapped[Legislator] = relationship(back_populates="voting_stats")

    def __str__(self) -> str:
        return f"Stats legislador {self.legislator_id}"

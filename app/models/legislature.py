from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SyncableMixin
from app.models.core import Circumscription, District


class PoliticalParty(SyncableMixin, Base):
    __tablename__ = "political_parties"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    abbreviation: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    founded_date: Mapped[date | None] = mapped_column(Date)
    logo_url: Mapped[str | None] = mapped_column(String(500))
    website: Mapped[str | None] = mapped_column(String(500))
    color: Mapped[str | None] = mapped_column(String(7))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    legislators: Mapped[list["Legislator"]] = relationship(back_populates="party")
    coalitions: Mapped[list["CoalitionMembership"]] = relationship(back_populates="party")


class Coalition(SyncableMixin, Base):
    __tablename__ = "coalitions"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    memberships: Mapped[list["CoalitionMembership"]] = relationship(back_populates="coalition")


class CoalitionMembership(SyncableMixin, Base):
    __tablename__ = "coalition_memberships"
    __table_args__ = (
        UniqueConstraint("coalition_id", "party_id", "joined_date", name="uq_coalition_memberships_membership"),
    )

    coalition_id: Mapped[int] = mapped_column(ForeignKey("coalitions.id", ondelete="CASCADE"), nullable=False)
    party_id: Mapped[int] = mapped_column(ForeignKey("political_parties.id", ondelete="CASCADE"), nullable=False)
    joined_date: Mapped[date] = mapped_column(Date, nullable=False)
    left_date: Mapped[date | None] = mapped_column(Date)

    coalition: Mapped[Coalition] = relationship(back_populates="memberships")
    party: Mapped[PoliticalParty] = relationship(back_populates="coalitions")


class LegislativePeriod(SyncableMixin, Base):
    __tablename__ = "legislative_periods"

    number: Mapped[int] = mapped_column(nullable=False, unique=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))

    sessions: Mapped[list["LegislativeSession"]] = relationship(back_populates="period")
    terms: Mapped[list["LegislatorTerm"]] = relationship(back_populates="period")


class Chamber(SyncableMixin, Base):
    __tablename__ = "chambers"

    chamber_type: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    total_seats: Mapped[int] = mapped_column(nullable=False)
    website: Mapped[str | None] = mapped_column(String(500))

    sessions: Mapped[list["LegislativeSession"]] = relationship(back_populates="chamber")
    committees: Mapped[list["Committee"]] = relationship(back_populates="chamber")
    originated_bills: Mapped[list["Bill"]] = relationship(back_populates="origin_chamber", foreign_keys="Bill.origin_chamber_id")
    current_bills: Mapped[list["Bill"]] = relationship(back_populates="current_chamber", foreign_keys="Bill.current_chamber_id")


class Legislator(SyncableMixin, Base):
    __tablename__ = "legislators"

    bcn_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(1))
    birth_date: Mapped[date | None] = mapped_column(Date)
    profession: Mapped[str | None] = mapped_column(String(200))
    biography: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(String(500))
    photo_thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    website: Mapped[str | None] = mapped_column(String(500))
    twitter_handle: Mapped[str | None] = mapped_column(String(50))
    instagram_handle: Mapped[str | None] = mapped_column(String(50))
    facebook_url: Mapped[str | None] = mapped_column(String(500))
    chamber_type: Mapped[str] = mapped_column(String(10), nullable=False)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("political_parties.id", ondelete="SET NULL"))
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id", ondelete="SET NULL"))
    circumscription_id: Mapped[int | None] = mapped_column(ForeignKey("circumscriptions.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    party: Mapped[PoliticalParty | None] = relationship(back_populates="legislators")
    district: Mapped[District | None] = relationship(back_populates="legislators")
    circumscription: Mapped[Circumscription | None] = relationship(back_populates="legislators")
    terms: Mapped[list["LegislatorTerm"]] = relationship(back_populates="legislator")
    committee_memberships: Mapped[list["CommitteeMembership"]] = relationship(back_populates="legislator")
    authored_bills: Mapped[list["BillAuthorship"]] = relationship(back_populates="legislator")
    votes: Mapped[list["Vote"]] = relationship(back_populates="legislator")
    voting_stats: Mapped["LegislatorVotingStats"] = relationship(back_populates="legislator", uselist=False)


class LegislatorTerm(SyncableMixin, Base):
    __tablename__ = "legislator_terms"

    legislator_id: Mapped[int] = mapped_column(ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False)
    period_id: Mapped[int] = mapped_column(ForeignKey("legislative_periods.id", ondelete="RESTRICT"), nullable=False)
    chamber_id: Mapped[int] = mapped_column(ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False)
    party_id: Mapped[int | None] = mapped_column(ForeignKey("political_parties.id", ondelete="SET NULL"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    end_reason: Mapped[str | None] = mapped_column(String(200))

    legislator: Mapped[Legislator] = relationship(back_populates="terms")
    period: Mapped[LegislativePeriod] = relationship(back_populates="terms")
    chamber: Mapped[Chamber] = relationship()
    party: Mapped[PoliticalParty | None] = relationship()


class Committee(SyncableMixin, Base):
    __tablename__ = "committees"

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    chamber_id: Mapped[int | None] = mapped_column(ForeignKey("chambers.id", ondelete="CASCADE"))
    committee_type: Mapped[str] = mapped_column(String(20), nullable=False, default="permanent")
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    chamber: Mapped[Chamber | None] = relationship(back_populates="committees")
    memberships: Mapped[list["CommitteeMembership"]] = relationship(back_populates="committee")
    current_bills: Mapped[list["Bill"]] = relationship(back_populates="current_committee")


class CommitteeMembership(SyncableMixin, Base):
    __tablename__ = "committee_memberships"

    committee_id: Mapped[int] = mapped_column(ForeignKey("committees.id", ondelete="CASCADE"), nullable=False)
    legislator_id: Mapped[int] = mapped_column(ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)

    committee: Mapped[Committee] = relationship(back_populates="memberships")
    legislator: Mapped[Legislator] = relationship(back_populates="committee_memberships")


class LegislativeSession(SyncableMixin, Base):
    __tablename__ = "legislative_sessions"

    number: Mapped[int] = mapped_column(nullable=False)
    session_type: Mapped[str] = mapped_column(String(30), nullable=False)
    period_id: Mapped[int] = mapped_column(ForeignKey("legislative_periods.id", ondelete="RESTRICT"), nullable=False)
    chamber_id: Mapped[int] = mapped_column(ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(200))

    period: Mapped[LegislativePeriod] = relationship(back_populates="sessions")
    chamber: Mapped[Chamber] = relationship(back_populates="sessions")
    voting_sessions: Mapped[list["VotingSession"]] = relationship(back_populates="session")

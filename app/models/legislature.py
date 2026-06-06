from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SyncableMixin
from app.models.core import Circumscription, District
from app.models.enums import Bloc, ChamberType, CommitteeType

if TYPE_CHECKING:
    from app.models.proyecto import Bill, BillAuthorship
    from app.models.votacion import LegislatorVotingStats, Vote, VotingSession


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
    coalitions: Mapped[list["CoalitionMembership"]] = relationship(
        back_populates="party"
    )
    bloc_affiliations: Mapped[list["BlocAffiliation"]] = relationship(
        back_populates="party",
        cascade="all, delete-orphan",
        order_by="BlocAffiliation.start_date.desc()",
    )

    @property
    def current_bloc(self) -> Bloc | None:
        """The party's bloc as of today, from the active ``BlocAffiliation`` row.

        Reads from the (ideally eager-loaded) ``bloc_affiliations`` relationship;
        callers should ``selectinload`` it to avoid N+1. Returns ``None`` when the
        party has no editorial bloc assignment. See ADR-0006.
        """
        today = date.today()
        active = [
            affiliation
            for affiliation in self.bloc_affiliations
            if affiliation.start_date <= today
            and (affiliation.end_date is None or affiliation.end_date > today)
        ]
        if not active:
            return None
        return max(active, key=lambda affiliation: affiliation.start_date).bloc

    def __str__(self) -> str:
        if self.abbreviation and self.abbreviation != self.name:
            return f"{self.name} ({self.abbreviation})"
        return self.name


class BlocAffiliation(SyncableMixin, Base):
    """A dated assignment of a political party to a structural bloc.

    Editorial data (oficialismo/oposición) with no upstream source. Modeled
    temporally — one row per (party, start_date) — so a change of government can
    be recorded by closing the old row (``end_date``) and opening a new one. v1
    UI consumes only the current row via :attr:`PoliticalParty.current_bloc`.
    See ADR-0006.
    """

    __tablename__ = "bloc_affiliations"
    __table_args__ = (
        UniqueConstraint(
            "party_id",
            "start_date",
            name="uq_bloc_affiliations_party_start",
        ),
    )

    party_id: Mapped[int] = mapped_column(
        ForeignKey("political_parties.id", ondelete="CASCADE"), nullable=False
    )
    bloc: Mapped[Bloc] = mapped_column(
        SqlEnum(Bloc, name="bloc", native_enum=False, validate_strings=True),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)

    party: Mapped[PoliticalParty] = relationship(back_populates="bloc_affiliations")

    def __str__(self) -> str:
        return f"{self.party_id} → {self.bloc.value} ({self.start_date})"


class Coalition(SyncableMixin, Base):
    __tablename__ = "coalitions"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    memberships: Mapped[list["CoalitionMembership"]] = relationship(
        back_populates="coalition"
    )

    def __str__(self) -> str:
        if self.abbreviation and self.abbreviation != self.name:
            return f"{self.name} ({self.abbreviation})"
        return self.name


class CoalitionMembership(SyncableMixin, Base):
    __tablename__ = "coalition_memberships"
    __table_args__ = (
        UniqueConstraint(
            "coalition_id",
            "party_id",
            "joined_date",
            name="uq_coalition_memberships_membership",
        ),
    )

    coalition_id: Mapped[int] = mapped_column(
        ForeignKey("coalitions.id", ondelete="CASCADE"), nullable=False
    )
    party_id: Mapped[int] = mapped_column(
        ForeignKey("political_parties.id", ondelete="CASCADE"), nullable=False
    )
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

    def __str__(self) -> str:
        return f"Periodo {self.number}"


class Chamber(SyncableMixin, Base):
    __tablename__ = "chambers"

    chamber_type: Mapped[ChamberType] = mapped_column(
        SqlEnum(
            ChamberType, name="chamber_type", native_enum=False, validate_strings=True
        ),
        nullable=False,
        unique=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    total_seats: Mapped[int] = mapped_column(nullable=False)
    website: Mapped[str | None] = mapped_column(String(500))

    sessions: Mapped[list["LegislativeSession"]] = relationship(
        back_populates="chamber"
    )
    committees: Mapped[list["Committee"]] = relationship(back_populates="chamber")
    originated_bills: Mapped[list["Bill"]] = relationship(
        back_populates="origin_chamber", foreign_keys="Bill.origin_chamber_id"
    )
    current_bills: Mapped[list["Bill"]] = relationship(
        back_populates="current_chamber", foreign_keys="Bill.current_chamber_id"
    )

    def __str__(self) -> str:
        return self.name


class Legislator(SyncableMixin, Base):
    __tablename__ = "legislators"

    bcn_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    bcn_uri: Mapped[str | None] = mapped_column(String(500), unique=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    gender: Mapped[str | None] = mapped_column(String(1))
    birth_date: Mapped[date | None] = mapped_column(Date)
    profession: Mapped[str | None] = mapped_column(String(200))
    biography: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(String(500))
    photo_thumbnail_url: Mapped[str | None] = mapped_column(String(500))
    profile_url: Mapped[str | None] = mapped_column(String(500))
    bcn_wiki_url: Mapped[str | None] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    website: Mapped[str | None] = mapped_column(String(500))
    twitter_handle: Mapped[str | None] = mapped_column(String(50))
    instagram_handle: Mapped[str | None] = mapped_column(String(50))
    facebook_url: Mapped[str | None] = mapped_column(String(500))
    chamber_type: Mapped[ChamberType] = mapped_column(
        SqlEnum(
            ChamberType,
            name="legislator_chamber_type",
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    party_id: Mapped[int | None] = mapped_column(
        ForeignKey("political_parties.id", ondelete="SET NULL")
    )
    district_id: Mapped[int | None] = mapped_column(
        ForeignKey("districts.id", ondelete="SET NULL")
    )
    circumscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("circumscriptions.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_bloc: Mapped[Bloc | None] = mapped_column(
        SqlEnum(
            Bloc,
            name="legislator_default_bloc",
            native_enum=False,
            validate_strings=True,
        )
    )

    party: Mapped[PoliticalParty | None] = relationship(back_populates="legislators")
    district: Mapped[District | None] = relationship(back_populates="legislators")
    circumscription: Mapped[Circumscription | None] = relationship(
        back_populates="legislators"
    )
    terms: Mapped[list["LegislatorTerm"]] = relationship(back_populates="legislator")
    appointments: Mapped[list["ParliamentaryAppointment"]] = relationship(
        back_populates="legislator"
    )
    committee_memberships: Mapped[list["CommitteeMembership"]] = relationship(
        back_populates="legislator"
    )
    authored_bills: Mapped[list["BillAuthorship"]] = relationship(
        back_populates="legislator"
    )
    votes: Mapped[list["Vote"]] = relationship(back_populates="legislator")
    voting_stats: Mapped["LegislatorVotingStats"] = relationship(
        back_populates="legislator", uselist=False
    )

    @property
    def voting_lean(self) -> dict | None:
        """Inclinación de voto for the API, projected from ``voting_stats``.

        Null when there is no stats row or too few contested sessions (*datos
        insuficientes*). ``bloc`` may still be null on an exact split. Callers
        should eager-load ``voting_stats`` to avoid N+1. See ADR-0007.
        """
        stats = self.voting_stats
        if stats is None or stats.lean_contested == 0:
            return None
        return {
            "bloc": stats.inferred_bloc,
            "agreed": stats.lean_agreed,
            "contested": stats.lean_contested,
            "seats": stats.lean_seats,
        }

    @property
    def party_discipline(self) -> dict | None:
        """Disciplina partidaria for the API. Only for current party members —
        an independent has no party to measure against (see web CONTEXT.md)."""
        stats = self.voting_stats
        if stats is None or self.party_id is None or stats.discipline_decided == 0:
            return None
        return {
            "rate": (
                float(stats.discipline_rate)
                if stats.discipline_rate is not None
                else None
            ),
            "with_party": stats.discipline_with,
            "decided": stats.discipline_decided,
        }

    def __str__(self) -> str:
        return self.full_name


class LegislatorTerm(SyncableMixin, Base):
    __tablename__ = "legislator_terms"

    legislator_id: Mapped[int] = mapped_column(
        ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False
    )
    period_id: Mapped[int] = mapped_column(
        ForeignKey("legislative_periods.id", ondelete="RESTRICT"), nullable=False
    )
    chamber_id: Mapped[int] = mapped_column(
        ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False
    )
    party_id: Mapped[int | None] = mapped_column(
        ForeignKey("political_parties.id", ondelete="SET NULL")
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    end_reason: Mapped[str | None] = mapped_column(String(200))

    legislator: Mapped[Legislator] = relationship(back_populates="terms")
    period: Mapped[LegislativePeriod] = relationship(back_populates="terms")
    chamber: Mapped[Chamber] = relationship()
    party: Mapped[PoliticalParty | None] = relationship()


class ParliamentaryAppointment(SyncableMixin, Base):
    """A single parliamentary appointment (BCN ``PositionPeriod``).

    One row per ``bcnbio:hasParliamentaryAppointment`` triple in the BCN graph:
    the formal, dated record that legislator X served in chamber Y from
    ``start_date`` to ``end_date``. Distinct from :class:`LegislatorTerm`, which
    tracks party-membership windows derived from OpenData militancias and may
    record several rows per appointment (one per party change). See ADR-0005.
    """

    __tablename__ = "parliamentary_appointments"

    legislator_id: Mapped[int] = mapped_column(
        ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False
    )
    chamber_id: Mapped[int] = mapped_column(
        ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False
    )
    bcn_appointment_uri: Mapped[str] = mapped_column(
        String(500), nullable=False, unique=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    legislator: Mapped[Legislator] = relationship(back_populates="appointments")
    chamber: Mapped[Chamber] = relationship()


class Committee(SyncableMixin, Base):
    __tablename__ = "committees"

    name: Mapped[str] = mapped_column(String(300), nullable=False)
    chamber_id: Mapped[int | None] = mapped_column(
        ForeignKey("chambers.id", ondelete="CASCADE")
    )
    committee_type: Mapped[CommitteeType] = mapped_column(
        SqlEnum(
            CommitteeType,
            name="committee_type",
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        default=CommitteeType.PERMANENT,
    )
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    chamber: Mapped[Chamber | None] = relationship(back_populates="committees")
    memberships: Mapped[list["CommitteeMembership"]] = relationship(
        back_populates="committee"
    )
    current_bills: Mapped[list["Bill"]] = relationship(
        back_populates="current_committee"
    )

    def __str__(self) -> str:
        return self.name


class CommitteeMembership(SyncableMixin, Base):
    __tablename__ = "committee_memberships"

    committee_id: Mapped[int] = mapped_column(
        ForeignKey("committees.id", ondelete="CASCADE"), nullable=False
    )
    legislator_id: Mapped[int] = mapped_column(
        ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)

    committee: Mapped[Committee] = relationship(back_populates="memberships")
    legislator: Mapped[Legislator] = relationship(
        back_populates="committee_memberships"
    )


class LegislativeSession(SyncableMixin, Base):
    __tablename__ = "legislative_sessions"

    number: Mapped[int] = mapped_column(nullable=False)
    session_type: Mapped[str] = mapped_column(String(30), nullable=False)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("legislative_periods.id", ondelete="RESTRICT"), nullable=False
    )
    chamber_id: Mapped[int] = mapped_column(
        ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(200))

    period: Mapped[LegislativePeriod] = relationship(back_populates="sessions")
    chamber: Mapped[Chamber] = relationship(back_populates="sessions")
    voting_sessions: Mapped[list["VotingSession"]] = relationship(
        back_populates="session"
    )

    def __str__(self) -> str:
        return f"Sesion {self.number} ({self.session_type})"

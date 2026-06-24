from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SyncableMixin
from app.models.core import Circumscription, District
from app.models.enums import (
    Bloc,
    CalendarEventKind,
    CalendarEventSource,
    ChamberType,
    CommitteeType,
    LegislatureKind,
    SessionKind,
)

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
        party has no editorial bloc assignment. See ADR-0014.
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
    See ADR-0014.
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
    """The 4-year presidential / Chamber-of-Deputies cycle.

    Date range is half-open ``[start_date, end_date)`` — ``end_date`` is the
    start of the next period (Mar 11), not the last day actually covered. See
    CONTEXT.md "Período Legislativo" and ADR-0016.
    """

    __tablename__ = "legislative_periods"

    number: Mapped[int] = mapped_column(nullable=False, unique=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(String(200))

    legislatures: Mapped[list["Legislature"]] = relationship(back_populates="period")
    terms: Mapped[list["LegislatorTerm"]] = relationship(back_populates="period")

    def __str__(self) -> str:
        return f"Periodo {self.number}"


class Legislature(SyncableMixin, Base):
    """The 1-year working cycle of Congress (e.g. Legislatura 374 = 2026–2027).

    ``number`` is the historical sequential count dating to the 19th century,
    sourced from upstream — never synthesized. Date range is half-open
    ``[Mar 11 year N, Mar 11 year N+1)``. See CONTEXT.md "Legislatura" and
    ADR-0016.
    """

    __tablename__ = "legislatures"

    number: Mapped[int] = mapped_column(nullable=False, unique=True)
    period_id: Mapped[int] = mapped_column(
        ForeignKey("legislative_periods.id", ondelete="RESTRICT"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[LegislatureKind] = mapped_column(
        SqlEnum(
            LegislatureKind,
            name="legislature_kind",
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        default=LegislatureKind.ORDINARIA,
    )
    description: Mapped[str | None] = mapped_column(String(200))

    period: Mapped[LegislativePeriod] = relationship(back_populates="legislatures")
    sessions: Mapped[list["LegislativeSession"]] = relationship(
        back_populates="legislature"
    )

    def __str__(self) -> str:
        return f"Legislatura {self.number}"


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
    """A physical parliamentarian, identity-stable across chambers and stints.

    ``Legislator`` represents the person; all chamber-scoped facts (chamber,
    party, district/circumscription, the upstream chamber bridge ID, dates,
    end reason) live on dated :class:`LegislatorTerm` rows. A senator who was
    previously a deputy is one ``Legislator`` with multiple terms.

    ``bcn_uri`` is the canonical cross-chamber identity (BCN's person URI).
    Chamber-side bridge IDs (``camara:{Id}``, ``senado:{PARLID}``) live on
    each term, since they are valid only during the matching stint and a
    person can carry different bridges across stints. See ADR-0015.
    """

    __tablename__ = "legislators"

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
    default_bloc: Mapped[Bloc | None] = mapped_column(
        SqlEnum(
            Bloc,
            name="legislator_default_bloc",
            native_enum=False,
            validate_strings=True,
        )
    )

    terms: Mapped[list["LegislatorTerm"]] = relationship(
        back_populates="legislator", order_by="LegislatorTerm.start_date.desc()"
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
    def active_term(self) -> "LegislatorTerm | None":
        """The currently-open term, or ``None`` if no term covers today.

        Reads from the eager-loadable ``terms`` relationship; callers should
        ``selectinload(Legislator.terms)`` to avoid N+1. When multiple terms
        are open simultaneously (rare — usually a data error) the most-recently
        started one wins.
        """
        today = date.today()
        active = [
            term
            for term in self.terms
            if term.start_date <= today
            and (term.end_date is None or term.end_date >= today)
        ]
        if not active:
            return None
        return max(active, key=lambda term: term.start_date)

    @property
    def is_active(self) -> bool:
        """``True`` when the legislator has an open ``LegislatorTerm``.

        Replaces the legacy stored flag — derived from terms, never written.
        See CONTEXT.md "Active legislator".
        """
        return self.active_term is not None

    @property
    def current_chamber_type(self) -> ChamberType | None:
        term = self.active_term
        return term.chamber.chamber_type if term and term.chamber else None

    @property
    def current_party(self) -> PoliticalParty | None:
        term = self.active_term
        return term.party if term else None

    @property
    def current_party_id(self) -> int | None:
        term = self.active_term
        return term.party_id if term else None

    @property
    def current_district(self) -> District | None:
        term = self.active_term
        return term.district if term else None

    @property
    def current_circumscription(self) -> Circumscription | None:
        term = self.active_term
        return term.circumscription if term else None

    @property
    def current_chamber_external_id(self) -> str | None:
        term = self.active_term
        return term.chamber_external_id if term else None

    def _term_on(self, d: date) -> "LegislatorTerm | None":
        matches = [
            term
            for term in self.terms
            if term.start_date <= d and (term.end_date is None or term.end_date >= d)
        ]
        if not matches:
            return None
        return max(matches, key=lambda term: term.start_date)

    def party_on(self, d: date) -> "PoliticalParty | None":
        """The party from the term whose window covers ``d``.

        Used for vote rows so that historical sessions render the legislator's
        party at the time of the vote, not today's. See CONTEXT.md
        "Vote-time party".
        """
        term = self._term_on(d)
        return term.party if term else None

    def chamber_type_on(self, d: date) -> ChamberType | None:
        term = self._term_on(d)
        return term.chamber.chamber_type if term and term.chamber else None

    @property
    def voting_lean(self) -> dict | None:
        """Inclinación de voto for the API, projected from ``voting_stats``.

        Null when there is no stats row or too few contested sessions (*datos
        insuficientes*). ``bloc`` may still be null on an exact split. Callers
        should eager-load ``voting_stats`` to avoid N+1. See ADR-0014.
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
        if (
            stats is None
            or self.current_party_id is None
            or stats.discipline_decided == 0
        ):
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
    """A dated chamber stint with the party, district/circumscription, and
    upstream chamber bridge ID active during that window.

    Canonical home for everything chamber-scoped about a parliamentarian.
    One row per per-stint party window: a four-year deputy who switches
    party once produces two contiguous terms sharing the same chamber and
    bridge. ``chamber_external_id`` is the upstream ID for the chamber
    holding the stint (``camara:{OpenData Id}`` for deputy stints,
    ``senado:{ID_PARLAMENTARIO}`` for senate stints) and is the primary
    join key from votes to legislators. ``bcn_appointment_uri`` (BCN
    ``PositionPeriod`` URI) is the SPARQL-side upsert key, populated only
    when the out-of-band BCN SPARQL enrichment runs. See ADR-0015.
    """

    __tablename__ = "legislator_terms"
    __table_args__ = (
        Index(
            "ix_legislator_terms_bridge_window",
            "chamber_external_id",
            "start_date",
            "end_date",
        ),
        UniqueConstraint(
            "bcn_appointment_uri",
            name="uq_legislator_terms_bcn_appointment_uri",
        ),
    )

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
    district_id: Mapped[int | None] = mapped_column(
        ForeignKey("districts.id", ondelete="SET NULL")
    )
    circumscription_id: Mapped[int | None] = mapped_column(
        ForeignKey("circumscriptions.id", ondelete="SET NULL")
    )
    chamber_external_id: Mapped[str | None] = mapped_column(String(50))
    bcn_appointment_uri: Mapped[str | None] = mapped_column(String(500))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    end_reason: Mapped[str | None] = mapped_column(String(200))

    legislator: Mapped[Legislator] = relationship(back_populates="terms")
    period: Mapped[LegislativePeriod] = relationship(back_populates="terms")
    chamber: Mapped[Chamber] = relationship()
    party: Mapped[PoliticalParty | None] = relationship()
    district: Mapped[District | None] = relationship()
    circumscription: Mapped[Circumscription | None] = relationship()


class LegislatorMergeCandidate(SyncableMixin, Base):
    """A pending cross-chamber person merge that name + period overlap could
    not auto-resolve.

    Written when ingesting a senator with a deputy-history (or a deputy who
    was previously a senator) whose normalized name matches several existing
    ``Legislator`` rows and whose `PERIODOS`/militancia date ranges don't
    disambiguate cleanly. Resolved manually via the sqladmin panel by
    setting ``resolved_legislator_id`` and ``resolved_at``. See ADR-0015.
    """

    __tablename__ = "legislator_merge_candidates"

    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    candidate_legislator_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    resolved_legislator_id: Mapped[int | None] = mapped_column(
        ForeignKey("legislators.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __str__(self) -> str:
        return f"{self.source}:{self.source_external_id} → {self.full_name}"


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
    """A single scheduled meeting (Sesión Legislativa).

    Venue is encoded by ``committee_id``: ``None`` means a plenary Sala
    session; non-null means a Comisión meeting of that committee. ``chamber_id``
    is retained because committees themselves belong to one chamber. See
    CONTEXT.md "Sesión Legislativa" and ADR-0016.
    """

    __tablename__ = "legislative_sessions"

    number: Mapped[int] = mapped_column(nullable=False)
    kind: Mapped[SessionKind] = mapped_column(
        SqlEnum(
            SessionKind,
            name="session_kind",
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        default=SessionKind.ORDINARIA,
    )
    legislature_id: Mapped[int] = mapped_column(
        ForeignKey("legislatures.id", ondelete="RESTRICT"), nullable=False
    )
    chamber_id: Mapped[int] = mapped_column(
        ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False
    )
    committee_id: Mapped[int | None] = mapped_column(
        ForeignKey("committees.id", ondelete="SET NULL")
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(String(200))

    legislature: Mapped[Legislature] = relationship(back_populates="sessions")
    chamber: Mapped[Chamber] = relationship(back_populates="sessions")
    committee: Mapped["Committee | None"] = relationship()
    voting_sessions: Mapped[list["VotingSession"]] = relationship(
        back_populates="session"
    )

    def __str__(self) -> str:
        return f"Sesion {self.number} ({self.kind.value})"


class CalendarEvent(SyncableMixin, Base):
    """A forward-looking, curator-selected moment in legislative life.

    Editorial layer, not an exhaustive feed: a curator picks the noteworthy
    moments per week (key Sesiones, Comisión hearings, interpelaciones,
    presidential mensajes, plazos). Distinct from :class:`LegislativeSession`
    (the exhaustive scraper-fed record of every meeting, not yet ingested —
    see ADR-0016) and from :class:`BillEvent` (the past-tense per-bill
    activity log). All writes flow through ``upsert_calendar_event`` in
    ``services/write.py`` — both the admin form and future agenda scrapers.

    ``source`` + ``external_ref`` form the dedup primitive for upstream
    scrapers: re-running a scrape with the same ``external_ref`` updates
    the row in place. Manual rows store ``external_ref=None`` (the unique
    index naturally tolerates multiple nulls in Postgres). See CONTEXT.md
    "Calendar event".
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "external_ref",
            name="uq_calendar_events_source_external_ref",
        ),
        Index("ix_calendar_events_starts_at", "starts_at"),
    )

    kind: Mapped[CalendarEventKind] = mapped_column(
        SqlEnum(
            CalendarEventKind,
            name="calendar_event_kind",
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    chamber_type: Mapped[ChamberType | None] = mapped_column(
        SqlEnum(
            ChamberType,
            name="calendar_event_chamber_type",
            native_enum=False,
            validate_strings=True,
        )
    )
    bill_id: Mapped[int | None] = mapped_column(
        ForeignKey("bills.id", ondelete="SET NULL")
    )
    legislator_id: Mapped[int | None] = mapped_column(
        ForeignKey("legislators.id", ondelete="SET NULL")
    )
    committee_id: Mapped[int | None] = mapped_column(
        ForeignKey("committees.id", ondelete="SET NULL")
    )
    source: Mapped[CalendarEventSource] = mapped_column(
        SqlEnum(
            CalendarEventSource,
            name="calendar_event_source",
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        default=CalendarEventSource.MANUAL,
    )
    external_ref: Mapped[str | None] = mapped_column(String(200))

    bill: Mapped["Bill | None"] = relationship()
    legislator: Mapped[Legislator | None] = relationship()
    committee: Mapped[Committee | None] = relationship()

    def __str__(self) -> str:
        return f"{self.kind.value}: {self.title} ({self.starts_at:%Y-%m-%d %H:%M})"

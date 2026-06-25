from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean
from sqlalchemy import Enum as SqlEnum
from sqlalchemy import ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SyncableMixin
from app.models.enums import Bloc, SignalType, VoteChoice, VotingResult, VotingType
from app.models.legislature import Chamber, LegislativeSession, Legislator
from app.models.proyecto import Bill, BillStage


class VotingSession(SyncableMixin, Base):
    __tablename__ = "voting_sessions"
    __table_args__ = (
        Index(
            "ix_voting_sessions_pending_bulletin",
            "bill_bulletin_number",
            postgresql_where="bill_id IS NULL AND bill_bulletin_number IS NOT NULL",
        ),
    )

    bcn_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    chamber_id: Mapped[int] = mapped_column(
        ForeignKey("chambers.id", ondelete="RESTRICT"), nullable=False
    )
    # Points at a single Sesión (one meeting), not a Legislatura. Nullable
    # because meeting-level ingestion is not yet wired up — votes link to the
    # parent Legislatura via Session→Legislature. See ADR-0016.
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("legislative_sessions.id", ondelete="SET NULL")
    )
    bill_id: Mapped[int | None] = mapped_column(
        ForeignKey("bills.id", ondelete="SET NULL")
    )
    bill_bulletin_number: Mapped[str | None] = mapped_column(String(50))
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
    dispensed_count: Mapped[int] = mapped_column(nullable=False, default=0)
    no_votes: Mapped[int] = mapped_column(nullable=False, default=0)
    paired_count: Mapped[int] = mapped_column(nullable=False, default=0)
    quorum_required: Mapped[int | None] = mapped_column()
    quorum_type: Mapped[str | None] = mapped_column(String(100))
    session_ref: Mapped[str | None] = mapped_column(String(100))
    stage_label: Mapped[str | None] = mapped_column(String(200))
    article_text: Mapped[str | None] = mapped_column(Text)
    constitutional_procedure_id: Mapped[int | None] = mapped_column()
    constitutional_procedure_label: Mapped[str | None] = mapped_column(String(100))
    regulatory_procedure_id: Mapped[int | None] = mapped_column()
    regulatory_procedure_label: Mapped[str | None] = mapped_column(String(100))

    chamber: Mapped[Chamber] = relationship()
    session: Mapped[LegislativeSession | None] = relationship(
        back_populates="voting_sessions"
    )
    bill: Mapped[Bill | None] = relationship(back_populates="voting_sessions")
    bill_stage: Mapped[BillStage | None] = relationship(
        back_populates="voting_sessions"
    )
    votes: Mapped[list["Vote"]] = relationship(back_populates="voting_session")
    signals: Mapped[list["VotingSessionSignal"]] = relationship(
        back_populates="voting_session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="VotingSessionSignal.severity.desc()",
    )

    def __str__(self) -> str:
        subject = self.subject if len(self.subject) <= 80 else f"{self.subject[:77]}..."
        return f"{self.voting_date.date().isoformat()} - {subject}"


class Vote(SyncableMixin, Base):
    """An individual legislator's vote within a :class:`VotingSession`.

    ``legislator_external_id`` is the per-stint chamber bridge from the
    upstream payload (``camara:{Id}`` for chamber votes, ``senado:{PARLID}``
    for senate votes) and is always populated — it remains on the row after
    resolution to support idempotent orphan reconciliation and traceability.

    When the resolver can match the bridge to a :class:`LegislatorTerm` whose
    date window covers ``voting_session.voting_date``, ``legislator_id`` is
    set to the canonical legislator. When no term matches (e.g. a brand-new
    legislator who has not been ingested yet), the row is saved orphaned —
    ``legislator_id IS NULL`` — and a reconciler fills it in once the term
    arrives. See ADR-0015.
    """

    __tablename__ = "votes"
    __table_args__ = (
        # One resolved vote per (session, legislator).
        Index(
            "uq_votes_session_legislator",
            "voting_session_id",
            "legislator_id",
            unique=True,
            postgresql_where="legislator_id IS NOT NULL",
        ),
        # One orphan vote per (session, bridge ID) while the legislator is
        # still unknown — prevents duplicate orphans across re-ingestion.
        Index(
            "uq_votes_session_external_orphan",
            "voting_session_id",
            "legislator_external_id",
            unique=True,
            postgresql_where="legislator_id IS NULL",
        ),
    )

    voting_session_id: Mapped[int] = mapped_column(
        ForeignKey("voting_sessions.id", ondelete="CASCADE"), nullable=False
    )
    legislator_id: Mapped[int | None] = mapped_column(
        ForeignKey("legislators.id", ondelete="CASCADE")
    )
    legislator_external_id: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )
    vote: Mapped[VoteChoice] = mapped_column(
        SqlEnum(
            VoteChoice, name="vote_choice", native_enum=False, validate_strings=True
        ),
        nullable=False,
    )

    voting_session: Mapped[VotingSession] = relationship(back_populates="votes")
    legislator: Mapped[Legislator | None] = relationship(back_populates="votes")

    def __str__(self) -> str:
        return f"{self.vote} - legislador {self.legislator_id}"


class VotingSessionSignal(SyncableMixin, Base):
    """A behavior-revealing signal fired on a single voting session.

    Precomputed by Celery after each vote ingestion. Multiple signals can fire
    on the same session (a session may be both a *quiebre de bloque* and a
    *votación dividida*); uniqueness is on (voting_session_id, signal_type).
    """

    __tablename__ = "voting_session_signals"
    __table_args__ = (
        UniqueConstraint(
            "voting_session_id",
            "signal_type",
            name="uq_voting_session_signals_session_type",
        ),
    )

    voting_session_id: Mapped[int] = mapped_column(
        ForeignKey("voting_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    signal_type: Mapped[SignalType] = mapped_column(
        SqlEnum(
            SignalType,
            name="signal_type",
            native_enum=False,
            validate_strings=True,
        ),
        nullable=False,
        index=True,
    )
    severity: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    voting_session: Mapped[VotingSession] = relationship(back_populates="signals")

    def __str__(self) -> str:
        return f"{self.signal_type.value} on session {self.voting_session_id}"


class VotingWindowAggregate(SyncableMixin, Base):
    """Rolling-window aggregates over voting sessions.

    Single row per ``window_days`` value, replaced wholesale by a daily Celery
    beat task. Read by ``GET /voting-sessions/aggregates`` to feed the
    permanent stats band on the /votaciones page.
    """

    __tablename__ = "voting_window_aggregates"
    __table_args__ = (
        UniqueConstraint("window_days", name="uq_voting_window_aggregates_window"),
    )

    window_days: Mapped[int] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    def __str__(self) -> str:
        return f"VotingWindowAggregate window_days={self.window_days}"


class LegislatorVotingStats(SyncableMixin, Base):
    """Precomputed per-legislator voting stats, refreshed out-of-band.

    Mixed windows by design (see ADR-0014): the base aggregates
    (``total_sessions`` … ``participation_rate``) are **career-wide** — matching
    the on-the-fly ``get_legislator_voting_summary`` — while the lean/discipline
    fields are scoped to the **current legislative period**.
    """

    __tablename__ = "legislator_voting_stats"

    legislator_id: Mapped[int] = mapped_column(
        ForeignKey("legislators.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    total_sessions: Mapped[int] = mapped_column(nullable=False, default=0)
    votes_for: Mapped[int] = mapped_column(nullable=False, default=0)
    votes_against: Mapped[int] = mapped_column(nullable=False, default=0)
    abstentions: Mapped[int] = mapped_column(nullable=False, default=0)
    no_votes: Mapped[int] = mapped_column(nullable=False, default=0)
    record_rate: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    participation_rate: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=0
    )

    # ── Inclinación de voto (current period) — see ADR-0014 ────────────────
    # The bloc whose modal vote the legislator matched most often across
    # contested, decisive sessions. ``lean_seats`` marks a lean strong enough to
    # seed an independent in the simulator. Null bloc = insufficient data or tie.
    inferred_bloc: Mapped[Bloc | None] = mapped_column(
        SqlEnum(
            Bloc,
            name="legislator_inferred_bloc",
            native_enum=False,
            validate_strings=True,
        )
    )
    lean_agreed: Mapped[int] = mapped_column(nullable=False, default=0)
    lean_contested: Mapped[int] = mapped_column(nullable=False, default=0)
    lean_seats: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Disciplina partidaria (current period, party members) ──────────────
    # ``discipline_rate`` is a percentage (0–100), mirroring record_rate.
    discipline_rate: Mapped[float | None] = mapped_column(Numeric(5, 2))
    discipline_with: Mapped[int] = mapped_column(nullable=False, default=0)
    discipline_decided: Mapped[int] = mapped_column(nullable=False, default=0)

    stats_updated_at: Mapped[datetime] = mapped_column(nullable=False)

    legislator: Mapped[Legislator] = relationship(back_populates="voting_stats")

    def __str__(self) -> str:
        return f"Stats legislador {self.legislator_id}"

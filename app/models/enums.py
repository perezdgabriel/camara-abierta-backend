from enum import Enum


class BillSummaryKind(str, Enum):
    """Layer of a bill's AI summary.

    ``PROPOSAL`` summarises the foundational mensaje/moción PDF; ``AMENDMENTS``
    summarises the comparados that accumulated during legislative tramitación.
    A third deterministic ``status_line`` layer is composed in the API and not
    stored. See ADR-0019.
    """

    PROPOSAL = "proposal"
    AMENDMENTS = "amendments"


class BillSummaryStatus(str, Enum):
    """Outcome of an attempt to generate a bill summary layer.

    Persisted on every attempt so callers can distinguish never-tried
    (row missing) from tried-and-skipped/failed. See ADR-0019.
    """

    SUCCESS = "success"
    SKIPPED = "skipped"
    FAILED = "failed"


class BillStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    WITHDRAWN = "withdrawn"
    UNCONSTITUTIONAL = "unconstitutional"
    ENACTED = "enacted"
    PUBLISHED = "published"


class BillOrigin(str, Enum):
    EXECUTIVE = "executive"
    DEPUTIES = "deputies"


class BillType(str, Enum):
    PROJECT = "project"


class StageType(str, Enum):
    FIRST_CONSTITUTIONAL_TRAMITE = "first_constitutional_tramite"
    SECOND_CONSTITUTIONAL_TRAMITE = "second_constitutional_tramite"
    THIRD_CONSTITUTIONAL_TRAMITE = "third_constitutional_tramite"
    MIXED_COMMISSION = "mixed_commission"
    CONSTITUTIONAL_TRIBUNAL = "constitutional_tribunal"
    PROMULGATION = "promulgation"
    PUBLICATION = "publication"
    OTHER = "other"


class UrgencyType(str, Enum):
    SIMPLE = "simple"
    SUM = "sum"
    IMMEDIATE = "immediate"


class VotingType(str, Enum):
    GENERAL = "general"
    PARTICULAR = "particular"
    SINGLE = "single"
    OTHER = "other"


class VoteChoice(str, Enum):
    FOR = "for"
    AGAINST = "against"
    ABSTAIN = "abstain"
    PAIRED = "paired"
    DISPENSED = "dispensed"
    NO_VOTE = "no_vote"


class VotingResult(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    TIE = "tie"


class ChamberType(str, Enum):
    DEPUTIES = "deputies"
    SENATE = "senate"


class CommitteeType(str, Enum):
    PERMANENT = "permanent"
    SPECIAL = "special"
    INVESTIGATIVE = "investigative"
    MIXED = "mixed"


class LegislatureKind(str, Enum):
    """The kind of an annual Legislatura.

    Post-2005 reform every Legislatura is ``ORDINARIA`` and runs continuously
    Mar 11 → Mar 10 next year; ``EXTRAORDINARIA`` is retained for historical
    fidelity (pre-2005 records). See CONTEXT.md "Legislatura".
    """

    ORDINARIA = "ordinaria"
    EXTRAORDINARIA = "extraordinaria"


class SessionKind(str, Enum):
    """The kind of a single Sesión Legislativa (one meeting).

    Distinct from :class:`LegislatureKind` — ``ESPECIAL`` is a session convened
    outside regular hours (e.g. minister interpellations or urgent topics), not
    an "extraordinary" annual cycle. See CONTEXT.md "Sesión Legislativa".
    """

    ORDINARIA = "ordinaria"
    ESPECIAL = "especial"


class Bloc(str, Enum):
    """Structural political alignment of a party (or independent legislator).

    Editorial, not upstream-sourced: there is no congress API for this. Modeled
    temporally via ``BlocAffiliation`` (party-scoped) and ``Legislator.default_bloc``
    (independent/override). See CONTEXT.md "Bloque" and ADR-0014.
    """

    OFICIALISMO = "oficialismo"
    OPOSICION = "oposicion"


class CalendarEventKind(str, Enum):
    """The kind of a curator-selected calendar event.

    Discriminator for editorial calendar entries — drives UI rendering and
    gives future agenda scrapers a structural slot. ``OTRO`` is the escape
    valve for moments that don't fit the named kinds. See CONTEXT.md
    "Calendar event".

    ``SESION`` and ``VOTACION`` are deliberately distinct: a Sesión is a
    chamber/comisión meeting block (the container), a Votación is a
    discrete announced vote (one item, possibly nested in a Sesión but
    often surfaced independently in client agendas — the dashboard
    widget is named *Próximas votaciones* precisely on this split).
    """

    SESION = "sesion"
    VOTACION = "votacion"
    COMISION = "comision"
    INTERPELACION = "interpelacion"
    MENSAJE = "mensaje"
    PLAZO = "plazo"
    ACUSACION_CONSTITUCIONAL = "acusacion_constitucional"
    INFORME_CEI = "informe_cei"
    OTRO = "otro"


class CalendarEventSource(str, Enum):
    """Where a calendar event row originated.

    ``MANUAL`` is the admin-panel entry path. ``TABLA_SEMANAL`` is the
    Cámara de Diputados weekly agenda PDF parsed by ``app/ingestors/parsers/
    tabla_semanal.py``. New values land as upstream agenda scrapers ship;
    each writes its own ``external_ref`` for dedup. See CONTEXT.md
    "Calendar event".
    """

    MANUAL = "manual"
    TABLA_SEMANAL = "tabla_semanal"


class SignalType(str, Enum):
    """Behavior-revealing signal flagged on a voting session.

    See CONTEXT.md in the web repo for the editorial meaning. Definitions:
    - QUIEBRE_BLOQUE: a party's cohesion dropped below threshold in this session
    - DIVERGENCIA_CAMARAS: same bill voted differently in Cámara vs Senado
    - VOTACION_DIVIDIDA: narrow margin with high participation
    - BAJO_REGISTRO: unusually high share of legislators left no recorded vote
    """

    QUIEBRE_BLOQUE = "quiebre_bloque"
    DIVERGENCIA_CAMARAS = "divergencia_camaras"
    VOTACION_DIVIDIDA = "votacion_dividida"
    BAJO_REGISTRO = "bajo_registro"

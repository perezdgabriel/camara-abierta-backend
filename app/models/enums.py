from enum import Enum


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
    ABSENT = "absent"


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


class Bloc(str, Enum):
    """Structural political alignment of a party (or independent legislator).

    Editorial, not upstream-sourced: there is no congress API for this. Modeled
    temporally via ``BlocAffiliation`` (party-scoped) and ``Legislator.default_bloc``
    (independent/override). See CONTEXT.md "Bloque" and ADR-0006.
    """

    OFICIALISMO = "oficialismo"
    OPOSICION = "oposicion"


class SignalType(str, Enum):
    """Behavior-revealing signal flagged on a voting session.

    See CONTEXT.md in the web repo for the editorial meaning. Definitions:
    - QUIEBRE_BLOQUE: a party's cohesion dropped below threshold in this session
    - DIVERGENCIA_CAMARAS: same bill voted differently in Cámara vs Senado
    - VOTACION_DIVIDIDA: narrow margin with high participation
    - ALTO_AUSENTISMO: absence rate unusually high vs chamber baseline
    """

    QUIEBRE_BLOQUE = "quiebre_bloque"
    DIVERGENCIA_CAMARAS = "divergencia_camaras"
    VOTACION_DIVIDIDA = "votacion_dividida"
    ALTO_AUSENTISMO = "alto_ausentismo"

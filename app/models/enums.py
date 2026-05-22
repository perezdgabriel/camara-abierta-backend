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

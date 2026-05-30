from datetime import date as date_, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import SyncableMixin


class OfficialGazetteNorm(SyncableMixin, Base):
    __tablename__ = "official_gazette_norms"

    date: Mapped[date_] = mapped_column(Date, nullable=False)
    edition: Mapped[str | None] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(Text)
    ministry: Mapped[str | None] = mapped_column(Text)
    organ: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    cve: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    titulo_amigable: Mapped[str | None] = mapped_column(Text)
    resumen_ejecutivo: Mapped[str | None] = mapped_column(Text)
    puntos_clave: Mapped[list[str] | None] = mapped_column(JSONB)
    beneficiarios: Mapped[str | None] = mapped_column(Text)
    categoria_ia: Mapped[str | None] = mapped_column(Text)
    importancia_ciudadana: Mapped[int | None] = mapped_column(BigInteger)


class Regulation(SyncableMixin, Base):
    __tablename__ = "regulations"
    __table_args__ = (
        UniqueConstraint(
            "numero",
            "anio",
            "ministerio",
            "categoria",
            name="uq_regulations_natural_key",
        ),
    )

    numero: Mapped[str] = mapped_column(Text, nullable=False)
    anio: Mapped[str] = mapped_column(Text, nullable=False)
    ministerio: Mapped[str] = mapped_column(Text, nullable=False)
    subsecretaria: Mapped[str | None] = mapped_column(Text)
    materia: Mapped[str | None] = mapped_column(Text)
    fecha_ingreso: Mapped[date_ | None] = mapped_column(Date)
    estado: Mapped[str | None] = mapped_column(Text)
    categoria: Mapped[str] = mapped_column(Text, nullable=False)
    reingresado: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    content_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )

    etapas: Mapped[list["RegulationStage"]] = relationship(
        back_populates="reglamento",
        order_by="RegulationStage.etapa",
    )


class RegulationStage(Base):
    __tablename__ = "regulation_stages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reglamento_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("regulations.id", ondelete="CASCADE"),
        nullable=False,
    )
    etapa: Mapped[str | None] = mapped_column(Text)
    fecha: Mapped[date_ | None] = mapped_column(Date)
    accion: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str | None] = mapped_column(Text)
    observaciones: Mapped[str | None] = mapped_column(Text)
    documento: Mapped[str | None] = mapped_column(Text)
    documento_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime)

    reglamento: Mapped[Regulation] = relationship(back_populates="etapas")

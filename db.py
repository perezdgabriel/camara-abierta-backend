from sqlalchemy import create_engine, Column, BigInteger, Boolean, Date, Text, Integer, ForeignKey, DateTime
from sqlalchemy.orm import sessionmaker, DeclarativeBase, relationship
from sqlalchemy.dialects.postgresql import JSONB

from config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


class NormaGeneralDB(Base):
    __tablename__ = "normas_generales"

    id = Column(BigInteger, primary_key=True)
    date = Column(Date, nullable=False)
    edition = Column(Text)
    branch = Column(Text)
    ministry = Column(Text)
    organ = Column(Text)
    title = Column(Text, nullable=False)
    pdf_url = Column(Text)
    cve = Column(Text, unique=True, nullable=False)
    explanation = Column(Text, nullable=False)
    titulo_amigable = Column(Text)
    resumen_ejecutivo = Column(Text)
    puntos_clave = Column(JSONB)
    beneficiarios = Column(Text)
    categoria_ia = Column(Text)
    importancia_ciudadana = Column(Integer)
    created_at = Column(Text)


class ReglamentoDB(Base):
    __tablename__ = "reglamentos"

    id = Column(BigInteger, primary_key=True)
    numero = Column(Text, nullable=False)
    anio = Column(Text, nullable=False)
    ministerio = Column(Text, nullable=False)
    subsecretaria = Column(Text)
    materia = Column(Text)
    fecha_ingreso = Column(Date)
    estado = Column(Text)
    categoria = Column(Text, nullable=False)
    reingresado = Column(Boolean, nullable=False, server_default="false")
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    etapas = relationship("ReglamentoEtapaDB", back_populates="reglamento", order_by="ReglamentoEtapaDB.etapa")


class ReglamentoEtapaDB(Base):
    __tablename__ = "reglamentos_etapas"

    id = Column(BigInteger, primary_key=True)
    reglamento_id = Column(BigInteger, ForeignKey("reglamentos.id", ondelete="CASCADE"), nullable=False)
    etapa = Column(Text)
    fecha = Column(Date)
    accion = Column(Text)
    sector = Column(Text)
    observaciones = Column(Text)
    documento = Column(Text)
    documento_url = Column(Text)
    created_at = Column(DateTime)

    reglamento = relationship("ReglamentoDB", back_populates="etapas")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

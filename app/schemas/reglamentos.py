from datetime import date, datetime

from pydantic import BaseModel
from pydantic import Field

from app.schemas.common import CountResponse, ORMModel


class Etapa(ORMModel):
    id: int
    etapa: str | None = None
    fecha: date | None = None
    accion: str | None = None
    sector: str | None = None
    observaciones: str | None = None
    documento: str | None = None
    documento_url: str | None = None
    gobierno_actual: bool = False


class Reglamento(ORMModel):
    id: int
    numero: str
    anio: str
    ministerio: str
    subsecretaria: str | None = None
    materia: str | None = None
    fecha_ingreso: date | None = None
    estado: str | None = None
    categoria: str
    reingresado: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ReglamentoDetail(Reglamento):
    etapas: list[Etapa] = Field(default_factory=list)


class ReglamentosResponse(CountResponse[Reglamento]):
    data: list[Reglamento] = Field(default_factory=list)


class ReglamentoStats(ORMModel):
    ministerio: str
    count: int


class ReglamentoTimeline(BaseModel):
    reglamento_id: int
    numero: str
    anio: str
    ministerio: str
    materia: str | None = None
    categoria: str
    estado: str | None = None
    ultima_etapa_fecha: date | None = None
    ultima_etapa_accion: str | None = None
    total_etapas: int

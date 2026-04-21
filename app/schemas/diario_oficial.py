from datetime import date, datetime

from pydantic import Field

from app.schemas.common import CountResponse, ORMModel


class Norma(ORMModel):
    id: int
    date: date
    edition: str | None = None
    branch: str | None = None
    ministry: str | None = None
    organ: str | None = None
    title: str
    pdf_url: str | None = None
    cve: str
    explanation: str
    titulo_amigable: str | None = None
    resumen_ejecutivo: str | None = None
    puntos_clave: list[str] | None = None
    beneficiarios: str | None = None
    categoria_ia: str | None = None
    importancia_ciudadana: int | None = None
    created_at: datetime | None = None


class NormasResponse(CountResponse[Norma]):
    data: list[Norma] = Field(default_factory=list)

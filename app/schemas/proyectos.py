from datetime import date

from pydantic import Field

from app.schemas.common import CountResponse, ORMModel


class ProyectoResumen(ORMModel):
    id: int
    bulletin_number: str
    title: str
    status: str
    entry_date: date


class ProyectosResponse(CountResponse[ProyectoResumen]):
    data: list[ProyectoResumen] = Field(default_factory=list)

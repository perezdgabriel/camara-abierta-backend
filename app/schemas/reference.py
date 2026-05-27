from app.schemas.common import ORMModel


class RegionBrief(ORMModel):
    id: int
    number: int
    name: str

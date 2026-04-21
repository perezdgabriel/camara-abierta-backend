from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

SchemaT = TypeVar("SchemaT")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CountResponse(BaseModel, Generic[SchemaT]):
    count: int
    data: list[SchemaT] = Field(default_factory=list)


class SyncMeta(BaseModel):
    current_version: int = 0
    has_more: bool = False
    count: int = 0


class DeltaSyncResponse(BaseModel, Generic[SchemaT]):
    items: list[SchemaT] = Field(default_factory=list)
    deleted_ids: list[int] = Field(default_factory=list)
    meta: SyncMeta = Field(default_factory=SyncMeta)

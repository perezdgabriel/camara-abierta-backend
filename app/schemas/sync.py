from datetime import datetime

from pydantic import Field

from app.schemas.common import DeltaSyncResponse, ORMModel, SyncMeta


class ClientSyncStateSchema(ORMModel):
    id: int
    device_id: str
    entity_type: str
    last_sync_version: int
    last_sync_at: datetime


class ClientSyncDeltaResponse(DeltaSyncResponse[ClientSyncStateSchema]):
    items: list[ClientSyncStateSchema] = Field(default_factory=list)
    meta: SyncMeta = Field(default_factory=SyncMeta)

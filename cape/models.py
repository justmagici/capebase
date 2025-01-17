from dataclasses import dataclass
from datetime import datetime
from typing import Generic, Literal

from sqlmodel import SQLModel

from cape.types import ModelType

TableEvent = Literal["INSERT", "UPDATE", "DELETE", "*"]


@dataclass(frozen=True)
class NotificationKey:
    table_name: str
    event_type: TableEvent


@dataclass(frozen=True)
class NotificationLog:
    key: NotificationKey
    # TODO: Serialized the instance for immutability
    instance: SQLModel
    timestamp: datetime

    def __str__(self):
        return f"{self.key.event_type} on {self.key.table_name} with row_id {self.instance.id} at {self.timestamp}"


@dataclass(frozen=True)
class ModelChange(Generic[ModelType]):
    table: str
    event: TableEvent
    payload: ModelType
    timestamp: datetime

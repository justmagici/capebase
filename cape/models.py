from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlmodel import SQLModel

TableEvent = Literal["INSERT", "UPDATE", "DELETE"]

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
    
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, Literal, Dict, Any, Union, Callable, TypeVar, Optional, Annotated, Awaitable

from sqlmodel import SQLModel

from cape.types import ModelType

TableEvent = Literal["INSERT", "UPDATE", "DELETE", "*"]

UserT = TypeVar("UserT")

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
    table: Union[str, Callable[..., str]]
    event: TableEvent
    payload: ModelType
    timestamp: datetime

    def to_json(self) -> Dict[str, Any]:
        return {
            "table": self.table,
            "event": self.event,
            "payload": self.payload.model_dump(),
            "timestamp": self.timestamp.isoformat(),
        }

@dataclass
class AuthContext(Generic[UserT]):
    subject: Optional[str] = None
    role: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AuthField:
    source: Literal["subject", "role", "context"]
    key: Optional[str] = None
    required: bool = True

    def get_value_from_context(self, auth_context: AuthContext) -> Any:
        if self.source == "subject":
            return auth_context.subject
        elif self.source == "role":
            return auth_context.role
        elif self.source == "context" and self.key:
            return auth_context.context[self.key]
        return None

FromSubject = Annotated[str, AuthField(source="subject")]
FromRole = Annotated[str, AuthField(source="role")]

def from_context(key: str) -> Annotated[Any, AuthField]:
    return Annotated[str, AuthField(source="context", key=key)]

AuthContextProvider = Callable[..., Awaitable[AuthContext]]
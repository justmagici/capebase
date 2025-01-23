import asyncio
import logging
from asyncio import Queue
from dataclasses import dataclass, field
from typing import (
    AsyncIterator,
    Callable,
    Coroutine,
    Dict,
    Generic,
    Type,
    Union,
    overload,
)
from weakref import WeakSet

from sqlmodel import SQLModel

from cape.models import ModelChange, NotificationLog
from cape.types import ModelType
 
logger = logging.getLogger(__name__)

F = Callable[[NotificationLog], None]


# API Generator will just setup a subscriber to notifcation engine for each model
# and then we can just use the notification engine to send notifications to the API


# In-memory broadcast channel that can be used to send notification to multiple listeners
@dataclass
class BroadcastChannel(Generic[ModelType]):
    _listeners: WeakSet[
        Callable[
            [ModelChange[ModelType]], Coroutine[ModelChange[ModelType], None, None]
        ]
    ] = field(default_factory=WeakSet)
    maxsize: int = field(default=10)

    async def subscribe(self) -> AsyncIterator[ModelChange[ModelType]]:
        queue: Queue[ModelChange[ModelType]] = Queue(maxsize=self.maxsize)

        async def listener(change: ModelChange[ModelType]) -> None:
            try:
                await queue.put(change)
            except asyncio.QueueFull:
                logger.error("Broadcast channel queue is full")
                # TOOD: Consider implementing backpressure here

        self._listeners.add(listener)
        try:
            while True:
                yield await queue.get()
        except asyncio.CancelledError:
            logger.info("Broadcast channel listener cancelled")
            self._listeners.remove(listener)
        finally:
            self._listeners.discard(listener)

    async def publish(self, change: ModelChange[ModelType]):
        tasks = [asyncio.create_task(listener(change)) for listener in self._listeners]
        await asyncio.gather(*tasks, return_exceptions=True)


@dataclass
class NotificationEngine:
    _channels: Dict[Union[str, Callable[..., str]], BroadcastChannel[SQLModel]] = field(default_factory=dict)

    @overload
    def get_channel(
        self, model_type: Type[SQLModel]
    ) -> BroadcastChannel[SQLModel]: ...
    

    @overload
    def get_channel(self, model_type: str) -> BroadcastChannel[SQLModel]: ...

    def get_channel(
        self, model_type: Union[Type[SQLModel], SQLModel, str]
    ) -> BroadcastChannel[SQLModel]:
        """Get or create a broadcast channel for a model type"""
        if isinstance(model_type, type) and issubclass(model_type, SQLModel):
            key = model_type.__tablename__
        elif isinstance(model_type, SQLModel):
            key = model_type.__tablename__
        elif isinstance(model_type, str):
            key = model_type
        else:
            raise ValueError(f"Invalid model type: {model_type}")

        assert isinstance(key, str)
        if key not in self._channels:
            self._channels[key] = BroadcastChannel()
        return self._channels[key]

    async def notify(self, change: ModelChange[SQLModel]):
        """Notify all subscribers of a model change"""
        if change.table in self._channels:
            await self._channels[change.table].publish(change)

        

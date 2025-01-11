import asyncio
import logging
from asyncio import Queue
from collections import defaultdict
from typing import Callable, Optional, Union

from pyrestore.models import NotificationKey, NotificationLog

logger = logging.getLogger(__name__)

F = Callable[[NotificationLog], None]


class NotificationEngine:
    queue: Queue[NotificationLog]
    subscribers: dict[NotificationKey, list[F]]
    polling_task: asyncio.Task | None = None

    def __init__(self, queue: Queue[NotificationLog] | None = None):
        self.subscribers = defaultdict(list)
        self.queue = queue or Queue()

    def subscribe(
        self, key: NotificationKey, callback: Optional[F] = None
    ) -> Union[F, Callable[[F], F]]:
        """Subscribe a callback function to notifications matching the given key.

        Args:
            key: NotificationKey to subscribe to, containing table name and event type
            callback: Optional callback function to execute when notification received.
                     If None, returns a decorator.

        Returns:
            Either the callback function directly, or a decorator that will register
            the decorated function as the callback.
        """

        def wrapper(
            callback: Callable[[NotificationLog], None],
        ) -> Callable[[NotificationLog], None]:
            """Wrapper function to use as a decorator.

            Args:
                callback: The function to register as the notification callback

            Returns:
                The original callback function after registering it
            """
            self.subscribers[key].append(callback)
            return callback

        if callback is None:
            return wrapper
        else:
            self.subscribers[key].append(callback)
            return callback

    def unsubscribe(self, key: NotificationKey, callback: F):
        "Unsubscribe from notifications for a specific key"
        if key in self.subscribers:
            self.subscribers[key].remove(callback)
            if not self.subscribers[key]:
                del self.subscribers[key]

    async def notify(self, notification: NotificationLog):
        await self.queue.put(notification)

    async def _poll(self):
        """Single polling task for all notifications"""
        try:
            while True:
                try:
                    notification = await self.queue.get()
                    callbacks = self.subscribers.get(notification.key, [])
                    for callback in callbacks:
                        try:
                            await self._execute_callback(callback, notification)
                        except Exception as e:
                            logger.error(
                                f"Error in executing callback for key {notification.key}: {e}"
                            )

                    self.queue.task_done()
                except asyncio.CancelledError:
                    logger.info("Notification engine poll cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error polling queue: {e}")
                    await asyncio.sleep(1)  # Prevent tight loop on error
        except Exception:
            logging.error("Fatal error in notification engine poll", exc_info=True)

    async def _execute_callback(self, callback: F, notification: NotificationLog):
        """Execute a callback, handling both async and sync callbacks"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(notification)
            else:
                callback(notification)
        except Exception as e:
            logger.error(f"Error in executing callback for key {e}")

    async def start(self):
        """Start the notification engine polling task"""
        if not self.polling_task:
            self.polling_task = asyncio.create_task(self._poll())

    async def stop(self):
        """Stop the notification engine polling task"""
        if self.polling_task:
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
            self.polling_task = None
            logger.info("Notification engine stopped")

        await self._cleanup()

    async def _cleanup(self):
        """Clean up the queue when the engine is stopped"""
        while not self.queue.empty():
            await self.queue.get()
            self.queue.task_done()

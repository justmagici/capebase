import asyncio
from dataclasses import dataclass
from datetime import datetime

import pytest

from pyrestore.core.notification import NotificationEngine
from pyrestore.models import NotificationKey, NotificationLog


@dataclass
class DataModel:
    """Test model for notifications"""

    id: int
    name: str


@pytest.fixture
def engine():
    """Create a fresh notification engine for each test"""
    return NotificationEngine()


@pytest.fixture
def notification_key():
    """Create a test notification key"""
    return NotificationKey("test_model", "test_action")


@pytest.fixture
def test_notification(notification_key):
    """Create a test notification"""
    return NotificationLog(
        key=notification_key,
        instance=DataModel(id=1, name="test"),
        timestamp=datetime.now(),
    )


@pytest.mark.asyncio
async def test_subscribe(engine, notification_key):
    """Test that subscribers can be added correctly"""

    async def async_callback(notification):
        pass

    def sync_callback(notification):
        pass

    # Add subscribers
    engine.subscribe(notification_key, async_callback)
    engine.subscribe(notification_key, sync_callback)

    # Verify subscribers were added
    assert async_callback in engine.subscribers[notification_key]
    assert sync_callback in engine.subscribers[notification_key]
    assert len(engine.subscribers[notification_key]) == 2


@pytest.mark.asyncio
async def test_unsubscribe(engine, notification_key):
    """Test that subscribers can be removed correctly for both sync and async callbacks"""

    async def async_callback(notification):
        pass

    def sync_callback(notification):
        pass

    # Add and then remove async subscriber
    engine.subscribe(notification_key, async_callback)
    engine.unsubscribe(notification_key, async_callback)
    assert notification_key not in engine.subscribers

    # Add and then remove sync subscriber
    engine.subscribe(notification_key, sync_callback)
    engine.unsubscribe(notification_key, sync_callback)
    assert notification_key not in engine.subscribers

    # Add both types and remove one
    engine.subscribe(notification_key, async_callback)
    engine.subscribe(notification_key, sync_callback)
    engine.unsubscribe(notification_key, async_callback)
    assert notification_key in engine.subscribers
    assert len(engine.subscribers[notification_key]) == 1
    assert sync_callback in engine.subscribers[notification_key]


@pytest.mark.asyncio
async def test_notify_enqueues(engine, test_notification):
    """Test that notifications are properly enqueued"""

    async def callback(notification):
        pass

    engine.subscribe(test_notification.key, callback)

    # Send notification
    await engine.notify(test_notification)

    # Verify notification was enqueued
    assert engine.queue.qsize() == 1
    queued = await engine.queue.get()
    assert queued == test_notification


@pytest.mark.asyncio
async def test_polling_and_callbacks(engine, test_notification):
    """Test that polling works and callbacks are executed"""
    received_notifications = []

    async def test_callback(notification):
        received_notifications.append(notification)

    # Setup and start engine
    engine.subscribe(test_notification.key, test_callback)
    await engine.start()

    # Send notification
    await engine.notify(test_notification)

    # Wait for processing
    await asyncio.sleep(0.1)

    # Verify callback was executed
    assert len(received_notifications) == 1
    assert received_notifications[0] == test_notification

    await engine.stop()


@pytest.mark.asyncio
async def test_engine_stop(engine, test_notification):
    """Test that engine can be properly stopped"""
    await engine.start()
    assert engine.polling_task is not None
    assert not engine.polling_task.done()

    await engine.stop()
    assert engine.polling_task is None


@pytest.mark.asyncio
async def test_multiple_subscribers(engine, test_notification):
    """Test handling multiple subscribers for the same notification"""
    received_1 = []
    received_2 = []

    async def callback_1(notification):
        received_1.append(notification)

    async def callback_2(notification):
        received_2.append(notification)

    engine.subscribe(test_notification.key, callback_1)
    engine.subscribe(test_notification.key, callback_2)

    await engine.start()
    await engine.notify(test_notification)
    await asyncio.sleep(0.1)

    assert len(received_1) == 1
    assert len(received_2) == 1
    assert received_1[0] == received_2[0] == test_notification

    await engine.stop()


@pytest.mark.asyncio
async def test_error_handling_in_callback(engine, test_notification):
    """Test that errors in callbacks are properly handled"""

    async def failing_callback(notification):
        raise ValueError("Test error")

    async def working_callback(notification):
        return True

    engine.subscribe(test_notification.key, failing_callback)
    engine.subscribe(test_notification.key, working_callback)

    await engine.start()
    await engine.notify(test_notification)
    await asyncio.sleep(0.1)  # Allow processing

    # Engine should continue running despite error
    assert not engine.polling_task.done()

    await engine.stop()


@pytest.mark.asyncio
async def test_sync_and_async_callbacks(engine, test_notification):
    """Test that both sync and async callbacks are supported"""
    async_received = []
    sync_received = []

    async def async_callback(notification):
        async_received.append(notification)

    def sync_callback(notification):
        sync_received.append(notification)

    engine.subscribe(test_notification.key, async_callback)
    engine.subscribe(test_notification.key, sync_callback)

    await engine.start()
    await engine.notify(test_notification)
    await asyncio.sleep(0.1)

    assert len(async_received) == 1
    assert len(sync_received) == 1

    await engine.stop()


@pytest.mark.asyncio
async def test_queue_cleanup_on_stop(engine, test_notification):
    """Test that queue is properly cleaned up when engine is stopped"""

    async def callback(notification):
        pass

    engine.subscribe(test_notification.key, callback)
    await engine.start()

    # Add multiple notifications
    for _ in range(5):
        await engine.notify(test_notification)

    await engine.stop()
    assert engine.queue.empty()

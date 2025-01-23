import asyncio
import logging
from datetime import datetime

import pytest
from sqlmodel import Field, SQLModel

from cape.notification import NotificationEngine
from cape.models import ModelChange

logger = logging.getLogger(__name__)


# Test models
class TestUser(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str


class TestPost(SQLModel, table=True):
    id: int = Field(primary_key=True)
    title: str


@pytest.fixture
def notification_engine():
    return NotificationEngine()


@pytest.fixture
def current_time():
    return datetime.now()


@pytest.mark.asyncio
class TestNotificationEngine:
    async def test_get_channel_with_model_class(self, notification_engine):
        """Test getting channel using model class"""
        channel = notification_engine.get_channel(TestUser)
        assert channel is not None
        # Verify same channel is returned for same model
        channel2 = notification_engine.get_channel(TestUser)
        assert channel is channel2

    async def test_get_channel_with_model_instance(self, notification_engine):
        """Test getting channel using model instance"""
        user = TestUser(id=1, name="test")
        channel = notification_engine.get_channel(user)
        assert channel is not None
        # Verify channel is same as one gotten by class
        class_channel = notification_engine.get_channel(TestUser)
        assert channel is class_channel

    async def test_get_channel_with_string(self, notification_engine):
        """Test getting channel using string table name"""
        channel = notification_engine.get_channel("testuser")
        assert channel is not None
        # Verify channel is same as one gotten by class
        class_channel = notification_engine.get_channel(TestUser)
        assert channel is class_channel

    async def test_invalid_model_type(self, notification_engine):
        """Test error handling for invalid model type"""
        with pytest.raises(ValueError, match="Invalid model type:.*"):
            notification_engine.get_channel(123)  # type: ignore

    async def test_notification_delivery(self, notification_engine, current_time):
        """Test that notifications are delivered to correct channel"""
        user_channel = notification_engine.get_channel(TestUser)
        post_channel = notification_engine.get_channel(TestPost)

        user_changes = []
        post_changes = []

        async def collect_user_changes():
            try:
                async for change in user_channel.subscribe():
                    user_changes.append(change)
                    if len(user_changes) == 1:
                        break
            except asyncio.CancelledError:
                logger.debug("User change collector cancelled")
                raise

        async def collect_post_changes():
            try:
                async for change in post_channel.subscribe():
                    post_changes.append(change)
                    if len(post_changes) == 1:
                        break
            except asyncio.CancelledError:
                logger.debug("Post change collector cancelled")
                raise

        async def send_notifications():
            # Send notifications
            user = TestUser(id=1, name="test")
            user_change = ModelChange[TestUser](
                table="testuser", event="INSERT", payload=user, timestamp=current_time
            )
            await notification_engine.notify(user_change)

            post = TestPost(id=1, title="test")
            post_change = ModelChange[TestPost](
                table="testpost", event="INSERT", payload=post, timestamp=current_time
            )
            await notification_engine.notify(post_change)

        # Start all tasks
        user_task = asyncio.create_task(collect_user_changes())
        post_task = asyncio.create_task(collect_post_changes())
        notify_task = asyncio.create_task(send_notifications())

        await asyncio.gather(user_task, post_task, notify_task)

        assert len(user_changes) == 1
        assert len(post_changes) == 1
        assert user_changes[0].table == "testuser"
        assert user_changes[0].payload.name == "test"
        assert post_changes[0].table == "testpost"
        assert post_changes[0].payload.title == "test"

    async def test_multiple_subscribers(self, notification_engine, current_time):
        """Test that multiple subscribers receive the same notification"""
        channel = notification_engine.get_channel(TestUser)
        changes1 = []
        changes2 = []

        async def collect_changes1():
            try:
                async for change in channel.subscribe():
                    changes1.append(change)
                    if len(changes1) == 1:
                        break
            except asyncio.CancelledError:
                logger.debug("Changes1 collector cancelled")
                raise

        async def collect_changes2():
            try:
                async for change in channel.subscribe():
                    changes2.append(change)
                    if len(changes2) == 1:
                        break
            except asyncio.CancelledError:
                logger.debug("Changes2 collector cancelled")
                raise

        async def send_notification():
            # Send notification
            user = TestUser(id=1, name="test")
            change = ModelChange[TestUser](
                table="testuser", event="INSERT", payload=user, timestamp=current_time
            )
            await notification_engine.notify(change)

        # Start collectors and notification task
        task1 = asyncio.create_task(collect_changes1())
        task2 = asyncio.create_task(collect_changes2())
        notify_task = asyncio.create_task(send_notification())

        # Wait for all tasks
        await asyncio.gather(task1, task2, notify_task)

        assert len(changes1) == 1
        assert len(changes2) == 1
        assert changes1[0].payload.id == changes2[0].payload.id
        assert changes1[0].payload.name == changes2[0].payload.name

    async def test_different_event_types(self, notification_engine, current_time):
        """Test handling of different event types"""
        channel = notification_engine.get_channel(TestUser)
        changes = []

        async def collect_changes():
            try:
                async for change in channel.subscribe():
                    changes.append(change)
                    if len(changes) == 3:  # Collect all three events
                        break
            except asyncio.CancelledError:
                logger.debug("Changes collector cancelled")
                raise

        async def send_notifications():
            # Test INSERT
            user = TestUser(id=1, name="test")
            insert_change = ModelChange[TestUser](
                table="testuser", event="INSERT", payload=user, timestamp=current_time
            )
            await notification_engine.notify(insert_change)

            # Test UPDATE
            user.name = "updated"
            update_change = ModelChange[TestUser](
                table="testuser", event="UPDATE", payload=user, timestamp=current_time
            )
            await notification_engine.notify(update_change)

            # Test DELETE
            delete_change = ModelChange[TestUser](
                table="testuser", event="DELETE", payload=user, timestamp=current_time
            )
            await notification_engine.notify(delete_change)

        # Start collector and notification tasks
        collector_task = asyncio.create_task(collect_changes())
        notify_task = asyncio.create_task(send_notifications())

        # Wait for tasks
        await asyncio.gather(collector_task, notify_task)

        assert len(changes) == 3
        assert changes[0].event == "INSERT"
        assert changes[1].event == "UPDATE"
        assert changes[2].event == "DELETE"

    async def test_channel_cleanup(self, notification_engine):
        """Test that subscribers are properly cleaned up"""
        channel = notification_engine.get_channel(TestUser)

        # Create and exit a subscription context
        async with asyncio.timeout(0.1):  # 0.1 second timeout
            async for _ in channel.subscribe():
                pass

        # Verify subscriber was removed
        assert len(channel._listeners) == 0

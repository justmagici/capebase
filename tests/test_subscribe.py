
from fastapi import FastAPI
from unittest.mock import Mock

import pytest
import pytest_asyncio
from sqlmodel import SQLModel
from datetime import datetime

from cape.models import ModelChange
from cape.main import Cape, AuthContext


@pytest.fixture
def app():
    return FastAPI()

@pytest_asyncio.fixture
async def cape(app):
    cape = Cape(app=app, db_path="sqlite+aiosqlite:///:memory:", auth_provider=lambda: AuthContext())

    async with cape.app.router.lifespan_context(app):
        yield cape

    async with cape.db_session.connect() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


def test_subscribe_decorator(cape: Cape):    
    # Create a test model
    class TestModel(SQLModel):
        pass
    
    # Test subscription registration
    @cape.subscribe(TestModel)
    def test_handler(change: ModelChange):
        pass
    
    # Verify the subscription was stored correctly
    assert len(cape._pending_subscriptions) == 1
    model, subscriptions = cape._pending_subscriptions[0]
    
    # Check the model type is correct
    assert model == TestModel
    # Check the handler was stored
    assert len(subscriptions) == 1
    assert test_handler in subscriptions


def test_multiple_subscriptions_same_model(cape: Cape):
    class TestModel(SQLModel):
        pass
    
    # Register multiple handlers for the same model
    @cape.subscribe(TestModel)
    def handler1(change: ModelChange):
        pass
    
    @cape.subscribe(TestModel)
    def handler2(change: ModelChange):
        pass
    
    assert len(cape._pending_subscriptions) == 2
    
    # Verify both handlers were registered
    handlers = [subs for model, subs in cape._pending_subscriptions if model == TestModel]
    assert len(handlers) == 2
    assert any(handler1 in subs for subs in handlers)
    assert any(handler2 in subs for subs in handlers)


def test_subscribe_invalid_model(cape: Cape):    
    # Try to subscribe to a non-SQLModel class
    class InvalidModel:
        pass
    
    with pytest.raises(TypeError, match="Model InvalidModel is not a SQLModel"):
        @cape.subscribe(InvalidModel)  # type: ignore
        def handler(change: ModelChange):
            pass


def test_subscribe_handler_called(cape: Cape):
    class TestModel(SQLModel):
        pass
    
    mock_handler = Mock()
    
    @cape.subscribe(TestModel)
    def test_handler(change: ModelChange):
        mock_handler(change)
    
    # Create a test change
    test_change: ModelChange[TestModel] = ModelChange(
        table=TestModel.__tablename__,
        event="INSERT",
        payload=TestModel(id=1),
        timestamp=datetime.now()
    )
    
    # Manually call the handler
    model, subscriptions = cape._pending_subscriptions[0]
    handler = subscriptions[0]
    handler(test_change)
    
    # Verify the mock was called with the change
    mock_handler.assert_called_once_with(test_change) 
from typing import Optional
from unittest.mock import Mock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, text, insert, update, delete, Insert, Update, Delete
from sqlmodel import Field, SQLModel, select

from capebase.main import AuthContext, CapeBase
from capebase.models import ModelChange


class SubscribeModel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str


@pytest_asyncio.fixture
def app():
    return FastAPI()


@pytest_asyncio.fixture
async def cape(app):
    cape = CapeBase(
        app=app,
        db_path="sqlite+aiosqlite:///:memory:",
        auth_provider=lambda: AuthContext(),
    )

    async with cape.app.router.lifespan_context(app):
        yield cape

    async with cape.db_session.connect() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest_asyncio.fixture
def mock_handler():
    return Mock()


def test_subscribe_decorator(cape: CapeBase):
    # Test subscription registration
    @cape.subscribe(SubscribeModel)
    def test_handler(change: ModelChange):
        pass

    # Verify the subscription was stored correctly
    assert len(cape._pending_subscriptions) == 1
    model, subscriptions = cape._pending_subscriptions[0]

    # Check the model type is correct
    assert model == SubscribeModel
    # Check the handler was stored
    assert len(subscriptions) == 1
    assert test_handler in subscriptions


def test_multiple_subscriptions_same_model(cape: CapeBase):
    # Register multiple handlers for the same model
    @cape.subscribe(SubscribeModel)
    def handler1(change: ModelChange):
        pass

    @cape.subscribe(SubscribeModel)
    def handler2(change: ModelChange):
        pass

    assert len(cape._pending_subscriptions) == 2

    # Verify both handlers were registered
    handlers = [
        subs for model, subs in cape._pending_subscriptions if model == SubscribeModel
    ]
    assert len(handlers) == 2
    assert any(handler1 in subs for subs in handlers)
    assert any(handler2 in subs for subs in handlers)


def test_subscribe_invalid_model(cape: CapeBase):
    # Try to subscribe to a non-SQLModel class
    class InvalidModel:
        pass

    with pytest.raises(TypeError, match="Model InvalidModel is not a SQLModel"):

        @cape.subscribe(InvalidModel)  # type: ignore
        def handler(change: ModelChange):
            pass


@pytest.mark.asyncio
async def test_subscribe_handler_called_for_statements(app):
    """Test that subscription handlers are called for statement operations"""
    cape = CapeBase(
        app=app,
        db_path="sqlite+aiosqlite:///:memory:",
        auth_provider=lambda: AuthContext(),
        timeout=0.1,
    )

    cape.publish(SubscribeModel)

    mock_handler = Mock()

    @cape.subscribe(SubscribeModel)
    async def test_handler(change: ModelChange):
        mock_handler(change)

    async with cape.app.router.lifespan_context(app):
        # Create test data using statement
        async with cape.get_privileged_session() as session:
            # Test INSERT via statement
            test_model = SubscribeModel(name="Test Item")
            session.add(test_model)
            await session.commit()
            await session.refresh(test_model)

            # verify that item has been added to db
            result = await session.execute(select(SubscribeModel))
            items = result.scalars().first()
            assert items.name == test_model.name

            # Verify INSERT notification
            mock_handler.assert_called_once()
            change = mock_handler.call_args[0][0]
            assert change.event == "INSERT"
            assert change.table == SubscribeModel.__tablename__
            assert change.payload.name == "Test Item"
            mock_handler.reset_mock()

            # Test UPDATE via ORM
            test_model.name = "Updated Item"
            session.add(test_model)
            await session.commit()
            await session.refresh(test_model)

            # Verify UPDATE notification
            mock_handler.assert_called_once()
            change = mock_handler.call_args[0][0]
            assert change.event == "UPDATE"
            assert change.table == SubscribeModel.__tablename__
            assert change.payload.name == "Updated Item"
            mock_handler.reset_mock()

            # Test DELETE via ORM
            await session.delete(test_model)
            await session.commit()

            # Verify DELETE notification
            mock_handler.assert_called_once()
            change = mock_handler.call_args[0][0]
            assert change.event == "DELETE"
            assert change.table == SubscribeModel.__tablename__


@pytest.mark.asyncio
async def test_subscribe_handler_called_for_bulk_operations(app):
    """Test that subscription handlers are called for bulk operations"""
    cape = CapeBase(
        app=app,
        db_path="sqlite+aiosqlite:///:memory:",
        auth_provider=lambda: AuthContext(),
        timeout=0.1,
    )

    cape.publish(SubscribeModel)

    mock_handler = Mock()

    @cape.subscribe(SubscribeModel)
    async def test_handler(change: ModelChange):
        mock_handler(change)

    async with cape.app.router.lifespan_context(app):
        async with cape.get_privileged_session() as session:
            # Test bulk INSERT
            test_models = [SubscribeModel(name=f"Test Item {i}") for i in range(3)]
            session.add_all(test_models)
            await session.commit()
            for model in test_models:
                await session.refresh(model)

            # Verify bulk INSERT notifications (one per item)
            assert mock_handler.call_count == 3
            for i, call in enumerate(mock_handler.call_args_list):
                change = call[0][0]
                assert change.event == "INSERT"
                assert change.table == SubscribeModel.__tablename__
                assert change.payload.name == f"Test Item {i}"
            mock_handler.reset_mock()

            # Test bulk UPDATE
            for model in test_models:
                model.name = "Updated Item"
            session.add_all(test_models)
            await session.commit()
            for model in test_models:
                await session.refresh(model)

            # Verify bulk UPDATE notifications
            assert mock_handler.call_count == 3
            for call in mock_handler.call_args_list:
                change = call[0][0]
                assert change.event == "UPDATE"
                assert change.table == SubscribeModel.__tablename__
                assert change.payload.name == "Updated Item"
            mock_handler.reset_mock()

            # Test bulk DELETE
            result = await session.execute(
                select(SubscribeModel).where(SubscribeModel.name.like("Updated Item%"))
            )
            models_to_delete = result.scalars().all()
            for model in models_to_delete:
                await session.delete(model)
            await session.commit()

            # Verify bulk DELETE notifications
            assert mock_handler.call_count == 3
            for call in mock_handler.call_args_list:
                change = call[0][0]
                assert change.event == "DELETE"
                assert change.table == SubscribeModel.__tablename__


@pytest.mark.asyncio
async def test_subscribe_handler_called_for_sql_constructs(app):
    """Test that subscription handlers are NOT called for SQLAlchemy SQL constructs"""
    cape = CapeBase(
        app=app,
        db_path="sqlite+aiosqlite:///:memory:",
        auth_provider=lambda: AuthContext(),
        timeout=0.1,
    )

    cape.publish(SubscribeModel)

    mock_handler = Mock()
    @cape.subscribe(SubscribeModel)
    async def test_handler(change: ModelChange):
        mock_handler(change)

    async with cape.app.router.lifespan_context(app):
        async with cape.get_privileged_session() as session:
            # Test INSERT using insert() construct
            stmt = insert(SubscribeModel).values(name="Test Item")
            await session.execute(stmt)
            await session.commit()

            # Verify no INSERT notification was sent
            assert mock_handler.call_count == 0
            mock_handler.reset_mock()

            # Test UPDATE using update() construct
            stmt = (
                update(SubscribeModel)
                .where(SubscribeModel.name == "Test Item")
                .values(name="Updated Item")
            )
            await session.execute(stmt)
            await session.commit()

            # Verify no UPDATE notification was sent
            assert mock_handler.call_count == 0
            mock_handler.reset_mock()

            # Test DELETE using delete() construct
            stmt = delete(SubscribeModel).where(SubscribeModel.name == "Updated Item")
            await session.execute(stmt)
            await session.commit()

            # Verify no DELETE notification was sent
            assert mock_handler.call_count == 0
            
            # Verify data changes actually happened despite no notifications
            result = await session.execute(
                select(func.count()).select_from(SubscribeModel)
            )
            count = result.scalar()
            assert count == 0  # Should be 0 after DELETE


@pytest.mark.asyncio
async def test_sql_constructs_bulk_operations(app):
    """Test that subscription handlers do not trigger for bulk SQL operations"""
    cape = CapeBase(
        app=app,
        db_path="sqlite+aiosqlite:///:memory:",
        auth_provider=lambda: AuthContext(),
        timeout=0.1,
    )

    cape.publish(SubscribeModel)

    mock_handler = Mock()
    @cape.subscribe(SubscribeModel)
    async def test_handler(change: ModelChange):
        mock_handler(change)

    async with cape.app.router.lifespan_context(app):
        async with cape.get_privileged_session() as session:
            # Test bulk INSERT
            stmt = insert(SubscribeModel).values([
                {"name": f"Item {i}"} for i in range(3)
            ])
            await session.execute(stmt)
            await session.commit()

            # Verify no INSERT notifications were sent
            assert mock_handler.call_count == 0
            mock_handler.reset_mock()

            # Test bulk UPDATE
            stmt = (
                update(SubscribeModel)
                .where(SubscribeModel.name.like("Item%"))
                .values(name=SubscribeModel.name + " Updated")
            )
            await session.execute(stmt)
            await session.commit()

            # Verify no UPDATE notifications were sent
            assert mock_handler.call_count == 0
            mock_handler.reset_mock()

            # Test bulk DELETE
            stmt = delete(SubscribeModel).where(SubscribeModel.name.like("Item%"))
            await session.execute(stmt)
            await session.commit()

            # Verify no DELETE notifications were sent
            assert mock_handler.call_count == 0

            # Verify data changes actually happened despite no notifications
            result = await session.execute(
                select(func.count()).select_from(SubscribeModel)
            )
            count = result.scalar()
            assert count == 0  # Should be 0 after DELETE

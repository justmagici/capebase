import asyncio
from datetime import datetime
from typing import List, Optional

import pytest
import pytest_asyncio
from async_asgi_testclient import TestClient as AsyncTestClient # type: ignore
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Field, SQLModel

from capebase.api import APIGenerator
from capebase.auth.access_control import AccessControl
from capebase.auth.row_level_security import RowLevelSecurity
from capebase.notification import NotificationEngine
from capebase.database import AsyncDatabaseManager
from capebase.models import ModelChange, AuthContext


# Base model for shared attributesx
class TestItemBase(SQLModel):
    name: str
    description: Optional[str] = Field(default=None)


# Schema for creating items (without ID)
class TestItemCreate(TestItemBase):
    category: str = Field(default="default")  # Additional field for creation


# Schema for updating items (all fields optional)
class TestItemUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None  # New field only available in updates


# Database model (the actual table)
class TestItem(TestItemBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    category: str = Field(default="default")
    status: Optional[str] = Field(default=None)


@pytest_asyncio.fixture
def db():
    db_path = "sqlite+aiosqlite:///:memory:"
    return AsyncDatabaseManager(db_path=db_path)


@pytest_asyncio.fixture
def app():
    return FastAPI()


@pytest_asyncio.fixture
async def client(app):
    return TestClient(app)


@pytest_asyncio.fixture
async def notification_engine():
    return NotificationEngine()


@pytest_asyncio.fixture
async def access_control():
    return AccessControl()


@pytest_asyncio.fixture
async def row_level_security(access_control):
    return RowLevelSecurity(access_control=access_control)


@pytest_asyncio.fixture
def reset_sse_starlette_appstatus_event():
    """
    Fixture that resets the appstatus event in the sse_starlette app.

    Should be used on any test that uses sse_starlette to stream events.
    """
    # See https://github.com/sysid/sse-starlette/issues/59
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None


@pytest_asyncio.fixture
async def api_generator(app, db, notification_engine, row_level_security):
    async def get_session():
        async with db.session() as session:
            yield session

    async with db.connect() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    generator = APIGenerator[TestItem](
        schema=TestItem,
        get_session=get_session,
        prefix="",
        tags=["test"],
        notification_engine=notification_engine,
        row_level_security=row_level_security,
    )
    app.include_router(generator)
    return generator


@pytest.mark.asyncio
async def test_create_item(client, api_generator):
    create_data = TestItemCreate(
        name="Test Item", description="Test Description", category="test-category"
    )

    response = client.post("/testitem", json=create_data.model_dump())
    assert response.status_code == 200

    result = TestItem.model_validate(response.json())
    assert result.name == create_data.name
    assert result.description == create_data.description
    assert result.category == create_data.category
    assert result.status is None  # Default value
    assert isinstance(result.id, int)


@pytest.mark.asyncio
async def test_get_item(client, api_generator):
    payload = TestItem(name="Test Item", description="Test Description")
    create_response = client.post("/testitem", json=payload.model_dump())
    item_id = TestItem.model_validate(create_response.json()).id

    response = client.get(f"/testitem/{item_id}")
    assert response.status_code == 200

    result = TestItem.model_validate(response.json())
    assert result == TestItem(
        id=result.id,  # Keep the generated ID
        **payload.model_dump(exclude={"id"}),  # Compare against original payload
    )


@pytest.mark.asyncio
async def test_get_nonexistent_item(client, api_generator):
    response = client.get("/test/999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_items(client, api_generator):
    # Create multiple items
    payloads = [
        TestItem(name="Item 1", description="Description 1"),
        TestItem(name="Item 2", description="Description 2"),
    ]
    for payload in payloads:
        client.post("/testitem", json=payload.model_dump())

    # List all items
    response = client.get("/testitem")
    assert response.status_code == 200

    results = [TestItem.model_validate(item) for item in response.json()]
    assert len(results) == 2

    for result, payload in zip(results, payloads):
        assert result == TestItem(
            id=result.id,  # Keep the generated ID
            **payload.model_dump(exclude={"id"}),  # Compare against original payload
        )


@pytest.mark.asyncio
async def test_update_item(client, api_generator):
    # First create an item
    create_data = TestItemCreate(
        name="Original Name",
        description="Original Description",
        category="original-category",
    )
    create_response = client.post("/testitem", json=create_data.model_dump())
    item_id = TestItem.model_validate(create_response.json()).id

    # Update with partial data
    update_data = TestItemUpdate(
        name="Updated Name",
        status="active",  # Only updating name and status
    )
    response = client.patch(
        f"/testitem/{item_id}", json=update_data.model_dump(exclude_unset=True)
    )
    assert response.status_code == 200

    result = TestItem.model_validate(response.json())
    assert result.id == item_id
    assert result.name == update_data.name
    assert result.description == create_data.description  # Should remain unchanged
    assert result.category == create_data.category  # Should remain unchanged
    assert result.status == update_data.status


@pytest.mark.asyncio
async def test_update_nonexistent_item(client, api_generator):
    payload = TestItem(name="Updated Name", description="Updated Description")
    response = client.patch("/testitem/999", json=payload.model_dump())
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_item(client, api_generator):
    # Create an item first
    payload = TestItem(name="Test Item", description="Test Description")
    create_response = client.post("/testitem", json=payload.model_dump())
    item_id = TestItem.model_validate(create_response.json()).id

    # Delete the item
    response = client.delete(f"/testitem/{item_id}")
    assert response.status_code == 200

    # Verify item is deleted
    get_response = client.get(f"/testitem/{item_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_nonexistent_item(client, api_generator):
    response = client.delete("/testitem/999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_partial_update(client, api_generator):
    # Create initial item
    create_data = TestItemCreate(
        name="Original Name",
        description="Original Description",
        category="test-category",
    )
    create_response = client.post("/testitem", json=create_data.model_dump())
    item_id = TestItem.model_validate(create_response.json()).id

    # Update only the status
    update_data = TestItemUpdate(status="inactive")
    response = client.patch(
        f"/testitem/{item_id}", json=update_data.model_dump(exclude_unset=True)
    )
    assert response.status_code == 200

    result = TestItem.model_validate(response.json())
    assert result.id == item_id
    assert result.name == create_data.name  # Should remain unchanged
    assert result.description == create_data.description  # Should remain unchanged
    assert result.category == create_data.category  # Should remain unchanged
    assert result.status == update_data.status  # Should be updated


class TestModel(SQLModel, table=True):
    id: int = Field(primary_key=True)
    name: str




# Use ASGI TestClient instead of httpx AsyncClient to bypass streaming issue
@pytest_asyncio.fixture
async def async_client(app):
    async with AsyncTestClient(app) as client:
        yield client


@pytest.mark.asyncio
class TestSubscribeRoute:
    async def test_subscribe_receives_notifications(
        self,
        app,
        async_client,
        notification_engine,
        reset_sse_starlette_appstatus_event,
    ):
        """Test that SSE subscription receives model changes"""
        # Setup API route
        from capebase.api import APIGenerator

        async def get_session():
            class MockSession:
                info = {"auth_context":  AuthContext(id="test_user", context={"org": "org1"})}

            return MockSession()

        api = APIGenerator(
            schema=TestModel,
            get_session=get_session,
            notification_engine=notification_engine,
            row_level_security=MockRLS(),
        )
        app.include_router(api)

        events_task = asyncio.create_task(
            collect_sse_events(
                client=async_client, url="/testmodel/subscribe", expected_events=2
            )
        )
        await asyncio.sleep(0.1)

        # Send notifications
        test_model = TestModel(id=1, name="test")
        changes = [
            ModelChange(
                table="testmodel",
                event="INSERT",
                payload=test_model,
                timestamp=datetime.utcnow(),
            ),
            ModelChange(
                table="testmodel",
                event="UPDATE",
                payload=TestModel(id=1, name="updated"),
                timestamp=datetime.utcnow(),
            ),
        ]

        for change in changes:
            await notification_engine.notify(change)

        events = await events_task

        # Verify events
        assert len(events) == 2
        assert "INSERT" in events[0]
        assert "UPDATE" in events[1]

        asyncio.sleep(1.0)

    async def test_subscribe_filters_unauthorized_changes(
        self,
        app,
        async_client,
        notification_engine,
        reset_sse_starlette_appstatus_event,
    ):
        """Test that unauthorized changes are filtered out"""

        async def get_session():
            class MockSession:
                info = {"auth_context": AuthContext(id="test_user", context={"org": "org1"})}

            return MockSession()

        class RestrictiveRLS(MockRLS):
            def can_read(self, auth_context, model):
                return model.name != "restricted"

        api = APIGenerator(
            schema=TestModel,
            get_session=get_session,
            notification_engine=notification_engine,
            row_level_security=RestrictiveRLS(),
        )
        app.include_router(api)

        events_task = asyncio.create_task(
            collect_sse_events(
                client=async_client, url="/testmodel/subscribe", expected_events=1
            )
        )
        await asyncio.sleep(0.1)

        # Send one authorized and one unauthorized change
        changes = [
            ModelChange(
                table="testmodel",
                event="INSERT",
                payload=TestModel(id=1, name="restricted"),
                timestamp=datetime.utcnow(),
            ),
            ModelChange(
                table="testmodel",
                event="INSERT",
                payload=TestModel(id=2, name="allowed"),
                timestamp=datetime.utcnow(),
            ),
        ]

        for change in changes:
            await notification_engine.notify(change)

        events = await events_task

        # Verify only authorized event was received
        assert len(events) == 1
        assert "allowed" in events[0]
        assert "restricted" not in events[0]


    @pytest.mark.asyncio
    async def test_schema_customization(self, app, db, notification_engine, row_level_security):
        """Test that create_schema and update_schema are properly used"""
        async def get_session():
            async with db.session() as session:
                yield session

        # Create custom schemas
        class CustomCreate(SQLModel):
            name: str
            description: str = "new"  # Default value for creation

        class CustomUpdate(SQLModel):
            name: Optional[str] = None
            status: Optional[str] = None  # Additional field for updates

        async with db.connect() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

        # Initialize API with custom schemas
        generator = APIGenerator[TestItem](
            schema=TestItem,
            create_schema=CustomCreate,
            update_schema=CustomUpdate,
            get_session=get_session,
            notification_engine=notification_engine,
            row_level_security=row_level_security,
        )
        app.include_router(generator)

        async with AsyncTestClient(app) as client:
            # Test create with custom schema
            create_data = {"name": "Test Item"}
            response = await client.post("/testitem", json=create_data)
            assert response.status_code == 200
            created = response.json()
            assert created["name"] == "Test Item"
            assert created["description"] == "new"  # Default from CustomCreate

            # Test update with custom schema
            item_id = created["id"]
            update_data = {"status": "active"}  # Using CustomUpdate field
            response = await client.patch(f"/testitem/{item_id}", json=update_data)
            assert response.status_code == 200
            updated = response.json()
            assert updated["status"] == "active"


# Mock RLS for testing
class MockRLS:
    def can_read(self, auth_context, model):
        return True


# Helper function to bypass issue with using AsyncClient.stream to collect SSE events
async def collect_sse_events(
    client: AsyncTestClient,
    url: str,
    expected_events: int = 1,
    timeout: float = 5.0,
    connection_ready: Optional[asyncio.Event] = None,
) -> List[str]:
    """
    Collect SSE events from an endpoint.

    Args:
        client: The ASGI TestClient instance
        url: The SSE endpoint URL
        expected_events: Number of events to collect before returning
        timeout: Maximum time to wait for events in seconds
        connection_ready: Optional event to signal when connection is established

    Returns:
        List of collected event data strings

    Raises:
        asyncio.TimeoutError: If events aren't received within timeout
        AssertionError: If response status is not 200
    """

    async def _collect() -> List[str]:
        events = []
        response = await client.get(url, stream=True)
        assert response.status_code == 200, (
            f"Expected 200 status code, got {response.status_code}"
        )

        buffer = ""

        try:
            async for char in response.iter_content():
                char = char.decode("utf-8")
                buffer += char
                if not buffer.endswith("\n"):
                    continue

                line = buffer.strip()
                if line.startswith("data:"):
                    events.append(line)
                    if len(events) == expected_events:
                        break

                buffer = ""
            return events
        except asyncio.CancelledError:
            return events
        finally:
            response.close()

    # Run collection with timeout
    return await asyncio.wait_for(_collect(), timeout=timeout)



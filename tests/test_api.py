from typing import Optional

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Field, SQLModel

from cape.api import APIGenerator
from cape.database import AsyncDatabaseManager


# Base model for shared attributes
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
async def api_generator(app, db):
    async def get_session():
        async with db.session() as session:
            yield session

    async with db.connect() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    generator = APIGenerator[TestItem](
        schema=TestItem, get_session=get_session, prefix="", tags=["test"]
    )
    app.include_router(generator)
    return generator


@pytest.mark.asyncio
async def test_create_item(client, api_generator):
    create_data = TestItemCreate(
        name="Test Item",
        description="Test Description",
        category="test-category"
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
        **payload.model_dump(exclude={"id"})  # Compare against original payload
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
        TestItem(name="Item 2", description="Description 2")
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
            **payload.model_dump(exclude={"id"})  # Compare against original payload
        )


@pytest.mark.asyncio
async def test_update_item(client, api_generator):
    # First create an item
    create_data = TestItemCreate(
        name="Original Name",
        description="Original Description",
        category="original-category"
    )
    create_response = client.post("/testitem", json=create_data.model_dump())
    item_id = TestItem.model_validate(create_response.json()).id

    # Update with partial data
    update_data = TestItemUpdate(
        name="Updated Name",
        status="active"  # Only updating name and status
    )
    response = client.put(
        f"/testitem/{item_id}", 
        json=update_data.model_dump(exclude_unset=True)
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
    response = client.put("/testitem/999", json=payload.model_dump())
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
        category="test-category"
    )
    create_response = client.post("/testitem", json=create_data.model_dump())
    item_id = TestItem.model_validate(create_response.json()).id

    # Update only the status
    update_data = TestItemUpdate(status="inactive")
    response = client.put(
        f"/testitem/{item_id}", 
        json=update_data.model_dump(exclude_unset=True)
    )
    assert response.status_code == 200
    
    result = TestItem.model_validate(response.json())
    assert result.id == item_id
    assert result.name == create_data.name  # Should remain unchanged
    assert result.description == create_data.description  # Should remain unchanged
    assert result.category == create_data.category  # Should remain unchanged
    assert result.status == update_data.status  # Should be updated

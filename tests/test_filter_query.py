from typing import Optional

import pytest
import pytest_asyncio
from sqlmodel import Field, SQLModel, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import update, delete, and_

from capebase.auth.access_control import AccessControl
from capebase.models import AuthContext
from capebase.auth.row_level_security import RLSConfig, RowLevelSecurity


# Test Models
class FilterQueryDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    content: str
    owner_id: str
    org_id: str


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create in-memory database for testing"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


@pytest_asyncio.fixture(scope="function")
async def async_session(engine):
    """Create database session"""
    from sqlalchemy.ext.asyncio import AsyncSession
    async_session = AsyncSession(engine)
    try:
        yield async_session
    finally:
        await async_session.close()

@pytest_asyncio.fixture
async def rls(async_session):
    """Create RLS instance with test policies"""
    ac = AccessControl()
    rls = RowLevelSecurity(access_control=ac)

    # Add test policies
    rls.register_model(
        RLSConfig(model=FilterQueryDocument, action="read", role="*", owner_field="owner_id")
    )

    rls.register_model(
        RLSConfig(
            model=FilterQueryDocument, action="write", role="*", owner_field="owner_id"
        )
    )

    rls.register_model(
        RLSConfig(
            model=FilterQueryDocument,
            action="read",
            role="*",
            context_fields=["org_id"],
        )
    )

    return rls


@pytest_asyncio.fixture
async def sample_data(async_session):
    """Create sample documents"""
    docs = [
        FilterQueryDocument(
            title="Doc 1", content="Content 1", owner_id="alice", org_id="org1"
        ),
        FilterQueryDocument(
            title="Doc 2", content="Content 2", owner_id="bob", org_id="org1"
        ),
        FilterQueryDocument(
            title="Doc 3", content="Content 3", owner_id="charlie", org_id="org2"
        ),
        FilterQueryDocument(
            title="Doc 4", content="Content 4", owner_id="bob", org_id="org2"
        ),
    ]
    for doc in docs:
        async_session.add(doc)
    await async_session.commit()


@pytest.mark.asyncio
async def test_filter_query_owner_access(async_session, rls, sample_data):
    """Test that users can access their own documents"""
    query = select(FilterQueryDocument)
    filtered_query = rls.filter_query(
        query=query, action="read", auth_context=AuthContext(id="bob", context={})
    )

    result = await async_session.execute(filtered_query)
    results = result.scalars().all()
    assert len(results) == 2
    assert all(doc.owner_id == "bob" for doc in results)


@pytest.mark.asyncio
async def test_filter_query_org_access(async_session, rls, sample_data):
    """Test that users can access documents in their org"""
    query = select(FilterQueryDocument)
    filtered_query = rls.filter_query(
        query=query, action="read", auth_context=AuthContext(id="alice", context={"org_id": "org1"})
    )

    result = await async_session.execute(filtered_query)
    results = result.scalars().all()
    assert len(results) == 2  # Should see all org1 docs
    assert all(doc.org_id == "org1" for doc in results)


@pytest.mark.asyncio
async def test_filter_query_no_access(async_session, rls, sample_data):
    """Test that users cannot access documents they shouldn't"""
    query = select(FilterQueryDocument)
    filtered_query = rls.filter_query(
        query=query,
        action="read",
        auth_context=AuthContext(id="dave", context={"org_id": "org3"})
    )

    result = await async_session.execute(filtered_query)
    results = result.scalars().all()
    assert len(results) == 0


@pytest.mark.asyncio
async def test_filter_query_combined_access(async_session, rls, sample_data):
    """Test combined access through ownership and org membership"""
    query = select(FilterQueryDocument)
    filtered_query = rls.filter_query(
        query=query,
        action="read",
        auth_context=AuthContext(id="bob", context={"org_id": "org2"})
    )

    result = await async_session.execute(filtered_query)
    results = result.scalars().all()
    assert len(results) == 3  # Should see own doc and org2 docs
    titles = {doc.title for doc in results}
    assert titles == {"Doc 2", "Doc 3", "Doc 4"}


@pytest.mark.asyncio
async def test_filter_query_update(async_session, rls, sample_data):
    """Test filtering for update queries"""
    query = update(FilterQueryDocument).values(content="Updated")

    filtered_query = rls.filter_query(
        query=query,
        action="write",
        auth_context=AuthContext(id="bob", context={"org_id": "org1"})
    )

    await async_session.execute(filtered_query)
    await async_session.commit()

    result = await async_session.execute(select(FilterQueryDocument))
    results = result.scalars().all()
    updated = [doc for doc in results if doc.content == "Updated"]
    assert len(updated) == 2
    assert all(doc.owner_id == "bob" for doc in updated)


@pytest.mark.asyncio
async def test_filter_query_delete(async_session, rls, sample_data):
    """Test filtering for delete queries"""
    query = delete(FilterQueryDocument).where(FilterQueryDocument.org_id == "org1")

    rls.register_model(
        RLSConfig(
            model=FilterQueryDocument,
            action="delete",
            role="*",
            owner_field="owner_id",
        )
    )

    filtered_query = rls.filter_query(
        query=query,
        action="delete",
        auth_context=AuthContext(id="bob", context={"org_id": "org1"})
    )

    await async_session.execute(filtered_query)
    await async_session.commit()

    result = await async_session.execute(select(FilterQueryDocument))
    results = result.scalars().all()
    assert len(results) == 3  # Should have deleted org1 doc where bob is owner
    assert not any(doc.owner_id == "bob" and doc.org_id == "org1" for doc in results)


@pytest.mark.asyncio
async def test_filter_query_complex_update(async_session, rls, sample_data):
    """Test complex update query with multiple conditions"""
    stmt = (
        update(FilterQueryDocument)
        .where(
            and_(
                FilterQueryDocument.owner_id == "alice",
                FilterQueryDocument.org_id == "org1",
                FilterQueryDocument.title.like("Doc%")
            )
        )
        .values(content="Complex update")
    )

    filtered_query = rls.filter_query(
        query=stmt,
        action="write",
        auth_context=AuthContext(id="alice", context={"org_id": "org1"})
    )

    await async_session.execute(filtered_query)
    await async_session.commit()
    
    result = await async_session.execute(select(FilterQueryDocument))
    results = result.scalars().all()
    
    updated_docs = [doc for doc in results if doc.content == "Complex update"]
    assert len(updated_docs) == 1
    assert all(doc.owner_id == "alice" for doc in updated_docs)
    assert all(doc.org_id == "org1" for doc in updated_docs)

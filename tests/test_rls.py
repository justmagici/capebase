import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import insert
from sqlmodel import SQLModel, Field, select, update
from typing import Optional, Sequence, Type

from cape.main import Cape

class SecureDocument(SQLModel, table=True):
    # SecureDocument could be called multiple times, so we need to extend the existing table
    __table_args__ = {'extend_existing': True}
    
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    content: str
    owner_id: str
    org_id: str

# Fixtures
@pytest.fixture
def app():
    return FastAPI()

@pytest_asyncio.fixture
async def cape(app):
    cape = Cape(app=app, db_path="sqlite+aiosqlite:///:memory:")

    async with cape.app.router.lifespan_context(app):
        yield cape

    async with cape.db_session.connect() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

@pytest_asyncio.fixture
def sample_docs(cape):
    docs = [
        SecureDocument(title="Doc 1", content="Content 1", owner_id="alice", org_id="org1"),
        SecureDocument(title="Doc 2", content="Content 2", owner_id="bob", org_id="org1"),
        SecureDocument(title="Doc 3", content="Content 3", owner_id="alice", org_id="org2"),
        SecureDocument(title="Doc 4", content="Content 4", owner_id="bob", org_id="org2")
    ]
    return docs

@pytest_asyncio.fixture(autouse=True)
async def setup_test_permission(cape):
    cape.permission_required(SecureDocument, role='admin', actions=["*"])
    cape.permission_required(SecureDocument, role="*", actions=["read"], context_fields=["org_id"])
    cape.permission_required(SecureDocument, role="*", actions=["read", "create", "update"], owner_field="owner_id")

@pytest_asyncio.fixture(autouse=True)
async def create_test_docs(cape: Cape, sample_docs: list[SecureDocument], setup_test_permission: None):
    async with cape.get_session(subject="admin", context={}) as session:
        for doc in sample_docs:
            session.add(doc)
        await session.commit()

async def query_docs(cape: Cape, SecureDocument: Type[SQLModel], subject: str, context: dict = {}) -> Sequence[SQLModel]:
    """Query documents with security context.
    
    Args:
        cape: Cape instance
        SecureDocument: Dynamic model class from fixture
        subject: User ID for security context
        context: Additional security context
    """
    async with cape.get_session(subject=subject, context=context) as session:
        result = await session.execute(select(SecureDocument))
        return result.scalars().all()

# Test Cases
@pytest.mark.asyncio
async def test_read_own_documents(cape, sample_docs):
    results = await query_docs(cape, SecureDocument, "alice")
    
    assert len(results) == 2  # Should only see owned docs
    titles = {doc.title for doc in results}
    assert titles == {"Doc 1", "Doc 3"}

@pytest.mark.asyncio
async def test_read_with_org_context(cape, sample_docs):
    results = await query_docs(cape, SecureDocument, "alice", {"org_id": "org1"})
    
    assert len(results) == 3  # Should see own docs (1,3) and org1 docs (2)
    titles = {doc.title for doc in results}
    assert titles == {"Doc 1", "Doc 2", "Doc 3"}

@pytest.mark.asyncio
async def test_unauthorized_access(cape, sample_docs):
    results = await query_docs(cape, SecureDocument, "carol")
    assert len(results) == 0  # Should see no docs

@pytest.mark.asyncio
async def test_write_own_documents(cape, sample_docs):
    async with cape.get_session(subject="alice", context={"org_id": "org1"}) as session:
        doc = await session.get(SecureDocument, 1)  # Doc owned by alice
        doc.content = "Updated content"
        session.add(doc)
        await session.commit()

        updated_doc = await session.get(SecureDocument, 1)
        assert updated_doc.content == "Updated content"

@pytest.mark.asyncio
async def test_insert_statement(cape, sample_docs):
    """Test that users can insert documents using SQLAlchemy insert statement"""
    async with cape.get_session(subject="alice", context={"org_id": "org1"}) as session:
        # Create new document using insert statement
        stmt = insert(SecureDocument).values(
            title="New Doc",
            content="New Content",
            owner_id="alice",  # Should match the subject
            org_id="org1"      # Should match the context
        )
        await session.execute(stmt)
        await session.commit()

        # Verify the document was inserted
        result = await session.execute(
            select(SecureDocument).where(SecureDocument.title == "New Doc")
        )
        inserted_doc = result.scalars().first()
        
        assert inserted_doc is not None
        assert inserted_doc.owner_id == "alice"
        assert inserted_doc.org_id == "org1"
        assert inserted_doc.content == "New Content"

@pytest.mark.asyncio
async def test_insert_statement_permission_denied(cape, sample_docs):
    """Test that users cannot insert documents with incorrect permissions using insert statement"""
    async with cape.get_session(subject="alice", context={"org_id": "org1"}) as session:
        with pytest.raises(PermissionError):
            # Try to create document owned by bob (should fail)
            stmt = insert(SecureDocument).values(
                title="Unauthorized Doc",
                content="Content",
                owner_id="bob",    # Different from subject (alice)
                org_id="org1"
            )
            await session.execute(stmt)
            await session.commit()

@pytest.mark.asyncio
async def test_write_unauthorized_document(cape, sample_docs):
    async with cape.get_session(subject="bob", context={"org_id": "org1"}) as session:
        doc = await session.get(SecureDocument, 2)  # Doc owned by bob

    with pytest.raises(PermissionError):
        async with cape.get_session(subject="alice", context={"org_id": "org1"}) as session:
            doc.content = "Updated content"
            session.add(doc)
            await session.commit()

@pytest.mark.asyncio
async def test_change_owner_field(cape, sample_docs):
    async with cape.get_session(subject="alice", context={"org_id": "org1"}) as session:
        doc = await session.get(SecureDocument, 1)  # Doc owned by alice
        doc.content = "Updated content"
        session.add(doc)
        await session.commit()

        updated_doc = await session.get(SecureDocument, 1)
        assert updated_doc.content == "Updated content"
        updated_doc.owner_id = "bob"
        session.add(updated_doc)
        await session.commit()

        with pytest.raises(PermissionError):
            updated_doc = await session.get(SecureDocument, 1)
            updated_doc.content = "This should fail"
            session.add(updated_doc)
            await session.commit()

@pytest.mark.asyncio
async def test_bulk_update_own_documents(cape, sample_docs):
    async with cape.get_session(subject="alice") as session:
        stmt = (
            update(SecureDocument)
            .where(SecureDocument.owner_id == "alice")
            .values(content="Bulk updated")
        )
        await session.execute(stmt)
        await session.commit()
        
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.content == "Bulk updated")
        )
        results = results.scalars().all()
        
        assert len(results) == 2  # Should update only owned docs
        titles = {doc.title for doc in results}
        assert titles == {"Doc 1", "Doc 3"}

@pytest.mark.asyncio
async def test_cross_org_access(cape, sample_docs):
    # Test bob's access to org2
    results = await query_docs(cape, SecureDocument, "bob", {"org_id": "org2"})

    assert len(results) == 3  # Should see own docs (2,4) and org2 docs (3)
    titles = {doc.title for doc in results}
    assert titles == {"Doc 2", "Doc 3", "Doc 4"}

@pytest.mark.asyncio
async def test_context_isolation(cape, sample_docs):
    # Test that context doesn't leak between queries
    results1 = await query_docs(cape, SecureDocument, "alice", {"org_id": "org1"})
    results2 = await query_docs(cape, SecureDocument, "alice")  # No context
    
    assert len(results1) == 3  # With org context
    assert len(results2) == 2  # Only owned docs
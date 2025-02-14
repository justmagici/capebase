from typing import Optional, Sequence, Type, List

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, insert, update
from sqlalchemy.engine import Row
from sqlmodel import Field, SQLModel, select

from capebase.main import AuthContext, CapeBase, PermissionDeniedError
from capebase.models import FromSubject


class SecureDocument(SQLModel, table=True):
    # SecureDocument could be called multiple times, so we need to extend the existing table
    __table_args__ = {'extend_existing': True}
    
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    content: str
    owner_id: FromSubject
    org_id: str

# Create a related model for testing joins
class RelatedDoc(SQLModel, table=True):
    __table_args__ = {'extend_existing': True}
    id: Optional[int] = Field(default=None, primary_key=True)
    doc_id: int = Field(foreign_key="securedocument.id")
    note: str

# Fixtures
@pytest_asyncio.fixture
def app():
    return FastAPI()

async def get_user_context():
    return AuthContext()

@pytest_asyncio.fixture
async def cape(app):
    cape = CapeBase(app=app, db_path="sqlite+aiosqlite:///:memory:", auth_provider=get_user_context)

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
    cape.permission_required(SecureDocument, role="*", actions=["create"])
    cape.permission_required(SecureDocument, role="*", actions=["read"], context_fields=["org_id"])
    cape.permission_required(SecureDocument, role="*", actions=["read", "create", "update", "delete"], owner_field="owner_id")
    cape.permission_required(RelatedDoc, role="*", actions=["read", "create"])

@pytest_asyncio.fixture(autouse=True)
async def create_test_docs(cape: CapeBase, setup_test_permission):
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        s1 = SecureDocument(title="Doc 1", content="Content 1", org_id="org1")
        s2 = SecureDocument(title="Doc 3", content="Content 3", org_id="org2")

        session.add(s1)
        session.add(s2)
        await session.commit()

    async with cape.get_session(AuthContext(subject="bob")) as session:
        s3 = SecureDocument(title="Doc 2", content="Content 2", org_id="org1")
        s4 = SecureDocument(title="Doc 4", content="Content 4", org_id="org2")

        session.add(s3)
        session.add(s4)
        await session.commit()

async def query_docs(cape: CapeBase, SecureDocument: Type[SQLModel], subject: str, context: dict = {}) -> Sequence[SQLModel]:
    """Query documents with security context.
    
    Args:
        cape: Cape instance
        SecureDocument: Dynamic model class from fixture
        subject: User ID for security context
        context: Additional security context
    """
    async with cape.get_session(AuthContext(subject=subject, context=context)) as session:
        result = await session.execute(select(SecureDocument))
        return result.scalars().all()

# Test Cases
@pytest.mark.asyncio
async def test_read_own_documents(cape):
    results = await query_docs(cape, SecureDocument, "alice")
    
    assert len(results) == 2  # Should only see owned docs
    titles = {doc.title for doc in results}
    assert titles == {"Doc 1", "Doc 3"}

@pytest.mark.asyncio
async def test_read_with_org_context(cape):
    results = await query_docs(cape, SecureDocument, "alice", {"org_id": "org1"})
    
    assert len(results) == 3  # Should see own docs (1,3) and org1 docs (2)
    titles = {doc.title for doc in results}
    assert titles == {"Doc 1", "Doc 2", "Doc 3"}

@pytest.mark.asyncio
async def test_unauthorized_access(cape):
    results = await query_docs(cape, SecureDocument, "carol")
    assert len(results) == 0  # Should see no docs

@pytest.mark.asyncio
async def test_write_own_documents(cape):
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        doc = await session.get(SecureDocument, 1)  # Doc owned by alice
        doc.content = "Updated content"
        session.add(doc)
        await session.commit()

        updated_doc = await session.get(SecureDocument, 1)
        assert updated_doc.content == "Updated content"

@pytest.mark.asyncio
async def test_create_multiple_documents(cape):
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        # Create multiple documents in the same session
        doc1 = SecureDocument(
            title="Document 1",
            org_id="org1",
            content="Document 1 content"
        )
        doc2 = SecureDocument(
            title="Document 2",
            org_id="org1",
            content="Document 2 content"
        )
        session.add_all([doc1, doc2])
        await session.commit()

        # Verify both documents were created
        created_doc1 = await session.get(SecureDocument, doc1.id)
        created_doc2 = await session.get(SecureDocument, doc2.id)
        
        assert created_doc1.content == "Document 1 content"
        assert created_doc2.content == "Document 2 content"
        assert created_doc1.owner_id == "alice"
        assert created_doc2.owner_id == "alice"


@pytest.mark.asyncio
async def test_insert_statement(cape):
    """Test that users can insert documents using SQLAlchemy insert statement"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        # Create new document using insert statement
        stmt = insert(SecureDocument).values(
            title="New Doc",
            content="New Content",
            org_id="org1"
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
async def test_insert_statement_system_managed_fields(cape):
    """Test that users cannot insert documents with incorrect permissions using insert statement"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        with pytest.raises(HTTPException):
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
async def test_insert_statement_success(cape):
    """Test that users can insert documents with correct permissions using insert statement"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        
        stmt = insert(SecureDocument).values(
            title="Auto-populated Doc",
            content="Content",
            org_id="org1"
        )
        await session.execute(stmt)
        await session.commit()
        
        # Verify auto-populated fields
        result = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title == "Auto-populated Doc")
        )
        doc = result.scalar_one()
        
        assert doc.title == "Auto-populated Doc"
        assert doc.owner_id == "alice"  # Auto-populated from subject
        assert doc.org_id == "org1"     # Auto-populated from context

@pytest.mark.asyncio
async def test_write_unauthorized_document(cape):
    async with cape.get_session(AuthContext(subject="bob", context={"org_id": "org1"})) as session:
        doc = await session.get(SecureDocument, 3)  # Doc owned by bob

    with pytest.raises(PermissionDeniedError):
        async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
            doc.content = "Updated content"
            session.add(doc)
            await session.commit()

@pytest.mark.asyncio
async def test_write_authorized_document(cape):
    """Test that updates are allowed when user has proper authorization"""
    # First get the document as bob
    async with cape.get_session(AuthContext(subject="bob", context={"org_id": "org1"})) as session:
        doc = await session.get(SecureDocument, 3)  # Doc owned by bob
        original_content = doc.content

    # Update as bob (should succeed)
    async with cape.get_session(AuthContext(subject="bob", context={"org_id": "org1"})) as session:
        doc.content = "Updated by owner"
        session.add(doc)
        await session.commit()

        # Verify the update succeeded
        await session.refresh(doc)
        assert doc.content == "Updated by owner"
        assert doc.content != original_content
        assert doc.owner_id == "bob"  # Owner should remain unchanged

@pytest.mark.asyncio
async def test_change_owner_field_permission_denied(cape):
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        doc = await session.get(SecureDocument, 1)  # Doc owned by alice
        doc.content = "Updated content"
        session.add(doc)
        await session.commit()

        with pytest.raises(PermissionDeniedError):
            updated_doc = await session.get(SecureDocument, 1)
            assert updated_doc.content == "Updated content"
            updated_doc.owner_id = "bob"
            session.add(updated_doc)
            await session.commit()

@pytest.mark.asyncio
async def test_bulk_update_own_documents(cape, sample_docs):
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
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

# Add test for bulk update with permission denied error
@pytest.mark.asyncio
async def test_bulk_update_permission_denied(cape):
    async with cape.get_session(AuthContext(subject="bob")) as session:
        stmt = (
            update(SecureDocument)
            .where(SecureDocument.owner_id == "alice")
            .values(content="Bulk updated")
        )

        await session.execute(stmt)
        await session.commit()

        # Verify no documents owned by alice were updated as bob does not have permission
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.owner_id == "alice")
        )
        docs = results.scalars().all()
        
        for doc in docs:
            assert doc.content != "Bulk updated"

@pytest.mark.asyncio
async def test_cross_org_access(cape):
    # Test bob's access to org2
    results = await query_docs(cape, SecureDocument, "bob", {"org_id": "org2"})

    assert len(results) == 3  # Should see own docs (2,4) and org2 docs (3)
    titles = {doc.title for doc in results}
    assert titles == {"Doc 2", "Doc 3", "Doc 4"}

@pytest.mark.asyncio
async def test_context_isolation(cape):
    # Test that context doesn't leak between queries
    results1 = await query_docs(cape, SecureDocument, "alice", {"org_id": "org1"})
    results2 = await query_docs(cape, SecureDocument, "alice")  # No context
    
    assert len(results1) == 3  # With org context
    assert len(results2) == 2  # Only owned docs


@pytest.mark.asyncio
async def test_bulk_delete_own_documents(cape):
    """Test that users can bulk delete their own documents using delete statement"""
    async with cape.get_session(AuthContext(subject="alice")) as session:
        # Delete all documents owned by alice
        stmt = delete(SecureDocument).where(SecureDocument.owner_id == "alice")
        await session.execute(stmt)
        await session.commit()
        
        # Verify alice's documents are deleted
        results = await session.execute(
            select(SecureDocument).where(SecureDocument.owner_id == "alice")
        )
        alice_docs = results.scalars().all()
        assert len(alice_docs) == 0
        
    async with cape.get_session(AuthContext(subject="bob")) as session:
        # Verify bob's documents still exist
        results = await session.execute(
            select(SecureDocument).where(SecureDocument.owner_id == "bob")
        )
        bob_docs = results.scalars().all()
        assert len(bob_docs) == 2

@pytest.mark.asyncio
async def test_unauthorized_bulk_delete(cape):
    """Test that users cannot bulk delete documents they don't own"""
    async with cape.get_session(AuthContext(subject="bob")) as session:
        # Attempt to delete alice's documents
        stmt = delete(SecureDocument).where(SecureDocument.owner_id == "alice").returning(SecureDocument.id)
        result = await session.execute(stmt)
        deleted_count = len(result.scalars().all())
        assert deleted_count == 0
        await session.commit()

    async with cape.get_session(AuthContext(subject="alice")) as session:
        # Verify alice's documents were not deleted
        results = await session.execute(
            select(SecureDocument).where(SecureDocument.owner_id == "alice")
        )
        alice_docs = results.scalars().all()
        assert len(alice_docs) == 2
        stmt = delete(SecureDocument).where(SecureDocument.owner_id == "alice").returning(SecureDocument.id)
        result = await session.execute(stmt)
        await session.commit()
        deleted_count = len(result.scalars().all())
        assert deleted_count == 2

@pytest.mark.asyncio
async def test_bulk_insert_with_statement(cape):
    """Test bulk insert using insert statement"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        # Create multiple documents using insert statement
        stmt = insert(SecureDocument).values([
            {
                "title": f"Bulk Doc {i}",
                "content": f"Bulk Content {i}",
                "org_id": "org1"
            }
            for i in range(3)
        ])
        await session.execute(stmt)
        await session.commit()
        
        # Verify documents were inserted with correct ownership
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title.like("Bulk Doc%"))
        )
        docs = results.scalars().all()
        
        assert len(docs) == 3
        for doc in docs:
            assert doc.owner_id == "alice"
            assert doc.org_id == "org1"
            assert "Bulk Content" in doc.content

@pytest.mark.asyncio
async def test_bulk_insert_system_managed_fields_permission_denied(cape):
    """Test bulk insert fails when trying to set unauthorized ownership"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        with pytest.raises(PermissionDeniedError):
            stmt = insert(SecureDocument).values([
                {
                    "title": f"Bulk Doc {i}",
                    "content": f"Bulk Content {i}",
                    "owner_id": "bob",  # Trying to create docs owned by bob
                    "org_id": "org1"
                }
                for i in range(3)
            ])
            await session.execute(stmt)
            await session.commit()

@pytest.mark.asyncio
async def test_bulk_insert_system_managed_fields(cape):
    """Test that system-managed fields are properly handled in bulk inserts"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        # Create multiple documents with system-managed fields
        stmt = insert(SecureDocument).values([
            {
                "title": "Doc A",
                "content": "Content A",
                "org_id": "org1"
                # owner_id intentionally omitted - should be auto-populated
            },
            {
                "title": "Doc B",
                "content": "Content B",
                "org_id": "org1"
                # owner_id intentionally omitted - should be auto-populated
            }
        ])
        await session.execute(stmt)
        await session.commit()

        # Verify all documents were created with correct system-managed fields
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title.in_(["Doc A", "Doc B"]))
            .order_by(SecureDocument.title)
        )
        docs = results.scalars().all()
        
        assert len(docs) == 2
        for doc in docs:
            assert doc.owner_id == "alice"  # System-managed field properly set
            assert doc.org_id == "org1"

@pytest.mark.asyncio
async def test_complex_update_query(cape):
    """Test complex update query with multiple conditions"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        # Update documents that match multiple conditions
        stmt = (
            update(SecureDocument)
            .where(
                SecureDocument.owner_id == "alice",
                SecureDocument.org_id == "org1",
                SecureDocument.title.like("Doc%")
            )
            .values(content="Complex update")
        )
        await session.execute(stmt)
        await session.commit()
        
        # Verify only matching documents were updated
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.content == "Complex update")
        )
        updated_docs = results.scalars().all()
        
        assert len(updated_docs) == 1  # Should only update Doc 1
        assert all(doc.owner_id == "alice" for doc in updated_docs)
        assert all(doc.org_id == "org1" for doc in updated_docs)

@pytest.mark.asyncio
async def test_join_query_permissions(cape):
    """Test that permissions are properly enforced in join queries"""
    
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        # Create some related documents
        doc = await session.execute(
            select(SecureDocument).where(SecureDocument.owner_id == "alice").limit(1)
        )
        doc = doc.scalar_one()
        
        related = RelatedDoc(doc_id=doc.id, note="Test note")
        session.add(related)
        await session.commit()
        
        # Test join query
        results = await session.execute(
            select(SecureDocument, RelatedDoc)
            .join(RelatedDoc, SecureDocument.id == RelatedDoc.doc_id)
        )
        joined_results: List[Row] = results.all()
        
        assert len(joined_results) == 1
        row = joined_results[0]
        assert row[0].owner_id == "alice"
        assert row[1].note == "Test note"

@pytest.mark.asyncio
async def test_filter_query_basic(cape):
    """Test basic filter query functionality"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        # Filter for documents with specific title
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title == "Doc 1")
        )
        docs: List[SecureDocument] = results.scalars().all()
        
        assert len(docs) == 1
        assert docs[0].title == "Doc 1"
        assert docs[0].owner_id == "alice"

@pytest.mark.asyncio
async def test_filter_query_multiple_conditions(cape):
    """Test filter query with multiple conditions"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        results = await session.execute(
            select(SecureDocument)
            .where(
                SecureDocument.org_id == "org1",
                SecureDocument.title.like("Doc%")
            )
        )
        docs = results.scalars().all()
        
        assert len(docs) == 2  # Should see Doc 1 (owned) and Doc 2 (same org)
        titles = {doc.title for doc in docs}
        assert titles == {"Doc 1", "Doc 2"}

@pytest.mark.asyncio
async def test_filter_query_or_conditions(cape):
    """Test filter query with OR conditions"""
    async with cape.get_session(AuthContext(subject="alice")) as session:
        from sqlalchemy import or_
        
        results = await session.execute(
            select(SecureDocument)
            .where(
                or_(
                    SecureDocument.title == "Doc 1",
                    SecureDocument.title == "Doc 3"
                )
            )
        )
        docs = results.scalars().all()
        
        assert len(docs) == 2
        titles = {doc.title for doc in docs}
        assert titles == {"Doc 1", "Doc 3"}
        assert all(doc.owner_id == "alice" for doc in docs)

@pytest.mark.asyncio
async def test_filter_query_with_order(cape):
    """Test filter query with ordering"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.org_id == "org1")
            .order_by(SecureDocument.title.desc())
        )
        docs = results.scalars().all()
        
        assert len(docs) == 2
        assert [doc.title for doc in docs] == ["Doc 2", "Doc 1"]

@pytest.mark.asyncio
async def test_filter_query_context_list_conditions(cape):
    """Test filter_query method with list/tuple context values"""
    # Create additional test documents with different org_ids
    async with cape.get_session(AuthContext(subject="admin")) as session:
        docs = [
            SecureDocument(
                title=f"MultiOrg Doc {i}",
                content="test",
                org_id=f"org{i}"
            )
            for i in range(1, 4)  # Creates docs for org1, org2, org3
        ]
        session.add_all(docs)
        await session.commit()

    # Test with list in context
    async with cape.get_session(
        AuthContext(
            subject="bob",
            context={"org_id": ["org1", "org2"]}  # Should only see docs from org1 and org2
        )
    ) as session:
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title.like("MultiOrg%"))
        )
        docs = results.scalars().all()
        
        assert len(docs) == 2  # Should only see docs from org1 and org2
        org_ids = {doc.org_id for doc in docs}
        assert org_ids == {"org1", "org2"}
        
    # Test with tuple in context
    async with cape.get_session(
        AuthContext(
            subject="bob",
            context={"org_id": ("org2", "org3")}  # Should only see docs from org2 and org3
        )
    ) as session:
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title.like("MultiOrg%"))
        )
        docs = results.scalars().all()
        
        assert len(docs) == 2  # Should only see docs from org2 and org3
        org_ids = {doc.org_id for doc in docs}
        assert org_ids == {"org2", "org3"}

    # Test empty list in context
    async with cape.get_session(
        AuthContext(
            subject="bob",
            context={"org_id": []}  # Empty list should return no results
        )
    ) as session:
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title.like("MultiOrg%"))
        )
        docs = results.scalars().all()
        
        assert len(docs) == 0  # Should see no docs when empty list provided

@pytest.mark.asyncio
async def test_update_system_managed_fields_blocked(cape):
    """Test that system-managed fields cannot be updated directly"""
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        # First create a document
        doc = SecureDocument(
            title="Test Doc",
            content="Original content",
            org_id="org1"
        )
        session.add(doc)
        await session.commit()
        
        # Attempt different ways to update system-managed fields
        
        # 1. Try direct update via model
        with pytest.raises(PermissionDeniedError) as exc_info:
            doc.owner_id = "bob"  # Attempt to change owner
            await session.commit()
            assert exc_info.value.status_code == 403
                
        # # 2. Try update via statement
        with pytest.raises(PermissionDeniedError) as exc_info:
            stmt = (
                update(SecureDocument)
                .where(SecureDocument.id == doc.id)
                .values(owner_id="bob")
            )
            await session.execute(stmt)
            await session.commit()
        assert exc_info.value.status_code == 403
            
        # # 3. Try bulk update
        with pytest.raises(PermissionDeniedError) as exc_info:
            stmt = (
                update(SecureDocument)
                .where(SecureDocument.org_id == "org1")
                .values(owner_id="bob")
            )
            await session.execute(stmt)
            await session.commit()
        assert exc_info.value.status_code == 403
            
        # Verify the field wasn't changed
        await session.refresh(doc)
        assert doc.owner_id == "alice"  # Should still be the original owner
        
        # Verify normal updates still work
        doc.content = "Updated content"
        await session.commit()
        assert doc.content == "Updated content"
        assert doc.owner_id == "alice"  # System-managed field still unchanged

@pytest.mark.asyncio
async def test_bulk_update_permission_checks(cape):
    """Test that bulk updates properly check permissions for all affected objects"""
    # First create test documents with different owners
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        docs_alice = [
            SecureDocument(title=f"Alice Doc {i}", content="test", org_id="org1")
            for i in range(2)
        ]
        session.add_all(docs_alice)
        await session.commit()

    async with cape.get_session(AuthContext(subject="bob", context={"org_id": "org1"})) as session:
        docs_bob = [
            SecureDocument(title=f"Bob Doc {i}", content="test", org_id="org1")
            for i in range(2)
        ]
        session.add_all(docs_bob)
        await session.commit()

    # Test 1: Update only owned documents (should succeed)
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        stmt = (
            update(SecureDocument)
            .where(SecureDocument.title.like("Alice Doc%"))
            .values(content="Updated by owner")
        )
        await session.execute(stmt)
        await session.commit()

        # Verify update succeeded for owned docs
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title.like("Alice Doc%"))
        )
        docs = results.scalars().all()
        assert all(doc.content == "Updated by owner" for doc in docs)

    # Test 2: Attempt to update mix of owned and unowned documents (should fail)
    async with cape.get_session(AuthContext(subject="alice", context={"org_id": "org1"})) as session:
        stmt = (
            update(SecureDocument)
            .where(SecureDocument.org_id == "org1")  # Matches both Alice's and Bob's docs
            .values(content="Unauthorized update")
        )
        await session.execute(stmt)
        await session.commit()

        # Verify no documents were updated
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.org_id == "org1")
        )
        docs = results.scalars().all()
        for doc in docs:
            if doc.owner_id == "alice":
                assert doc.content == "Unauthorized update"  # Alice's docs should be updated
            else:
                assert doc.content in ("test", "Content 2")   # Bob's docs should remain unchanged

@pytest.mark.asyncio
async def test_privileged_session_bypass_rls(cape):
    """Test that privileged session bypasses RLS checks"""
    # First create test documents with different owners
    async with cape.get_session(AuthContext(subject="dave")) as session:
        doc_alice = SecureDocument(title="Dave Doc", content="test", org_id="org1")
        session.add(doc_alice)
        await session.commit()

    async with cape.get_session(AuthContext(subject="elaine")) as session:
        doc_bob = SecureDocument(title="Elaine Doc", content="test", org_id="org2")
        session.add(doc_bob)
        await session.commit()

    # Test regular session (should only see owned documents)
    async with cape.get_session(AuthContext(subject="dave")) as session:
        results = await session.execute(select(SecureDocument))
        docs = results.scalars().all()
        assert len(docs) == 1  # Should only see Alice's doc
        assert docs[0].title == "Dave Doc"

    # Test privileged session (should see all documents)
    async with cape.get_privileged_session() as session:
        results = await session.execute(select(SecureDocument))
        docs = results.scalars().all()
        assert len(docs) >= 2  # Should see all docs
        titles = {doc.title for doc in docs}
        assert "Dave Doc" in titles
        assert "Elaine Doc" in titles

@pytest.mark.asyncio
async def test_privileged_session_write_operations(cape):
    """Test that privileged session can perform write operations on any document"""
    # Create test documents
    async with cape.get_session(AuthContext(subject="alice")) as session:
        doc = SecureDocument(title="Test Doc", content="Original", org_id="org1")
        session.add(doc)
        await session.commit()
        doc_id = doc.id

    # Test update with privileged session
    async with cape.get_privileged_session() as session:
        doc = await session.get(SecureDocument, doc_id)
        doc.content = "Updated by privileged session"
        doc.owner_id = "system"  # Can modify system-managed fields
        await session.commit()

    # Verify changes
    async with cape.get_privileged_session() as session:
        doc = await session.get(SecureDocument, doc_id)
        assert doc.content == "Updated by privileged session"
        assert doc.owner_id == "system"

@pytest.mark.asyncio
async def test_privileged_session_bulk_operations(cape):
    """Test that privileged session can perform bulk operations"""
    # Create test data
    async with cape.get_privileged_session() as session:
        # Bulk insert
        docs = [
            SecureDocument(title=f"Bulk Doc {i}", content="test", org_id=f"org{i}", owner_id="system")
            for i in range(3)
        ]
        session.add_all(docs)
        await session.commit()

        # Bulk update
        stmt = (
            update(SecureDocument)
            .where(SecureDocument.title.like("Bulk Doc%"))
            .values(content="Bulk updated")
        )
        await session.execute(stmt)
        await session.commit()

        # Verify updates
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title.like("Bulk Doc%"))
        )
        updated_docs = results.scalars().all()
        assert len(updated_docs) == 3
        assert all(doc.content == "Bulk updated" for doc in updated_docs)

        # Bulk delete
        stmt = delete(SecureDocument).where(SecureDocument.title.like("Bulk Doc%"))
        await session.execute(stmt)
        await session.commit()

        # Verify deletion
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title.like("Bulk Doc%"))
        )
        remaining_docs = results.scalars().all()
        assert len(remaining_docs) == 0

@pytest.mark.asyncio
async def test_privileged_session_context_isolation(cape):
    """Test that privileged session context doesn't affect regular sessions"""
    # Create test document with privileged session
    async with cape.get_privileged_session() as session:
        doc = SecureDocument(title="System Doc", content="test", org_id="org1", owner_id="system")
        session.add(doc)
        await session.commit()

    # Verify regular session still has RLS enforced
    async with cape.get_session(AuthContext(subject="alice")) as session:
        results = await session.execute(
            select(SecureDocument)
            .where(SecureDocument.title == "System Doc")
        )
        docs = results.scalars().all()
        assert len(docs) == 0  # Regular session shouldn't see system-owned doc

    # Verify privileged session can still see everything
    async with cape.get_privileged_session() as session:
        results = await session.execute(select(SecureDocument))
        docs = results.scalars().all()
        assert any(doc.title == "System Doc" for doc in docs)
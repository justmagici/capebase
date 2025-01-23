from typing import Optional

import pytest
from sqlmodel import Field, Session, SQLModel, create_engine, select

from cape.core.auth.access_control import AccessControl
from cape.core.auth.row_level_security import RLSConfig, RowLevelSecurity


# Test Models
class SecureDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    content: str
    owner_id: str
    org_id: str


@pytest.fixture
def engine():
    """Create in-memory database for testing"""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def session(engine):
    """Create database session"""
    with Session(engine) as session:
        yield session


@pytest.fixture
def rls(session):
    """Create RLS instance with test policies"""
    ac = AccessControl()
    rls = RowLevelSecurity(access_control=ac)

    # Add test policies
    rls.register_model(
        RLSConfig(model=SecureDocument, action="read", role="*", owner_field="owner_id")
    )

    rls.register_model(
        RLSConfig(
            model=SecureDocument, action="write", role="*", owner_field="owner_id"
        )
    )

    rls.register_model(
        RLSConfig(
            model=SecureDocument,
            action="read",
            role="*",
            context_fields=["org_id"],
        )
    )

    return rls


@pytest.fixture
def sample_data(session):
    """Create sample documents"""
    docs = [
        SecureDocument(
            title="Doc 1", content="Content 1", owner_id="alice", org_id="org1"
        ),
        SecureDocument(
            title="Doc 2", content="Content 2", owner_id="bob", org_id="org1"
        ),
        SecureDocument(
            title="Doc 3", content="Content 3", owner_id="charlie", org_id="org2"
        ),
        SecureDocument(
            title="Doc 4", content="Content 4", owner_id="bob", org_id="org2"
        ),
    ]
    for doc in docs:
        session.add(doc)
    session.commit()


def test_filter_query_owner_access(session, rls, sample_data):
    """Test that users can access their own documents"""
    query = select(SecureDocument)
    filtered_query = rls.filter_query(
        query=query, subject="bob", action="read", context={}
    )

    results = session.exec(filtered_query).all()
    assert len(results) == 2
    assert all(doc.owner_id == "bob" for doc in results)


def test_filter_query_org_access(session, rls, sample_data):
    """Test that users can access documents in their org"""
    query = select(SecureDocument)
    filtered_query = rls.filter_query(
        query=query, subject="alice", action="read", context={"org_id": "org1"}
    )

    results = session.exec(filtered_query).all()
    assert len(results) == 2  # Should see all org1 docs
    assert all(doc.org_id == "org1" for doc in results)


def test_filter_query_no_access(session, rls, sample_data):
    """Test that users cannot access documents they shouldn't"""
    query = select(SecureDocument)
    filtered_query = rls.filter_query(
        query=query,
        subject="dave",  # User with no ownership or org access
        action="read",
        context={"org_id": "org3"},  # Non-existent org
    )

    results = session.exec(filtered_query).all()
    assert len(results) == 0


def test_filter_query_combined_access(session, rls, sample_data):
    """Test combined access through ownership and org membership"""
    query = select(SecureDocument)
    filtered_query = rls.filter_query(
        query=query, subject="bob", action="read", context={"org_id": "org2"}
    )

    results = session.exec(filtered_query).all()
    assert len(results) == 3  # Should see own doc and org2 docs
    titles = {doc.title for doc in results}
    assert titles == {"Doc 2", "Doc 3", "Doc 4"}


def test_filter_query_update(session, rls, sample_data):
    """Test filtering for update queries"""
    from sqlalchemy import update

    query = update(SecureDocument).values(content="Updated")

    filtered_query = rls.filter_query(
        query=query, subject="bob", action="write", context={"org_id": "org1"}
    )

    session.execute(filtered_query)
    session.commit()

    # Verify updates
    results = session.exec(select(SecureDocument)).all()
    updated = [doc for doc in results if doc.content == "Updated"]
    assert len(updated) == 2
    assert all(doc.owner_id == "bob" for doc in updated)


def test_filter_query_delete(session, rls, sample_data):
    """Test filtering for delete queries"""
    from sqlalchemy import delete

    query = delete(SecureDocument).where(SecureDocument.org_id == "org1")

    rls.register_model(
        RLSConfig(
            model=SecureDocument,
            action="delete",
            role="*",
            owner_field="owner_id",
        )
    )

    filtered_query = rls.filter_query(
        query=query, subject="bob", action="delete", context={"org_id": "org1"}
    )

    session.execute(filtered_query)
    session.commit()

    # Verify deletes
    results = session.exec(select(SecureDocument)).all()
    assert len(results) == 3  # Should have deleted org1 doc where bob is owner
    assert not any(doc.owner_id == "bob" and doc.org_id == "org1" for doc in results)

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from capebase.database import AsyncDatabaseManager
from sqlalchemy import text

@pytest_asyncio.fixture
async def db_manager():
    """Create a test database manager using SQLite memory database"""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    yield manager
    await manager.close()

@pytest.mark.asyncio
class TestAsyncDatabaseManager:
    async def test_init(self, db_manager):
        """Test database manager initialization"""
        assert db_manager.engine is not None
        assert db_manager._async_sessionmaker is not None

    async def test_connect_context(self, db_manager):
        """Test connection context manager"""
        async with db_manager.connect() as conn:
            assert isinstance(conn, AsyncConnection)
            # Test we can execute a simple query using SQLAlchemy text()
            result = await conn.execute(text("SELECT 1"))
            assert result.scalar_one() == 1

    async def test_session_context(self, db_manager):
        """Test session context manager"""
        async with db_manager.session() as session:
            assert isinstance(session, AsyncSession)
            # Test we can execute a simple query using SQLAlchemy text()
            result = await session.execute(text("SELECT 1"))
            assert result.scalar_one() == 1

    async def test_connect_rollback_on_error(self, db_manager):
        """Test connection rolls back on error"""
        with pytest.raises(Exception, match="Test error"):
            async with db_manager.connect() as conn:
                # In a transaction block, changes are rolled back automatically on error
                await conn.execute(text("CREATE TABLE test_table (id INTEGER PRIMARY KEY)"))
                await conn.execute(text("INSERT INTO test_table (id) VALUES (1)"))
                raise Exception("Test error")
            
        async with db_manager.connect() as conn:
            result = await conn.execute(text("SELECT * FROM test_table"))
            assert result.first() is None
        
    async def test_session_rollback_on_error(self, db_manager):
        """Test session rolls back on error"""
        with pytest.raises(Exception, match="Test error"):
            async with db_manager.session() as session:
                # Perform an insert instead of CREATE TABLE
                await session.execute(text("CREATE TABLE test_table (id INTEGER PRIMARY KEY)"))
                await session.execute(text("INSERT INTO test_table (id) VALUES (1)"))
                raise Exception("Test error")
            
        async with db_manager.session() as session:
            result = await session.execute(text("SELECT * FROM test_table"))
            assert result.first() is None

    async def test_close(self, db_manager):
        """Test database manager close"""
        # Create a new manager specifically for this test
        test_manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await test_manager.close()
        assert test_manager.engine is None
        assert test_manager._async_sessionmaker is None

        # Verify operations fail after close
        with pytest.raises(RuntimeError, match="AsyncDatabaseManager is not initialized"):
            async with test_manager.session():
                pass

    async def test_multiple_sessions(self, db_manager):
        """Test multiple concurrent sessions"""
        async with db_manager.session() as session1:
            async with db_manager.session() as session2:
                assert session1 is not session2
                # Test both sessions can execute queries
                result1 = await session1.execute(text("SELECT 1"))
                result2 = await session2.execute(text("SELECT 2"))
                assert result1.scalar_one() == 1
                assert result2.scalar_one() == 2 
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class AsyncDatabaseManager:
    def __init__(self, db_path: str):
        self.engine = create_async_engine(db_path)
        self._async_sessionmaker = async_sessionmaker(
            bind=self.engine, expire_on_commit=False, autocommit=False
        )

    async def close(self):
        if self.engine is None:
            raise RuntimeError("AsyncDatabaseManager is not initialized")
        await self.engine.dispose()

        self.engine = None
        self._async_sessionmaker = None

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[AsyncConnection]:
        if self.engine is None:
            raise Exception("DatabaseSessionManager is not initialized")

        async with self.engine.begin() as connection:
            try:
                yield connection
            except Exception:
                await connection.rollback()
                raise

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._async_sessionmaker is None:
            raise RuntimeError("AsyncDatabaseManager is not initialized")

        session = self._async_sessionmaker()
        try:
            yield session
        except Exception:
            print("Rolling back session")
            await session.rollback()
            raise
        finally:
            await session.close()

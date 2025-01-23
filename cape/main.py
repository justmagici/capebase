# Utils
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    List,
    Tuple,
    Optional,
    Type,
    TypeVar,
)

from fastapi import FastAPI
from sqlalchemy import Insert, event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session
from sqlmodel import SQLModel

from cape.api import APIGenerator
from cape.auth.access_control import AccessControl
from cape.auth.row_level_security import RLSConfig, RowLevelSecurity
from cape.notification import NotificationEngine
from cape.database import AsyncDatabaseManager
from cape.models import ModelChange, TableEvent
from cape.utils import get_original_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

ModelVar = TypeVar("ModelVar", bound=SQLModel)

DEFAULT_TIMEOUT = 5


@dataclass
class Context:
    subject: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


@dataclass
class Cape:
    app: FastAPI
    db_path: str
    routers: dict[str, APIGenerator] = field(default_factory=defaultdict)
    notification_engine: NotificationEngine = field(default_factory=NotificationEngine)
    model_registry: dict[str, Type[SQLModel]] = field(default_factory=defaultdict)

    db_session: AsyncDatabaseManager = field(init=False)
    row_level_security: RowLevelSecurity = field(init=False)

    _tasks: List[asyncio.Task] = field(default_factory=list)
    _pending_subscriptions: List[Tuple[Type[SQLModel], List[Callable[[ModelChange], None]]]] = field(default_factory=list)

    def __post_init__(self):
        self.db_session = AsyncDatabaseManager(self.db_path)
        self.row_level_security = RowLevelSecurity(AccessControl())

        self._setup_lifespan()

    def _setup_lifespan(self):
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            try:
                logger.info("Starting up Cape")
                await self._initialize_database_schema()
                self._setup_row_level_security()
                self._setup_crud_routes()
                self._setup_publish_handlers()
                self._setup_subscriptions()

                yield
            finally:
                logger.info("Shutting down Cape")
                if self._tasks:
                    try:
                        # Wait for all tasks with timeout
                        await asyncio.wait_for(
                            asyncio.gather(*self._tasks), timeout=DEFAULT_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        logger.debug("Canelling long running tasks during shutdown")
                    except Exception as e:
                        logger.error(f"Error during task cleanup: {e}")
                    finally:
                        # Ensure all tasks are cancelled and cleaned up
                        for task in self._tasks:
                            if not task.done():
                                task.cancel()
                            try:
                                await task  # Handle any cancellation exceptions
                            except (asyncio.CancelledError, Exception) as e:
                                logger.debug(f"Task cleanup: {e}")
                        self._tasks = []
                logger.info("Cape shutdown complete")

        # Attach lifespan context to app
        self.app.router.lifespan_context = lifespan

    async def _initialize_database_schema(self):
        """Initialize database schema using async session."""
        async with self.db_session.connect() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    def _setup_row_level_security(self):
        """Set up query filtering for row-level security"""

        # Create a local reference to row_level_security to use in closure
        rls = self.row_level_security

        @event.listens_for(Session, "do_orm_execute")
        def _do_orm_execute(orm_execute_state: ORMExecuteState):
            """Listen for query execution and apply RLS filtering"""

            subject = orm_execute_state.session.info["subject"]
            context = orm_execute_state.session.info["context"]

            if orm_execute_state.is_insert and isinstance(
                orm_execute_state.statement, Insert
            ):
                if not rls.can_create(
                    subject=subject,
                    subject_context=context,
                    statement=orm_execute_state.statement,
                ):
                    raise PermissionError(
                        "User does not have permission to create this object"
                    )

            if orm_execute_state.is_select:
                action = "read"
            elif orm_execute_state.is_update:
                action = "update"
            elif orm_execute_state.is_delete:
                action = "delete"
            else:
                return

            orm_execute_state.statement = rls.filter_query(
                orm_execute_state.statement, subject, action, context
            )

        @event.listens_for(Session, "before_flush")
        def _before_flush(session, flush_context, instances):
            """Check permissions before any changes are committed to the database"""
            context = session.info["context"]
            subject = session.info["subject"]

            if subject is None or context is None:
                # TODO: Update error system to support invalid context
                raise PermissionError("No context provided")

            for obj in session.identity_map.values():
                # Need to retrieve the object from DB first to make sure user can read.
                if isinstance(obj, SQLModel):
                    original = get_original_state(obj)

                    if not rls.can_read(
                        subject=subject,
                        subject_context=context,
                        obj=original,
                    ):
                        raise PermissionError(
                            "User does not have permission to read this object"
                        )

            # Check permissions for modified objects
            for obj in session.dirty:
                if isinstance(obj, SQLModel):
                    original = get_original_state(obj)

                    if not rls.can_update(
                        subject=subject,
                        subject_context=context,
                        obj=original,
                    ):
                        raise PermissionError(
                            "User does not have permission to update this object"
                        )

            # Check permission for new objects
            for obj in session.new:
                if isinstance(obj, SQLModel):
                    if not rls.can_create(
                        subject=subject,
                        subject_context=context,
                        obj=obj,
                    ):
                        raise PermissionError(
                            "User does not have permission to create this object"
                        )

            # Check permission for deleted objects
            for obj in session.deleted:
                if isinstance(obj, SQLModel):
                    if not rls.can_delete(
                        subject=subject,
                        subject_context=context,
                        obj=obj,
                    ):
                        raise PermissionError(
                            "User does not have permission to delete this object"
                        )

    def _setup_publish_handlers(self):
        for model in self.model_registry.values():
            event.listen(
                model, "after_insert", partial(self._notify_change, event_type="INSERT")
            )
            event.listen(
                model, "after_update", partial(self._notify_change, event_type="UPDATE")
            )
            event.listen(
                model, "after_delete", partial(self._notify_change, event_type="DELETE")
            )

    def _add_task(self, coro) -> asyncio.Task:
        """Helper method to add and manage async tasks.

        Args:
            coro: A coroutine to be scheduled as a task
        Returns:
            The created task
        """

        def handle_done(task):
            self._tasks.remove(task)
            if task.exception():
                logger.error(f"Task failed: {task.exception()}")

        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        self._tasks.append(task)
        task.add_done_callback(handle_done)
        return task

    def _notify_change(
        self, mapping, connection, target: SQLModel, event_type: TableEvent
    ):
        """Helper method to handle model change notifications"""
        change = ModelChange(
            table=target.__tablename__,
            event=event_type,
            payload=target,
            timestamp=datetime.now(),
        )
        self._add_task(self.notification_engine.notify(change))

    def _setup_crud_routes(self):
        for model_name, model in self.model_registry.items():
            self.routers[model_name] = APIGenerator[model](
                schema=model,
                get_session=self.get_db_dependency,
                notification_engine=self.notification_engine,
                row_level_security=self.row_level_security,
            )
            self.app.include_router(self.routers[model_name])

    def _setup_subscriptions(self):
        """Set up all pending model change subscriptions."""
        for model, callbacks in self._pending_subscriptions:
            for callback in callbacks:

                async def subscription_task():
                    async for change in self.notification_engine.get_channel(
                        model
                    ).subscribe():
                        await callback(change)

                self._add_task(subscription_task())

    def permission_required(
        self,
        cls: Optional[Type[SQLModel]] = None,
        *,
        role: Optional[str] = None,
        actions: List[str],
        owner_field: Optional[str] = None,
        context_fields: List[str] = [],
    ):
        """
        Decorator to set up row-level security for SQLModel classes.
        """

        def decorator(cls: Type[SQLModel]) -> Type[SQLModel]:
            # Register each action separately with RLS
            for action in actions:
                config = RLSConfig(
                    model=cls,
                    action=action,
                    role=role,
                    owner_field=owner_field,
                    context_fields=context_fields,
                )
                self.row_level_security.register_model(config)
            return cls

        if cls is not None:
            return decorator(cls)
        return decorator

    def publish(self, cls: Type[SQLModel]) -> Type[SQLModel]:
        if not issubclass(cls, SQLModel):
            raise TypeError(
                f"@publish can only be applied to SQLModel classes, not {type(cls).__name__}."
            )

        self.model_registry[cls.__name__] = cls

        return cls

    @asynccontextmanager
    async def get_session(
        self, subject: Optional[str] = None, context: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[AsyncSession]:
        """Get a database session with security context."""
        async with self.db_session.session() as session:
            session.info["subject"] = subject
            session.info["context"] = context
            yield session

    async def get_db_dependency(
        self, subject: Optional[str] = None, context: Optional[Dict[str, Any]] = None
    ) -> AsyncGenerator[AsyncSession, None]:
        async with self.db_session.session() as session:
            session.info["subject"] = subject
            session.info["context"] = context
            yield session

    def subscribe(self, model: Type[SQLModel]):
        """Decorator to subscribe to model changes."""

        # Check that model belongs to SQLModel
        if not issubclass(model, SQLModel):
            raise TypeError(f"Model {model.__name__} is not a SQLModel")

        def decorator(callable: Callable[[ModelChange], None]):
            # Store subscription info for setup during lifespan
            self._pending_subscriptions.append((model, [callable]))
            return callable

        return decorator

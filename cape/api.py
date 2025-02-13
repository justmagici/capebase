import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    AsyncContextManager,
    Awaitable,
    Callable,
    Coroutine,
    Generic,
    List,
    Optional,
    Sequence,
    Type,
    TypeVar,
)

from fastapi import APIRouter, Depends, HTTPException, params
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select
from sse_starlette import EventSourceResponse

from cape.auth.row_level_security import RowLevelSecurity
from cape.notification import NotificationEngine

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=SQLModel)


# Inspired by https://github.com/awtkns/fastapi-crudrouter/blob/master/fastapi_crudrouter/core/_base.py


@dataclass(init=False)
class APIGenerator(Generic[T], APIRouter):
    schema: Type[T]
    create_schema: Type[T]
    update_schema: Type[T]
    get_session: Callable[..., AsyncContextManager[AsyncSession]]
    notification_engine: NotificationEngine
    row_level_security: RowLevelSecurity
    _base_path: str = "/"
    _primary_key: str = "id"

    def __init__(
        self,
        schema: Type[T],
        get_session: Callable[..., AsyncContextManager[AsyncSession]],
        notification_engine: NotificationEngine,
        row_level_security: RowLevelSecurity,
        *,
        create_schema: Optional[Type[T]] = None,
        update_schema: Optional[Type[T]] = None,
        prefix: Optional[str] = None,
        tags: Optional[List[str | Enum]] = None,
        **kwargs: Any,
    ):
        self.schema = schema
        self.create_schema = create_schema or schema
        self.update_schema = update_schema or schema
        self.get_session = get_session
        self.notification_engine = notification_engine
        self.row_level_security = row_level_security

        # TODO: Add pagination support
        prefix = str(prefix or self.schema.__name__).lower()
        prefix = self._base_path + prefix.strip("/")
        tags = tags or [prefix.strip("/").capitalize()]

        # Need to pass in additional kwargs
        super().__init__(prefix=prefix, tags=tags, **kwargs)

        self._add_api_route(
            "",
            endpoint=self._list(),
            methods=["GET"],
            response_model=Optional[List[self.schema]],  # type: ignore
            summary=f"List {self.schema.__name__}",
            description=f"List all items of type {self.schema.__name__}",
        )

        self._add_api_route(
            "",
            methods=["POST"],
            endpoint=self._create(),
            response_model=self.schema,
            summary=f"Create {self.schema.__name__}",
            description=f"Create an item of type {self.schema.__name__}",
        )

        self.add_api_route(
            "/subscribe",
            methods=["GET"],
            endpoint=self._subscribe(),
            summary=f"Subscribe to {self.schema.__name__} changes",
            description=f"Subscribe to changes of type {self.schema.__name__}",
        )

        self._add_api_route(
            "/{item_id}",
            methods=["GET"],
            endpoint=self._get(),
            response_model=self.schema,
            summary=f"Get {self.schema.__name__}",
            description=f"Get an item of type {self.schema.__name__}",
        )

        self.add_api_route(
            "/{item_id}",
            methods=["PUT"],
            endpoint=self._update(),
            response_model=self.schema,
            summary=f"Update {self.schema.__name__}",
            description=f"Update an item of type {self.schema.__name__}",
        )

        self.add_api_route(
            "/{item_id}",
            methods=["DELETE"],
            endpoint=self._delete(),
            summary=f"Delete {self.schema.__name__}",
            description=f"Delete an item of type {self.schema.__name__}",
        )

    def _add_api_route(
        self,
        path: str,
        *,
        methods: List[str],
        endpoint: Callable[..., Any],
        response_model: Optional[Any] = None,
        dependencies: Optional[Sequence[params.Depends]] = None,
        error_responses: Optional[List[HTTPException]] = None,
        **kwargs: Any,
    ) -> None:
        responses: Any = (
            {err.status_code: {"detail": err.detail} for err in error_responses}
            if error_responses
            else None
        )
        super().add_api_route(
            path,
            endpoint,
            responses=responses,
            dependencies=dependencies,
            methods=methods,
            response_model=response_model,
            **kwargs,
        )

    def _list(self) -> Callable[..., Awaitable[Sequence[T]]]:
        async def route(
            session: AsyncSession = Depends(self.get_session),
        ) -> Sequence[T]:
            results = await session.execute(select(self.schema))
            return results.scalars().all()

        return route

    def _create(self) -> Callable[..., Awaitable[T]]:
        async def route(
            item: self.create_schema,  # type: ignore
            session: AsyncSession = Depends(self.get_session),
        ) -> T:
            db_item = self.schema(**item.model_dump())
            session.add(db_item)
            await session.commit()
            await session.refresh(db_item)
            return db_item

        return route

    def _get(self) -> Callable[..., Awaitable[T]]:
        async def route(
            item_id: int, session: AsyncSession = Depends(self.get_session)
        ) -> T:
            item = await session.get(self.schema, item_id)
            if not item:
                raise HTTPException(
                    status_code=404, detail=f"{self.schema.__name__} not found"
                )
            return item

        return route

    def _update(self) -> Callable[..., Awaitable[T]]:
        async def route(
            item_id: int,
            updated_item: self.update_schema,  # type: ignore
            session: AsyncSession = Depends(self.get_session),
        ) -> T:
            item = await session.get(self.schema, item_id)
            if not item:
                raise HTTPException(
                    status_code=404, detail=f"{self.schema.__name__} not found"
                )
            for key, value in updated_item.model_dump(exclude_unset=True).items():
                setattr(item, key, value)
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

        return route

    def _delete(self) -> Callable[..., Awaitable[dict]]:
        async def route(
            item_id: int, session: AsyncSession = Depends(self.get_session)
        ) -> dict:
            item = await session.get(self.schema, item_id)
            if not item:
                raise HTTPException(
                    status_code=404, detail=f"{self.schema.__name__} not found"
                )
            await session.delete(item)
            await session.commit()
            return {"message": f"{self.schema.__name__} with ID {item_id} deleted"}

        return route

    # Using EventSourceResponse instead of StreamingResponse to gracefully handle exit signals from server
    def _subscribe(self) -> Callable[..., Coroutine[Any, Any, EventSourceResponse]]:
        async def route(
            session: AsyncSession = Depends(self.get_session),
        ) -> EventSourceResponse:
            channel = self.notification_engine.get_channel(self.schema)

            async def event_generator():
                try:
                    async for change in channel.subscribe():
                        auth_context = session.info["auth_context"]

                        if not self.row_level_security.can_read(
                            auth_context, change.payload
                        ):
                            logger.warning(f"User does not have permission to read {change.payload}")
                            continue

                        yield dict(data=change.to_json())
                except asyncio.CancelledError:
                    logger.warning("Subscription cancelled")
                    raise
                except Exception as e:
                    logger.error(f"Error in subscription for {self.schema.__name__}: {e}")

            return EventSourceResponse(event_generator(), send_timeout=5)

        return route


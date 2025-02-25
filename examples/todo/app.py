from datetime import datetime
from typing import Optional, Sequence

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Field, SQLModel, select

from capebase.main import CapeBase
from capebase.models import FROM_AUTH_ID, AuthContext
from examples.todo.db import User, create_db_and_tables
from examples.todo.scheme import UserCreate, UserRead
from examples.todo.users import auth_backend, current_active_user, fastapi_users


class Todo(SQLModel, table=True):
    """Model for todo items"""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: FROM_AUTH_ID = Field(index=True)
    task: str
    is_complete: bool = Field(default=False)
    inserted_at: datetime = Field(default_factory=datetime.utcnow)


class CreateTodo(SQLModel):
    task: str
    is_complete: bool = Field(default=False)


class UpdateTodo(SQLModel):
    is_complete: bool = Field(default=False)


# current_active_user follows example from fastapi-users
# https://fastapi-users.github.io/fastapi-users/10.1/configuration/full-example/
async def auth_provider(
    request: Request, user: Optional[User] = Depends(current_active_user)
) -> AuthContext:
    if not user:
        return AuthContext()

    return AuthContext(id=str(user.id), role=user.role, context={})


app = FastAPI(title="Real-time Todo App")
cape = CapeBase(
    app=app,
    db_path="sqlite+aiosqlite:///todos.db",
    auth_provider=auth_provider,
)

# Register model with Cape
cape.publish(
    Todo, create_schema=CreateTodo, update_schema=UpdateTodo
)  # Auto generated list, get, create, delete, subscribe routes
# Or specify certain routes
# cape.publish(Todo, routes=["list", "get", "create"])

# Set permissions
cape.permission_required(
    Todo, role="admin", actions=["read", "create", "update", "delete"]
)

cape.permission_required(
    Todo, role="*", actions=["create"]
)  # All login users can create
cape.permission_required(
    Todo, role="*", actions=["read", "update", "delete"], owner_field="user_id"
)  # All login users can update and delete their own todos


# Define additional routes by retrieving the session from CapeBase
@app.get("/todo/filter")
async def filter(
    completed: bool, session=Depends(cape.get_db_dependency())
) -> Sequence[Todo]:
    """Filter todos by completion status"""
    result = await session.execute(select(Todo).where(Todo.is_complete == completed))
    return result.scalars().all()


# Data access policy will be applied even if the session is not retrieved from CapeBase

# from sqlalchemy import create_engine
# from sqlalchemy.orm import Session
# engine = create_engine("sqlite:///todos.db")

# @app.get("/todo/filter_wont_work")
# def filter_wont_work(completed: bool) -> Sequence[Todo]:
#     """Filter todos by completion status"""
#     with Session(engine) as session:
#         result = session.execute(
#             select(Todo).where(Todo.is_complete == completed)
#         )
#         return result.scalars().all() # Should be nil since read is subjected to access policy


# Subscribe to changes for server-side operations
@cape.subscribe(Todo)
async def on_todo_change(change):
    """Handle todo changes"""
    if change.event == "INSERT":
        print(f"New todo created: {change.payload.task}")
    elif change.event == "UPDATE":
        print(
            f"Todo updated: {change.payload.task} (Complete: {change.payload.is_complete})"
        )
    elif change.event == "DELETE":
        print(f"Todo deleted: {change.payload.task}")

    # Use get_privileged_session (i.e. service role) for unrestricted access
    async with cape.get_privileged_session() as session:
        result = await session.execute(
            select(Todo).where(Todo.user_id == change.payload.user_id)
        )
        print(f"User has created {len(result.scalars().all())} todos")


# Example auth routes with FastAPI Users (from fastapi-users examples)
app.include_router(
    fastapi_users.get_auth_router(auth_backend), prefix="/auth", tags=["auth"]
)

app.include_router(
    fastapi_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)
app.include_router(
    fastapi_users.get_verify_router(UserRead),
    prefix="/auth",
    tags=["auth"],
)


@app.post("/auth/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(key="todo_auth")
    return response


if __name__ == "__main__":
    import asyncio

    import uvicorn

    asyncio.run(create_db_and_tables())

    uvicorn.run(app, host="0.0.0.0", port=8080)

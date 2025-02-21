<p align="center">
  <img src="docs/assets/logo.png" alt="CapeBase Logo">
</p>

<p align="center">
  Enhance your Python backend with auto-generated APIs, real-time features, and granular permissions
</p>

---

[![PyPI version](https://badge.fury.io/py/capebase.svg)](https://badge.fury.io/py/capebase)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

CapeBase is a Python library designed for building real-time backends with single-instance databases, ideal for small to medium projects. It provides auto-generated REST APIs, real-time updates, and built-in data access controls. Database-agnostic by design, it supports SQLite, PostgreSQL, and MySQL, while seamlessly integrating with FastAPI and SQLModel for straightforward development.

## ✨ Features

- 🔌 **FastAPI  Integration**: First-class support for FastAPI.
- 🔄 **Database Agnostic**: Works seamlessly with SQLite, PostgreSQL, and MySQL
- 🚀 **Automatic API Generation**: Instantly create RESTful APIs from your SQLModels.
- ⚡ **Real-time Database**: Subscribe to database changes in real-time
- 🔐 **Authentication**: Easily plug in your own solution or use the built-in system (WIP)
- 🛡️ **Data Access Control**: Role-based and resource-level permissions with dynamic filtering
- 📊 **Observability**: API endpoint monitoring with Prometheus and comprehensive audit trail (WIP)

## 🚀 Quick Start

### Installation

```bash
pip install capebase
```

### Basic Usage

1. **Define your SQLModel**

```python
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field
from capebase import FROM_AUTH_ID

class Todo(SQLModel, table=True):
    """Model for todo items"""
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: FROM_AUTH_ID = Field(index=True)  # Automatically populated from auth context
    task: str
    is_complete: bool = Field(default=False)
    inserted_at: datetime = Field(default_factory=datetime.utcnow)
```

2. **Set up your FastAPI app with CapeBase**

```python
from fastapi import FastAPI, Depends
from capebase import CapeBase

app = FastAPI(title="Real-time Todo App")
cape = CapeBase(
    app=app,
    db_path="sqlite+aiosqlite:///todos.db",
    auth_provider=auth_provider,  # Your auth provider
)

# Register model with Cape - automatically generates CRUD and subscription routes
cape.publish(Todo)  # Basic usage with default schemas

# Or with optional custom schemas for more control


cape.publish(
    Todo,
    create_schema=CreateTodo,  # Optional: Custom schema for creation
    update_schema=UpdateTodo   # Optional: Custom schema for updates
)

# Set up permissions
cape.permission_required(
    Todo, role="*", actions=["read"],
)  # Allow anyone to read todos (public read access)

cape.permission_required(
    Todo, owner_field="user_id", actions=["create", "update", "delete"]
)  # Users can only create/update/delete their own todos

# Example: Admin permissions (optional)
cape.permission_required(
    Todo, role="admin", actions=["read", "create", "update", "delete"]
)  # Admins have full access to all todos
```

3. **Define custom routes (optional)**

```python
from sqlalchemy import select
from typing import Sequence

@app.get("/todo/filter")
async def filter(
    completed: bool, 
    session=Depends(cape.get_db_dependency())
) -> Sequence[Todo]:
    """Filter todos by completion status"""
    result = await session.execute(
        select(Todo).where(Todo.is_complete == completed)
    )
    return result.scalars().all()
```

4. **Subscribe to real-time changes (optional)**

```python
@cape.subscribe(Todo)
async def on_todo_change(change):
    """Handle todo changes"""
    if change.event == "INSERT":
        print(f"New todo created: {change.payload.task}")
    elif change.event == "UPDATE":
        print(f"Todo updated: {change.payload.task}")
    elif change.event == "DELETE":
        print(f"Todo deleted: {change.payload.task}")

    # Use privileged session for unrestricted access
    async with cape.get_privileged_session() as session:
        result = await session.execute(
            select(Todo).where(Todo.user_id == change.payload.user_id)
        )
        print(f"User has created {len(result.scalars().all())} todos")
```

### Generated API Routes

The `cape.publish(Todo)` command automatically creates the following endpoints:

- `GET /todo` - List todos
- `GET /todo/{id}` - Get a specific todo
- `POST /todo` - Create a new todo
- `PATCH /todo/{id}` - Update a todo
- `DELETE /todo/{id}` - Delete a todo
- `GET /todo/subscribe` - Server-Sent Events (SSE) endpoint for real-time updates
- (WIP) `WS /todo/subscribe` - WebSocket endpoint for real-time updates

You can also specify which routes to generate:
```python
cape.publish(Todo, routes=["list", "get", "create"])
```

### Custom Schemas (Optional)
You can customize which fields are allowed in create/update operations:

```python
# Define custom schema for creation
class CreateTodo(SQLModel):
    task: str  # Required field
    is_complete: bool = Field(default=False)  # Optional with default

# Define custom schema for updates
class UpdateTodo(SQLModel):
    is_complete: bool = Field(default=False)  # Only allow updating completion status

# Register model with custom schemas
cape.publish(
    Todo,
    create_schema=CreateTodo,   # Control fields allowed during creation
    update_schema=UpdateTodo    # Control fields allowed during updates
)
```

This will generate:
- `POST /todo` - Uses `CreateTodo` schema
- `PATCH /todo/{id}` - Uses `UpdateTodo` schema
- Other endpoints use the base `Todo` model

### Authentication

#### Auth Context Provider

CapeBase uses an auth context provider to handle authentication and user context. You can implement your own provider based on your authentication needs. The provider should return an `AuthContext` object containing:

```python
from capebase.auth import AuthContext

async def get_auth_context(request: Request) -> AuthContext:
    # Your authentication logic here
    return AuthContext(
        id="user_id",        # Required: Unique identifier for the user
        role="user_role",    # Optional: Role for permission checks
        context={}           # Optional: Additional context data
    )

cape = CapeBase(
    app=app,
    db_path="sqlite+aiosqlite:///app.db",
    get_auth_context=get_auth_context
)
```

#### Example: Integration with FastAPI Users

Here's an example of implementing an auth context provider using [FastAPI Users](https://fastapi-users.github.io/fastapi-users/):

```python
from fastapi import Request, Depends
from fastapi_users import FastAPIUsers
from typing import Optional
from capebase.model import AuthContext

# Assuming you have FastAPI Users configured with your User model
fastapi_users = FastAPIUsers[User, int](
    get_user_manager,
    [auth_backend],
)

# Create auth context provider
async def get_auth_context(
    request: Request, 
    # You can use any FastAPI dependencies in your auth context provider
    user: Optional[User] = Depends(fastapi_users.current_user(active=True))
) -> AuthContext:
    if not user:
        return AuthContext()

    return AuthContext(
        id=str(user.id),
        role=user.role,
        context={}
    )

# Initialize CapeBase with the auth context
app = FastAPI()
cape = CapeBase(
    app=app,
    db_path="sqlite+aiosqlite:///app.db",
    get_auth_context=get_auth_context
)
```

This setup allows you to:
- Use FastAPI Users' authentication system
- Automatically populate user IDs in your models using `FROM_AUTH_ID`
- Control access based on user roles
- Access the authenticated user's context in your routes

### Telemetry and Audit Trail

#### Metrics

CapeBase can be easily integrated with Prometheus for API endpoint monitoring using `starlette-prometheus` or other similar libraries:

```python
from starlette_prometheus import PrometheusMiddleware, metrics

# Add Prometheus middleware to your FastAPI app
app.add_middleware(PrometheusMiddleware)
app.add_route("/metrics", metrics)
```

This provides HTTP-level metrics including:
- Request latency
- Request counts by endpoint
- Response status codes
- Active requests

Database monitoring features are currently in development, which will provide insights into:
- Query performance
- Connection pool status
- Database operations
- Error rates

#### Audit Trail (WIP)

### Important Note on Database Operations

> ⚠️ **Note**: Currently, raw SQL operations have limited functionality. We recommend using ORM-style operations for full feature support.

✅ **Recommended**:
```python
@app.patch("/todo/{todo_id}")
async def update_todo(
    todo_id: int,
    session=Depends(cape.get_db_dependency())
):
    todo = await session.get(Todo, todo_id)
    todo.is_complete = True  # Works well with Cape's event handlers
    await session.commit()
```

❌ **Not Recommended**:
```python
# Use ORM-style operations instead
await session.execute(
    update(Todo)
    .where(Todo.id == todo_id)
    .values(is_complete=True)
)
```



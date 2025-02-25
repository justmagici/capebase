from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin, models
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

import examples.todo.db as db

SECRET = "SECRET"  # In production, use a secure secret key from environment variables


class UserManager(IntegerIDMixin, BaseUserManager[db.User, int]):
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: db.User, request: Optional[Request] = None):
        print(f"User {user.id} has registered.")

    async def on_after_forgot_password(
        self, user: db.User, token: str, request: Optional[Request] = None
    ):
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(
        self, user: db.User, token: str, request: Optional[Request] = None
    ):
        print(f"Verification requested for user {user.id}. Verification token: {token}")


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(db.get_user_db)):
    yield UserManager(user_db)


# Cookie transport configuration
cookie_transport = CookieTransport(
    cookie_max_age=3600,  # 1 hour
    cookie_name="todo_auth",
    cookie_secure=False,  # Set to True in production with HTTPS
    cookie_httponly=True
)


def get_jwt_strategy() -> JWTStrategy[models.UP, models.ID]:
    return JWTStrategy(secret=SECRET, lifetime_seconds=3600)


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[db.User, int](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.security import hash_session_token
from app.auth.store import (
    AuthSessionRecord,
    AuthStore,
    PostgresAuthStore,
    UserAuthRecord,
)
from app.core.config import settings
from app.db import get_db


@dataclass(frozen=True)
class CurrentAuth:
    session: AuthSessionRecord

    @property
    def user(self) -> UserAuthRecord:
        return self.session.user


def get_auth_store(connection: Annotated[object, Depends(get_db)]) -> AuthStore:
    return PostgresAuthStore(connection)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not_authenticated",
    )


def get_current_auth(
    request: Request,
    store: Annotated[AuthStore, Depends(get_auth_store)],
) -> CurrentAuth:
    token = request.cookies.get(settings.auth_session_cookie_name)
    if not token:
        raise unauthorized()

    auth_session = store.get_user_by_session_hash(hash_session_token(token))
    if auth_session is None:
        raise unauthorized()

    now = datetime.now(UTC)
    if auth_session.session.expires_at <= now:
        raise unauthorized()

    store.touch_session(auth_session.session.id, now)
    return CurrentAuth(session=auth_session)


def get_current_user(
    auth: Annotated[CurrentAuth, Depends(get_current_auth)],
) -> UserAuthRecord:
    return auth.user


def require_roles(*role_ids: int) -> Callable[[UserAuthRecord], UserAuthRecord]:
    allowed_roles = set(role_ids)

    def dependency(
        user: Annotated[UserAuthRecord, Depends(get_current_user)],
    ) -> UserAuthRecord:
        if allowed_roles and allowed_roles.isdisjoint(role.id for role in user.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient_role",
            )
        return user

    return dependency

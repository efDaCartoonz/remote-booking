from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.auth.dependencies import CurrentAuth, get_auth_store, get_current_auth
from app.auth.schemas import (
    CurrentUserResponse,
    LoginRequest,
    LoginResponse,
    RoleResponse,
)
from app.auth.security import hash_session_token, new_session_token, verify_password
from app.auth.store import AuthStore, UserAuthRecord
from app.core.config import settings

AUDIT_ACTION_LOGIN = 3
AUDIT_ACTION_LOGOUT = 4

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    store: Annotated[AuthStore, Depends(get_auth_store)],
) -> LoginResponse:
    user = store.get_user_by_username(payload.username)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_credentials",
        )

    token = new_session_token()
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.auth_session_ttl_minutes)
    session = store.create_session(
        user_id=user.id,
        session_hash=hash_session_token(token),
        expires_at=expires_at,
    )
    store.log_auth_event(
        user_id=user.id,
        action_code=AUDIT_ACTION_LOGIN,
        session_id=session.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token)
    return LoginResponse(user=_serialize_user(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    auth: Annotated[CurrentAuth, Depends(get_current_auth)],
    store: Annotated[AuthStore, Depends(get_auth_store)],
) -> None:
    store.revoke_session(auth.session.session.id, datetime.now(UTC))
    store.log_auth_event(
        user_id=auth.user.id,
        action_code=AUDIT_ACTION_LOGOUT,
        session_id=auth.session.session.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _clear_session_cookie(response)


@router.get("/me", response_model=CurrentUserResponse)
def me(auth: Annotated[CurrentAuth, Depends(get_current_auth)]) -> CurrentUserResponse:
    return _serialize_user(auth.user)


def _serialize_user(user: UserAuthRecord) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        roles=[RoleResponse(id=role.id, name=role.name) for role in user.roles],
    )


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=token,
        max_age=settings.auth_session_ttl_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_session_cookie_name,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    candidate = request.client.host
    try:
        ip_address(candidate)
    except ValueError:
        return None
    return candidate

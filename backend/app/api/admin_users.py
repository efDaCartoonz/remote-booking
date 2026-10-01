from __future__ import annotations

from collections.abc import Generator
from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.admin.users import (
    AdminUserRepository,
    AdminUserRecord,
    InvalidRoleError,
    LastAdminDeactivationError,
    LastAdminRoleRemovalError,
    OmnideskStaffIdConflictError,
    PostgresAdminUserRepository,
    SelfDeactivationError,
    UserNotFoundError,
    UsernameConflictError,
)
from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import RoleId
from app.db import db_connection

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin"])

require_admin_role = require_roles(int(RoleId.ADMIN))


StaffId = Annotated[str, Field(pattern=r"^[0-9]{1,100}$")]


class RoleResponse(BaseModel):
    id: int
    name: str


class AdminUserResponse(BaseModel):
    id: int
    username: str
    full_name: str
    email: str | None = None
    phone: str | None = None
    omnidesk_staff_id: str | None = None
    is_active: bool
    roles: list[RoleResponse]
    timezone: str = "Asia/Yekaterinburg"
    telegram_chat_id: str | None = None
    bitrix24_user_id: str | None = None


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1)
    full_name: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    omnidesk_staff_id: StaffId | None = None
    roles: list[int] = Field(default_factory=lambda: [int(RoleId.L1)])
    is_active: bool = True


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    omnidesk_staff_id: StaffId | None = None
    is_active: bool | None = None


class UserRolesUpdateRequest(BaseModel):
    roles: list[int] = Field(min_length=1)


def get_admin_user_repository() -> Generator[AdminUserRepository, None, None]:
    with db_connection() as connection:
        yield PostgresAdminUserRepository(connection)


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    candidate = request.client.host
    try:
        ip_address(candidate)
    except ValueError:
        return None
    return candidate


def _serialize_user(user: AdminUserRecord) -> AdminUserResponse:
    return AdminUserResponse(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        phone=user.phone,
        omnidesk_staff_id=user.omnidesk_staff_id,
        is_active=user.is_active,
        roles=[RoleResponse(id=r.id, name=r.name) for r in user.roles],
        timezone=user.timezone,
        telegram_chat_id=user.telegram_chat_id,
        bitrix24_user_id=user.bitrix24_user_id,
    )


@router.get("", response_model=list[AdminUserResponse])
def list_users(
    _: Annotated[UserAuthRecord, Depends(require_admin_role)],
    repo: Annotated[AdminUserRepository, Depends(get_admin_user_repository)],
) -> list[AdminUserResponse]:
    users = repo.list_users()
    return [_serialize_user(u) for u in users]


@router.get("/{user_id}", response_model=AdminUserResponse)
def get_user(
    user_id: int,
    _: Annotated[UserAuthRecord, Depends(require_admin_role)],
    repo: Annotated[AdminUserRepository, Depends(get_admin_user_repository)],
) -> AdminUserResponse:
    user = repo.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user_not_found",
        )
    return _serialize_user(user)


@router.post("", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    actor: Annotated[UserAuthRecord, Depends(require_admin_role)],
    repo: Annotated[AdminUserRepository, Depends(get_admin_user_repository)],
) -> AdminUserResponse:
    try:
        user = repo.create_user(
            username=payload.username,
            password=payload.password,
            full_name=payload.full_name,
            email=payload.email,
            phone=payload.phone,
            omnidesk_staff_id=payload.omnidesk_staff_id,
            roles=payload.roles,
            is_active=payload.is_active,
            actor_user_id=actor.id,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except UsernameConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except OmnideskStaffIdConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except InvalidRoleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.detail,
        ) from exc
    return _serialize_user(user)


@router.patch("/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    request: Request,
    actor: Annotated[UserAuthRecord, Depends(require_admin_role)],
    repo: Annotated[AdminUserRepository, Depends(get_admin_user_repository)],
) -> AdminUserResponse:
    try:
        user = repo.update_user(
            user_id=user_id,
            full_name=payload.full_name,
            email=payload.email,
            phone=payload.phone,
            omnidesk_staff_id=payload.omnidesk_staff_id,
            is_active=payload.is_active,
            actor_user_id=actor.id,
            fields_set=payload.model_fields_set,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail,
        ) from exc
    except SelfDeactivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except LastAdminDeactivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except OmnideskStaffIdConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    return _serialize_user(user)


@router.put("/{user_id}/roles", response_model=AdminUserResponse)
def update_user_roles(
    user_id: int,
    payload: UserRolesUpdateRequest,
    request: Request,
    actor: Annotated[UserAuthRecord, Depends(require_admin_role)],
    repo: Annotated[AdminUserRepository, Depends(get_admin_user_repository)],
) -> AdminUserResponse:
    try:
        user = repo.update_user_roles(
            user_id=user_id,
            role_ids=payload.roles,
            actor_user_id=actor.id,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail,
        ) from exc
    except LastAdminRoleRemovalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except InvalidRoleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.detail,
        ) from exc
    return _serialize_user(user)

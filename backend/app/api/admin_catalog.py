from __future__ import annotations

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.admin.catalog import (
    InvalidTemplatePlaceholderError,
    LastActiveResultDeactivationError,
    NotificationTemplateNotFoundError,
    ResultCodeConflictError,
    ResultNotFoundError,
    build_integrations_status,
    validate_template_placeholders,
)
from app.admin.repository import (
    AdministrativeRepository,
    ConnectionResult,
)
from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import RoleId
from app.db import get_db

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

require_admin_role = require_roles(int(RoleId.ADMIN))


# ---------------------------------------------------------------------------
# 1. Connection Results
# ---------------------------------------------------------------------------


class ConnectionResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: int
    name: str
    is_active: bool
    sort_order: int


class ConnectionResultCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=255)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("result_name_cannot_be_blank")
        return trimmed


class ConnectionResultUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    is_active: bool
    sort_order: int = Field(default=0, ge=0)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("result_name_cannot_be_blank")
        return trimmed


@router.get("/results", response_model=list[ConnectionResultResponse])
def list_results(
    _user: Annotated[UserAuthRecord, Depends(require_admin_role)],
    connection: Annotated[psycopg.Connection, Depends(get_db)],
) -> list[ConnectionResultResponse]:
    repo = AdministrativeRepository(connection)
    results = repo.list_all_results()
    return [
        ConnectionResultResponse(
            code=r.code,
            name=r.name,
            is_active=r.is_active,
            sort_order=r.sort_order,
        )
        for r in results
    ]


@router.post(
    "/results",
    response_model=ConnectionResultResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_result(
    payload: ConnectionResultCreateRequest,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
    connection: Annotated[psycopg.Connection, Depends(get_db)],
) -> ConnectionResultResponse:
    repo = AdministrativeRepository(connection)
    try:
        created = repo.create_result(
            result=ConnectionResult(
                code=payload.code,
                name=payload.name,
                is_active=payload.is_active,
                sort_order=payload.sort_order,
            ),
            actor_user_id=user.id,
        )
    except (ResultCodeConflictError, ValueError) as exc:
        msg = str(exc)
        if "result_code_already_exists" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="result_code_already_exists",
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=msg,
        )
    return ConnectionResultResponse(
        code=created.code,
        name=created.name,
        is_active=created.is_active,
        sort_order=created.sort_order,
    )


@router.put("/results/{code}", response_model=ConnectionResultResponse)
def update_result(
    code: Annotated[int, Path(ge=0)],
    payload: ConnectionResultUpdateRequest,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
    connection: Annotated[psycopg.Connection, Depends(get_db)],
) -> ConnectionResultResponse:
    repo = AdministrativeRepository(connection)
    try:
        updated = repo.update_result(
            result=ConnectionResult(
                code=code,
                name=payload.name,
                is_active=payload.is_active,
                sort_order=payload.sort_order,
            ),
            actor_user_id=user.id,
        )
    except ResultNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="result_not_found",
        )
    except LastActiveResultDeactivationError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot_deactivate_last_active_result",
        )
    except ValueError as exc:
        msg = str(exc)
        if "result_not_found" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="result_not_found",
            )
        if "cannot_deactivate_last_active_result" in msg:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="cannot_deactivate_last_active_result",
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=msg,
        )
    return ConnectionResultResponse(
        code=updated.code,
        name=updated.name,
        is_active=updated.is_active,
        sort_order=updated.sort_order,
    )


# ---------------------------------------------------------------------------
# 2. Notification Templates
# ---------------------------------------------------------------------------


class NotificationTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    channel_code: int
    visible: bool
    subject_template: str | None
    body_template: str


class NotificationTemplateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_template: str | None = Field(default=None, max_length=1000)
    body_template: str = Field(min_length=1, max_length=4000)
    visible: bool = True

    @field_validator("body_template")
    @classmethod
    def validate_body(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("body_template_cannot_be_blank")
        if len(v) > 4000:
            raise ValueError("body_template_max_length_exceeded")
        try:
            validate_template_placeholders(v)
        except InvalidTemplatePlaceholderError as exc:
            raise ValueError(str(exc)) from exc
        return v

    @field_validator("subject_template")
    @classmethod
    def validate_subject(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                validate_template_placeholders(v)
            except InvalidTemplatePlaceholderError as exc:
                raise ValueError(str(exc)) from exc
        return v


@router.get(
    "/notification-templates", response_model=list[NotificationTemplateResponse]
)
def list_notification_templates(
    _user: Annotated[UserAuthRecord, Depends(require_admin_role)],
    connection: Annotated[psycopg.Connection, Depends(get_db)],
) -> list[NotificationTemplateResponse]:
    repo = AdministrativeRepository(connection)
    templates = repo.list_notification_templates()
    return [
        NotificationTemplateResponse(
            code=t.code,
            channel_code=t.channel_code,
            visible=t.visible,
            subject_template=t.subject_template,
            body_template=t.body_template,
        )
        for t in templates
    ]


@router.put(
    "/notification-templates/{code}", response_model=NotificationTemplateResponse
)
def update_notification_template(
    code: Annotated[str, Path(min_length=1, max_length=100)],
    payload: NotificationTemplateUpdateRequest,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
    connection: Annotated[psycopg.Connection, Depends(get_db)],
) -> NotificationTemplateResponse:
    repo = AdministrativeRepository(connection)
    try:
        updated = repo.update_notification_template(
            code=code,
            subject_template=payload.subject_template,
            body_template=payload.body_template,
            visible=payload.visible,
            actor_user_id=user.id,
        )
    except NotificationTemplateNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notification_template_not_found",
        )
    except ValueError as exc:
        msg = str(exc)
        if "notification_template_not_found" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="notification_template_not_found",
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=msg,
        )
    return NotificationTemplateResponse(
        code=updated.code,
        channel_code=updated.channel_code,
        visible=updated.visible,
        subject_template=updated.subject_template,
        body_template=updated.body_template,
    )


# ---------------------------------------------------------------------------
# 3. Integration Status
# ---------------------------------------------------------------------------


class OmnideskStatusResponse(BaseModel):
    configured: bool
    enabled: bool
    base_url: str
    staff_email: str
    case_index_enabled: bool
    case_backfill_window_days: int
    timeout_seconds: float
    public_notification_enabled: bool
    cancellation_public_notification_enabled: bool


class TelegramStatusResponse(BaseModel):
    configured: bool
    enabled: bool
    api_url: str
    delivery_enabled: bool
    scanner_enabled: bool
    scan_interval_seconds: int
    reminder_l1_interval_seconds: int
    reminder_l2_interval_seconds: int


class Bitrix24StatusResponse(BaseModel):
    configured: bool
    enabled: bool
    bot_id: str
    bot_client_id: str
    delivery_enabled: bool


class IntegrationsStatusResponse(BaseModel):
    omnidesk: OmnideskStatusResponse
    telegram: TelegramStatusResponse
    bitrix24: Bitrix24StatusResponse


@router.get("/integrations/status", response_model=IntegrationsStatusResponse)
def get_integrations_status(
    _user: Annotated[UserAuthRecord, Depends(require_admin_role)],
    connection: Annotated[psycopg.Connection, Depends(get_db)],
) -> IntegrationsStatusResponse:
    repo = AdministrativeRepository(connection)
    pub_settings = repo.get_public_notification_settings()
    cancel_settings = repo.get_cancellation_public_notification_settings()
    pub_enabled = pub_settings.get("enabled", True)
    cancel_enabled = cancel_settings.get("enabled", True)

    status_data = build_integrations_status(
        public_notification_enabled=pub_enabled,
        cancellation_public_notification_enabled=cancel_enabled,
    )
    return IntegrationsStatusResponse(
        omnidesk=OmnideskStatusResponse(
            configured=status_data.omnidesk.configured,
            enabled=status_data.omnidesk.enabled,
            base_url=status_data.omnidesk.base_url,
            staff_email=status_data.omnidesk.staff_email,
            case_index_enabled=status_data.omnidesk.case_index_enabled,
            case_backfill_window_days=status_data.omnidesk.case_backfill_window_days,
            timeout_seconds=status_data.omnidesk.timeout_seconds,
            public_notification_enabled=status_data.omnidesk.public_notification_enabled,
            cancellation_public_notification_enabled=status_data.omnidesk.cancellation_public_notification_enabled,
        ),
        telegram=TelegramStatusResponse(
            configured=status_data.telegram.configured,
            enabled=status_data.telegram.enabled,
            api_url=status_data.telegram.api_url,
            delivery_enabled=status_data.telegram.delivery_enabled,
            scanner_enabled=status_data.telegram.scanner_enabled,
            scan_interval_seconds=status_data.telegram.scan_interval_seconds,
            reminder_l1_interval_seconds=status_data.telegram.reminder_l1_interval_seconds,
            reminder_l2_interval_seconds=status_data.telegram.reminder_l2_interval_seconds,
        ),
        bitrix24=Bitrix24StatusResponse(
            configured=status_data.bitrix24.configured,
            enabled=status_data.bitrix24.enabled,
            bot_id=status_data.bitrix24.bot_id,
            bot_client_id=status_data.bitrix24.bot_client_id,
            delivery_enabled=status_data.bitrix24.delivery_enabled,
        ),
    )

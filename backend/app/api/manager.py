from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi import status as http_status
from pydantic import BaseModel, Field

from app.admin.repository import AdministrativeRepository
from app.auth.dependencies import get_current_user, require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import CardStatus, CardStatusSlug, RoleId, status_slug
from app.cards.policy import CardActionPolicyError, authorize_create, role_ids
from app.cards.repository import CardRepository, ClientSyncData, PostgresCardRepository
from app.cards.schemas import (
    CardCreateRequest,
    CardResponse,
    card_response,
    status_label,
)
from app.cards.service import CardService, InvalidCardTransitionError
from app.db import db_connection
from app.frame.omnidesk import (
    OmnideskTicketClient,
    get_omnidesk_ticket_client,
)
from app.manager_create import (
    ManagerCreateConflictError,
    ManagerCreateRequest,
    ManagerL2Option,
    ManagerL2OptionsResponse,
    ManagerTicketPreflightResponse,
    run_manager_create_transaction,
    validate_manager_window,
)
from app.notifications import PostgresNotificationService
from app.omnidesk_index.resolver import (
    PublicTicketResolutionError,
    resolve_ticket_by_case_id,
    resolve_ticket_by_case_number,
)

router = APIRouter(prefix="/api/v1/manager", tags=["manager"])


class ManagerSummary(BaseModel):
    assigned: int = 0
    confirmed: int = 0
    rejected: int = 0
    overdue: int = 0
    urgent: int = 0
    urgent_collision: int = 0


class ManagerCard(BaseModel):
    public_id: str
    number: str
    omnidesk_ticket_number: str
    status: str
    status_label: str
    planned_start_at: datetime
    planned_end_at: datetime
    planned_duration_minutes: int
    l1_owner_name: str | None
    l2_engineer_name: str | None
    urgent: bool
    overdue: bool
    out_of_hours: bool


class ManagerCardsResponse(BaseModel):
    summary: ManagerSummary
    items: list[ManagerCard]
    limit: int


@dataclass(frozen=True)
class ManagerFilters:
    status: CardStatusSlug | None
    period_from: datetime | None
    period_to: datetime | None
    limit: int


@dataclass(frozen=True)
class ManagerAccessContext:
    user: UserAuthRecord
    filters: ManagerFilters


def get_manager_filters(
    status: Annotated[CardStatusSlug | None, Query()] = None,
    period_from: Annotated[datetime | None, Query()] = None,
    period_to: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ManagerFilters:
    if period_from is not None and (
        period_from.tzinfo is None or period_from.utcoffset() is None
    ):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_from_timezone_required",
        )
    if period_to is not None and (
        period_to.tzinfo is None or period_to.utcoffset() is None
    ):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_to_timezone_required",
        )
    if period_from is not None and period_to is not None and period_from >= period_to:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_from_must_be_before_period_to",
        )
    return ManagerFilters(status, period_from, period_to, limit)


require_manager_role = require_roles(int(RoleId.MANAGER))
require_admin_role = require_roles(int(RoleId.ADMIN))


class SessionExtensionIntervalUpdate(BaseModel):
    interval_seconds: int = Field(ge=60, le=86_400, multiple_of=60)


class SessionExtensionIntervalResponse(BaseModel):
    interval_seconds: int


class PublicNotificationSettingsUpdate(BaseModel):
    enabled: bool
    template: str = Field(min_length=1)


class PublicNotificationSettingsResponse(BaseModel):
    enabled: bool
    template: str


@router.get(
    "/settings/session-extension-interval",
    response_model=SessionExtensionIntervalResponse,
)
def get_session_extension_interval(
    _user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> SessionExtensionIntervalResponse:
    with db_connection() as connection:
        seconds = AdministrativeRepository(connection).get_extension_interval()
    return SessionExtensionIntervalResponse(interval_seconds=seconds)


@router.put(
    "/settings/session-extension-interval",
    response_model=SessionExtensionIntervalResponse,
)
def update_session_extension_interval(
    payload: SessionExtensionIntervalUpdate,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> SessionExtensionIntervalResponse:
    with db_connection() as connection:
        AdministrativeRepository(connection).set_extension_interval(
            interval_seconds=payload.interval_seconds,
            actor_user_id=user.id,
        )
    return SessionExtensionIntervalResponse(interval_seconds=payload.interval_seconds)


@router.get(
    "/settings/public-notification",
    response_model=PublicNotificationSettingsResponse,
)
def get_public_notification_settings(
    _user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> PublicNotificationSettingsResponse:
    with db_connection() as connection:
        settings = AdministrativeRepository(connection).get_public_notification_settings()
    return PublicNotificationSettingsResponse(**settings)


@router.put(
    "/settings/public-notification",
    response_model=PublicNotificationSettingsResponse,
)
def update_public_notification_settings(
    payload: PublicNotificationSettingsUpdate,
    user: Annotated[UserAuthRecord, Depends(require_admin_role)],
) -> PublicNotificationSettingsResponse:
    with db_connection() as connection:
        AdministrativeRepository(connection).set_public_notification_settings(
            enabled=payload.enabled,
            template=payload.template,
            actor_user_id=user.id,
        )
    return PublicNotificationSettingsResponse(enabled=payload.enabled, template=payload.template)


def _manager_ticket(client: OmnideskTicketClient, case_id: str, case_number: str):
    try:
        return resolve_ticket_by_case_id(client, case_id, case_number)
    except PublicTicketResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _manager_ticket_by_number(
    connection, client: OmnideskTicketClient, case_number: str
):
    try:
        return resolve_ticket_by_case_number(connection, client, case_number)
    except PublicTicketResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.get(
    "/tickets/{case_number}/preflight",
    response_model=ManagerTicketPreflightResponse,
)
def manager_ticket_preflight(
    case_number: Annotated[str, Path(pattern=r"^[0-9]{3}-[0-9]{6}$")],
    _: Annotated[UserAuthRecord, Depends(require_manager_role)],
    omnidesk: Annotated[OmnideskTicketClient, Depends(get_omnidesk_ticket_client)],
) -> ManagerTicketPreflightResponse:
    with db_connection() as connection:
        ticket = _manager_ticket_by_number(connection, omnidesk, case_number)
        repository = PostgresCardRepository(connection)
        can_create = bool(ticket.user_id) and not repository.has_active_card_for_ticket(
            ticket.number
        )
    return ManagerTicketPreflightResponse(
        case_number=ticket.number,
        status=ticket.status,
        client_display_name=ticket.client_display_name,
        can_create=can_create,
    )


@router.get("/l2-options", response_model=ManagerL2OptionsResponse)
def manager_l2_options(
    planned_start_at: datetime,
    planned_duration_minutes: Annotated[int, Query(ge=30, le=720)],
    _: Annotated[UserAuthRecord, Depends(require_manager_role)],
) -> ManagerL2OptionsResponse:
    if planned_start_at.tzinfo is None or planned_start_at.utcoffset() is None:
        raise HTTPException(
            status_code=422, detail="planned_start_at_must_be_timezone_aware"
        )
    try:
        validate_manager_window(
            planned_start_at.astimezone(datetime.now().astimezone().tzinfo),
            planned_duration_minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    start = planned_start_at.astimezone(datetime.now().astimezone().tzinfo)
    end = start + timedelta(minutes=planned_duration_minutes)
    with db_connection() as connection:
        repository = PostgresCardRepository(connection)
        candidates = {
            c.user_id: c
            for c in repository.list_all_l2_candidates(
                planned_start_at=start, planned_end_at=end
            )
        }
        items = []
        from app.assignments.service import _candidate_is_available

        for user_id, candidate in candidates.items():
            available = _candidate_is_available(
                candidate, planned_start_at=start, planned_end_at=end
            )
            items.append(
                ManagerL2Option(
                    user_id=user_id,
                    display_name=repository.get_user_display_name(user_id) or "L2",
                    available=available,
                    reason_code=None if available else "schedule_or_conflict",
                )
            )
    return ManagerL2OptionsResponse(items=items)


@router.post("/cards", response_model=CardResponse, status_code=201)
def manager_create_card(
    payload: ManagerCreateRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    omnidesk: Annotated[OmnideskTicketClient, Depends(get_omnidesk_ticket_client)],
) -> CardResponse:
    try:
        authorize_create(actor_role_ids=role_ids(user.roles))
    except CardActionPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    try:
        validate_manager_window(
            payload.planned_start_at, payload.planned_duration_minutes
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        with db_connection() as connection:
            ticket = _manager_ticket_by_number(
                connection, omnidesk, payload.case_number
            )
            if not ticket.user_id:
                raise HTTPException(status_code=422, detail="ticket_client_missing")
            repository = PostgresCardRepository(connection)
            if repository.has_active_card_for_ticket(ticket.number):
                raise HTTPException(
                    status_code=409, detail="active_card_exists_for_ticket"
                )
            client = repository.get_or_create_client(
                ClientSyncData(
                    omnidesk_user_id=ticket.user_id,
                    omnidesk_company_id=ticket.company_id,
                    display_name=ticket.client_display_name,
                    preferred_contact_value=ticket.client_contact_value,
                )
            )
            try:
                card = run_manager_create_transaction(
                    lambda: CardService(
                        repository, PostgresNotificationService(connection)
                    ).create_card(
                        CardCreateRequest(
                            omnidesk_ticket_number=ticket.number,
                            planned_start_at=payload.planned_start_at,
                            planned_duration_minutes=payload.planned_duration_minutes,
                            client_id=client.id,
                            description=payload.description,
                            l2_engineer_id=payload.l2_user_id,
                        ),
                        actor_user_id=user.id,
                        ip_address=request.client.host if request.client else None,
                        user_agent=request.headers.get("user-agent"),
                        manual_assignment=payload.l2_user_id is not None,
                        allow_out_of_hours=payload.l2_user_id is not None,
                    ),
                    rollback=connection.rollback,
                )
            except InvalidCardTransitionError as exc:
                raise HTTPException(status_code=409, detail=exc.detail) from exc
    except ManagerCreateConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    return card_response(card)


def get_manager_context(
    user: Annotated[UserAuthRecord, Depends(require_manager_role)],
    filters: Annotated[ManagerFilters, Depends(get_manager_filters)],
) -> ManagerAccessContext:
    return ManagerAccessContext(user=user, filters=filters)


def get_manager_repository(
    _context: Annotated[ManagerAccessContext, Depends(get_manager_context)],
) -> Generator[CardRepository, None, None]:
    with db_connection() as connection:
        yield PostgresCardRepository(connection)


@router.get("/cards", response_model=ManagerCardsResponse)
def list_manager_cards(
    repository: Annotated[CardRepository, Depends(get_manager_repository)],
    context: Annotated[ManagerAccessContext, Depends(get_manager_context)],
) -> ManagerCardsResponse:
    rows, counts = repository.list_manager_cards(
        status_code=(
            next(
                item
                for item in CardStatus
                if status_slug(item) == context.filters.status
            )
            if context.filters.status is not None
            else None
        ),
        period_from=context.filters.period_from,
        period_to=context.filters.period_to,
        limit=context.filters.limit,
    )
    return ManagerCardsResponse(
        summary=ManagerSummary(**counts),
        items=[
            ManagerCard(
                public_id=str(card.public_id),
                number=card.number,
                omnidesk_ticket_number=card.omnidesk_ticket_number,
                status=status_slug(card.status_code).value,
                status_label=status_label(card.status_code),
                planned_start_at=card.planned_start_at,
                planned_end_at=card.planned_start_at
                + timedelta(minutes=card.planned_duration_minutes),
                planned_duration_minutes=card.planned_duration_minutes,
                l1_owner_name=card.l1_owner_name,
                l2_engineer_name=card.l2_engineer_name,
                urgent=card.urgency_code > 0,
                overdue=card.overdue_flag,
                out_of_hours=card.out_of_hours_flag,
            )
            for card in rows
        ],
        limit=context.filters.limit,
    )

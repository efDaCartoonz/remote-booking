from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi import status as http_status
from pydantic import BaseModel

from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import CardStatus, CardStatusSlug, RoleId, status_slug
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
    OmnideskTicketClientChangedError,
    OmnideskTicketMismatchError,
    OmnideskTicketNotFoundError,
    OmnideskTicketReopenError,
    get_omnidesk_ticket_client,
    validate_ticket_response,
)
from app.manager_create import (
    ManagerCreateRequest,
    ManagerL2Option,
    ManagerL2OptionsResponse,
    ManagerTicketPreflightResponse,
    validate_manager_window,
)
from app.notifications import PostgresNotificationService

router = APIRouter(prefix="/api/v1/manager", tags=["manager"])


class ManagerSummary(BaseModel):
    assigned: int
    confirmed: int
    rejected: int
    overdue: int


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


def _manager_ticket(client: OmnideskTicketClient, case_id: str, case_number: str):
    try:
        ticket = client.get_ticket_by_case_id(case_id)
    except (OmnideskTicketNotFoundError, OmnideskTicketMismatchError) as exc:
        raise HTTPException(
            status_code=404, detail="omnidesk_ticket_not_found"
        ) from exc
    try:
        ticket = validate_ticket_response(
            ticket, case_id=case_id, case_number=case_number
        )
    except OmnideskTicketMismatchError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    except OmnideskTicketNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.detail) from exc
    original_user_id = ticket.user_id
    if ticket.status == "closed":
        try:
            client.reopen_ticket(case_id)
            ticket = validate_ticket_response(
                client.get_ticket_by_case_id(case_id),
                case_id=case_id,
                case_number=case_number,
                expected_user_id=original_user_id,
                require_open=True,
            )
        except (
            OmnideskTicketNotFoundError,
            OmnideskTicketMismatchError,
            OmnideskTicketReopenError,
            OmnideskTicketClientChangedError,
        ) as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc
    return ticket


@router.get(
    "/tickets/{case_id}/preflight", response_model=ManagerTicketPreflightResponse
)
def manager_ticket_preflight(
    case_id: str,
    case_number: str,
    _: Annotated[UserAuthRecord, Depends(require_manager_role)],
    omnidesk: Annotated[OmnideskTicketClient, Depends(get_omnidesk_ticket_client)],
) -> ManagerTicketPreflightResponse:
    ticket = _manager_ticket(omnidesk, case_id, case_number)
    with db_connection() as connection:
        repository = PostgresCardRepository(connection)
        can_create = bool(ticket.user_id) and not repository.has_active_card_for_ticket(
            ticket.number
        )
    return ManagerTicketPreflightResponse(
        case_id=ticket.case_id,
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
    user: Annotated[UserAuthRecord, Depends(require_manager_role)],
    omnidesk: Annotated[OmnideskTicketClient, Depends(get_omnidesk_ticket_client)],
) -> CardResponse:
    try:
        validate_manager_window(
            payload.planned_start_at, payload.planned_duration_minutes
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ticket = _manager_ticket(omnidesk, payload.case_id, payload.case_number)
    if not ticket.user_id:
        raise HTTPException(status_code=422, detail="ticket_client_missing")
    with db_connection() as connection:
        repository = PostgresCardRepository(connection)
        if repository.has_active_card_for_ticket(ticket.number):
            raise HTTPException(status_code=409, detail="active_card_exists_for_ticket")
        client = repository.get_or_create_client(
            ClientSyncData(
                omnidesk_user_id=ticket.user_id,
                omnidesk_company_id=ticket.company_id,
                display_name=ticket.client_display_name,
                preferred_contact_value=ticket.client_contact_value,
            )
        )
        try:
            card = CardService(
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
            )
        except InvalidCardTransitionError as exc:
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

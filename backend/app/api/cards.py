from __future__ import annotations

from collections.abc import Callable
from ipaddress import ip_address
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg.errors import ExclusionViolation

from app.auth.dependencies import get_current_user
from app.auth.store import UserAuthRecord
from app.cards.policy import (
    CardActionPolicyError,
    authorize_create,
    role_ids,
)
from app.cards.create_policy import CreateScenario, validate_role_create
from app.cards.repository import (
    CardRecord,
    CardRepository,
    ClientSyncData,
    PostgresCardRepository,
)
from app.cards.schemas import (
    CardEndPendingResultRequest,
    CardAssignRequest,
    CardCompleteRequest,
    CardCreateRequest,
    CardHistoryResponse,
    CardRejectRequest,
    CardResponse,
    CardStatusChangeRequest,
    L1CardCreateRequest,
    L1RescheduleRequest,
    L2RetroactiveCreateRequest,
    L2SelfCreateRequest,
    L2UrgentCreateRequest,
    card_history_response,
    card_response,
)
from app.cards.service import CardNotFoundError, CardService, InvalidCardTransitionError
from app.db import db_connection, get_db
from app.frame.omnidesk import OmnideskTicketClient, get_omnidesk_ticket_client
from app.manager_create import (
    ManagerCreateConflictError,
    run_manager_create_transaction,
)
from app.notifications import PostgresNotificationService
from app.omnidesk_index.resolver import (
    PublicTicketResolutionError,
    resolve_ticket_by_case_number,
)

router = APIRouter(prefix="/api/v1/cards", tags=["cards"])


def get_card_repository(
    connection: Annotated[object, Depends(get_db)],
) -> CardRepository:
    return PostgresCardRepository(connection)


def get_notification_service(
    connection: Annotated[object, Depends(get_db)],
) -> PostgresNotificationService:
    return PostgresNotificationService(connection)


def get_card_service(
    repository: Annotated[CardRepository, Depends(get_card_repository)],
    notifications: Annotated[PostgresNotificationService, Depends(get_notification_service)],
) -> CardService:
    return CardService(repository, notifications)


@router.post("/l1", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
def create_l1_card(
    payload: L1CardCreateRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    omnidesk: Annotated[OmnideskTicketClient, Depends(get_omnidesk_ticket_client)],
) -> CardResponse:
    return _create_role_card(
        payload=payload,
        scenario=CreateScenario.L1,
        request=request,
        user=user,
        omnidesk=omnidesk,
    )


@router.post("/l2", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
def create_l2_self_card(
    payload: L2SelfCreateRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    omnidesk: Annotated[OmnideskTicketClient, Depends(get_omnidesk_ticket_client)],
) -> CardResponse:
    return _create_role_card(
        payload=payload,
        scenario=CreateScenario.L2_SELF,
        request=request,
        user=user,
        omnidesk=omnidesk,
    )


@router.post(
    "/l2/urgent", response_model=CardResponse, status_code=status.HTTP_201_CREATED
)
def create_l2_urgent_card(
    payload: L2UrgentCreateRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    omnidesk: Annotated[OmnideskTicketClient, Depends(get_omnidesk_ticket_client)],
) -> CardResponse:
    return _create_role_card(
        payload=payload,
        scenario=CreateScenario.L2_URGENT,
        request=request,
        user=user,
        omnidesk=omnidesk,
    )


@router.post(
    "/l2/retroactive", response_model=CardResponse, status_code=status.HTTP_201_CREATED
)
def create_l2_retroactive_card(
    payload: L2RetroactiveCreateRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    omnidesk: Annotated[OmnideskTicketClient, Depends(get_omnidesk_ticket_client)],
) -> CardResponse:
    return _create_role_card(
        payload=payload,
        scenario=CreateScenario.L2_RETROACTIVE,
        request=request,
        user=user,
        omnidesk=omnidesk,
    )


@router.get("/{card_id}", response_model=CardResponse)
def get_card(
    card_id: UUID,
    _: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    try:
        return card_response(service.get_card(card_id))
    except CardNotFoundError as exc:
        raise _not_found() from exc


@router.get("/{card_id}/history", response_model=list[CardHistoryResponse])
def get_card_history(
    card_id: UUID,
    _: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> list[CardHistoryResponse]:
    try:
        return [
            card_history_response(event) for event in service.list_card_history(card_id)
        ]
    except CardNotFoundError as exc:
        raise _not_found() from exc


@router.post("/{card_id}/assign", response_model=CardResponse)
def assign_card(
    card_id: UUID,
    payload: CardAssignRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.assign_card(
            card_id,
            l2_engineer_id=payload.l2_engineer_id,
            actor_user_id=user.id,
            actor_role_ids=role_ids(user.roles),
            comment=payload.comment,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/{card_id}/confirm", response_model=CardResponse)
def confirm_card(
    card_id: UUID,
    payload: CardStatusChangeRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.confirm_card(
            card_id,
            actor_user_id=user.id,
            actor_role_ids=role_ids(user.roles),
            comment=payload.comment,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/{card_id}/reject", response_model=CardResponse)
def reject_card(
    card_id: UUID,
    payload: CardRejectRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.reject_card(
            card_id,
            actor_user_id=user.id,
            actor_role_ids=role_ids(user.roles),
            rejection_reason=payload.rejection_reason,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/{card_id}/start", response_model=CardResponse)
def start_card(
    card_id: UUID,
    payload: CardStatusChangeRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.start_card(
            card_id,
            actor_user_id=user.id,
            actor_role_ids=role_ids(user.roles),
            comment=payload.comment,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/{card_id}/end-pending-result", response_model=CardResponse)
def end_pending_result(
    card_id: UUID,
    payload: CardEndPendingResultRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.end_pending_result(
            card_id,
            actor_user_id=user.id,
            actor_role_ids=role_ids(user.roles),
            comment=payload.comment,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/{card_id}/complete", response_model=CardResponse)
def complete_card(
    card_id: UUID,
    payload: CardCompleteRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.complete_card(
            card_id,
            result_code=payload.result_code,
            engineer_report=payload.engineer_report,
            actor_user_id=user.id,
            actual_duration_minutes=payload.actual_duration_minutes,
            actor_role_ids=role_ids(user.roles),
            comment=payload.comment,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/{card_id}/cancel", response_model=CardResponse)
def cancel_card(
    card_id: UUID,
    payload: CardStatusChangeRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.cancel_card(
            card_id,
            actor_user_id=user.id,
            actor_role_ids=role_ids(user.roles),
            comment=payload.comment,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/{card_id}/l1/client-informed", response_model=CardResponse)
def mark_client_informed(
    card_id: UUID,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.mark_client_informed(
            card_id,
            actor_user_id=user.id,
            actor_role_ids=role_ids(user.roles),
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


@router.post("/{card_id}/l1/reschedule", response_model=CardResponse)
def reschedule_rejected(
    card_id: UUID,
    payload: L1RescheduleRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    return _handle_change(
        lambda: service.reschedule_card(
            card_id,
            actor_user_id=user.id,
            actor_role_ids=role_ids(user.roles),
            planned_start_at=payload.planned_start_at,
            planned_duration_minutes=payload.planned_duration_minutes,
            description=payload.description,
            reason=payload.reason,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            selected_l2_engineer_id=payload.l2_engineer_id,
        ),
        rollback=_connection_rollback(service),
    )


def _handle_change(
    change: Callable[[], CardRecord], *, rollback: Callable[[], None] | None = None
) -> CardResponse:
    try:
        card = change()
    except CardNotFoundError as exc:
        raise _not_found() from exc
    except InvalidCardTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    except CardActionPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except ExclusionViolation as exc:
        constraint_name = getattr(getattr(exc, "diag", None), "constraint_name", None)
        if constraint_name != "ex_connection_cards_l2_no_overlap":
            raise
        if rollback is not None:
            rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="l2_assignment_conflict",
        ) from None
    return card_response(card)


def _connection_rollback(service: CardService) -> Callable[[], None] | None:
    """Return the DB rollback hook for a change handled by the API.

    In-memory repositories used by unit tests intentionally have no connection.
    PostgreSQL exclusion errors abort the current transaction, so the API must
    roll back before converting the error into a safe 409 response.
    """
    connection = getattr(service.repository, "connection", None)
    rollback = getattr(connection, "rollback", None)
    return rollback if callable(rollback) else None


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card_not_found")


def _authorize_create(user: UserAuthRecord) -> None:
    try:
        authorize_create(actor_role_ids=role_ids(user.roles))
    except CardActionPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _create_role_card(
    *,
    payload: L1CardCreateRequest
    | L2SelfCreateRequest
    | L2UrgentCreateRequest
    | L2RetroactiveCreateRequest,
    scenario: CreateScenario,
    request: Request,
    user: UserAuthRecord,
    omnidesk: OmnideskTicketClient,
) -> CardResponse:
    try:
        authorize_create(actor_role_ids=role_ids(user.roles), scenario=scenario)
    except CardActionPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    try:
        plan = validate_role_create(
            scenario=scenario,
            planned_start_at=payload.planned_start_at,
            planned_duration_minutes=payload.planned_duration_minutes,
            urgent_reason=getattr(payload, "urgent_reason", None),
            result_code=getattr(payload, "result_code", None),
            engineer_report=getattr(payload, "engineer_report", None),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        with db_connection() as connection:
            try:
                ticket = resolve_ticket_by_case_number(
                    connection, omnidesk, payload.case_number
                )
            except PublicTicketResolutionError as exc:
                raise HTTPException(
                    status_code=exc.status_code, detail=exc.detail
                ) from exc
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
                        ),
                        actor_user_id=user.id,
                        ip_address=_client_ip(request),
                        user_agent=request.headers.get("user-agent"),
                        allow_out_of_hours=scenario
                        in {
                            CreateScenario.L2_SELF,
                            CreateScenario.L2_URGENT,
                            CreateScenario.L2_RETROACTIVE,
                        },
                        role_create_plan=plan,
                    ),
                    rollback=connection.rollback,
                )
            except InvalidCardTransitionError as exc:
                raise HTTPException(status_code=409, detail=exc.detail) from exc
    except ManagerCreateConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    return card_response(card)


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    candidate = request.client.host
    try:
        ip_address(candidate)
    except ValueError:
        return None
    return candidate

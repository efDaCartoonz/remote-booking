from __future__ import annotations

from collections.abc import Callable
from ipaddress import ip_address
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.dependencies import get_current_user
from app.auth.store import UserAuthRecord
from app.cards.constants import RoleId
from app.cards.repository import CardRecord, CardRepository, PostgresCardRepository
from app.cards.schemas import (
    CardAssignRequest,
    CardCompleteRequest,
    CardCreateRequest,
    CardRejectRequest,
    CardResponse,
    CardStatusChangeRequest,
    L1RescheduleRequest,
    card_response,
)
from app.cards.service import CardNotFoundError, CardService, InvalidCardTransitionError
from app.db import get_db
from app.notifications import PostgresNotificationService

router = APIRouter(prefix="/api/v1/cards", tags=["cards"])


def get_card_repository(
    connection: Annotated[object, Depends(get_db)],
) -> CardRepository:
    return PostgresCardRepository(connection)


def get_card_service(
    repository: Annotated[CardRepository, Depends(get_card_repository)],
) -> CardService:
    notifications = (
        PostgresNotificationService(repository.connection)
        if isinstance(repository, PostgresCardRepository)
        else None
    )
    return CardService(repository, notifications)


@router.post("", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
def create_card(
    payload: CardCreateRequest,
    request: Request,
    user: Annotated[UserAuthRecord, Depends(get_current_user)],
    service: Annotated[CardService, Depends(get_card_service)],
) -> CardResponse:
    card = service.create_card(
        payload,
        actor_user_id=user.id,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return card_response(card)


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
    _require_l2_role(user)
    return _handle_change(
        lambda: service.confirm_card(
            card_id,
            actor_user_id=user.id,
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
    _require_l2_role(user)
    return _handle_change(
        lambda: service.reject_card(
            card_id,
            actor_user_id=user.id,
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
    _require_l1_role(user)
    return _handle_change(
        lambda: service.mark_client_informed(
            card_id,
            actor_user_id=user.id,
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
    _require_l1_role(user)
    return _handle_change(
        lambda: service.update_rejected_card(
            card_id,
            actor_user_id=user.id,
            planned_start_at=payload.planned_start_at,
            planned_duration_minutes=payload.planned_duration_minutes,
            description=payload.description,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    )


def _handle_change(change: Callable[[], CardRecord]) -> CardResponse:
    try:
        card = change()
    except CardNotFoundError as exc:
        raise _not_found() from exc
    except InvalidCardTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail,
        ) from exc
    return card_response(card)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card_not_found")


def _require_l2_role(user: UserAuthRecord) -> None:
    if not any(role.id == int(RoleId.L2) for role in user.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="l2_role_required",
        )


def _require_l1_role(user: UserAuthRecord) -> None:
    if not any(role.id == int(RoleId.L1) for role in user.roles):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="l1_role_required",
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

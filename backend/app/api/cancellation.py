from __future__ import annotations

from ipaddress import ip_address
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.cards.repository import CardRepository, PostgresCardRepository
from app.cards.service import CardService
from app.db import get_db
from app.notifications import NotificationService, PostgresNotificationService
from app.cancellation.schemas import (
    CancellationConfirmRequest,
    CancellationConfirmResponse,
    CancellationVerificationResponse,
    CancellationVerifyRequest,
)
from app.cancellation.service import (
    CancellationAccessError,
    CancellationConflictError,
    CancellationError,
    CancellationExpiredError,
    CancellationNotFoundError,
    CancellationService,
)

router = APIRouter(prefix="/api/v1/cancellation", tags=["cancellation"])


def get_cancellation_card_repository(
    connection: Annotated[object, Depends(get_db)],
) -> CardRepository:
    return PostgresCardRepository(connection)


def get_cancellation_notification_service(
    repository: Annotated[CardRepository, Depends(get_cancellation_card_repository)],
) -> NotificationService | None:
    if not isinstance(repository, PostgresCardRepository):
        return None
    return PostgresNotificationService(repository.connection)


def get_cancellation_service(
    repository: Annotated[CardRepository, Depends(get_cancellation_card_repository)],
    notifications: Annotated[
        NotificationService | None, Depends(get_cancellation_notification_service)
    ],
) -> CancellationService:
    card_service = CardService(repository, notifications)
    return CancellationService(repository=repository, card_service=card_service)


@router.post("/verify", response_model=CancellationVerificationResponse)
def verify_cancellation_by_body(
    payload: CancellationVerifyRequest,
    service: Annotated[CancellationService, Depends(get_cancellation_service)],
) -> CancellationVerificationResponse:
    try:
        return service.verify_token(payload.token)
    except CancellationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail
        ) from exc
    except CancellationAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail
        ) from exc
    except CancellationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail
        ) from exc


@router.post(
    "/confirm",
    response_model=CancellationConfirmResponse,
    status_code=status.HTTP_200_OK,
)
def confirm_cancellation_by_body(
    payload: CancellationConfirmRequest,
    request: Request,
    service: Annotated[CancellationService, Depends(get_cancellation_service)],
) -> CancellationConfirmResponse:
    try:
        return service.confirm_cancellation(
            payload.token,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except CancellationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=exc.detail
        ) from exc
    except CancellationExpiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail=exc.detail
        ) from exc
    except CancellationAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail
        ) from exc
    except CancellationConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.detail
        ) from exc
    except CancellationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail
        ) from exc


def _client_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    candidate = request.client.host
    try:
        ip_address(candidate)
    except ValueError:
        return None
    return candidate

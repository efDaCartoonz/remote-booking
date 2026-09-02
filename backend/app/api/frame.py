from __future__ import annotations

from collections.abc import Callable
from ipaddress import ip_address
from typing import Annotated, TypeVar
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.cards.repository import CardRepository, PostgresCardRepository
from app.db import get_db
from app.frame.omnidesk import (
    OmnideskInvalidResponseError,
    OmnideskTicketClient,
    OmnideskTicketReopenError,
    OmnideskUnavailableError,
    get_omnidesk_ticket_client,
)
from app.frame.schemas import (
    FrameCardCreateRequest,
    FrameCardResponse,
    FrameCardsResponse,
    FrameSessionCreateRequest,
    FrameSessionResponse,
    frame_card_response,
    frame_cards_response,
    frame_session_response,
)
from app.frame.service import (
    FrameCardConflictError,
    FrameCardValidationError,
    FrameService,
    FrameSessionNotFoundError,
    FrameSessionOriginMismatchError,
    FrameTicketAccessError,
)
from app.frame.sessions import (
    FRAME_TOKEN_HEADER,
    FrameSession,
    FrameSessionStore,
    get_frame_session_store,
)

router = APIRouter(prefix="/api/v1/frame", tags=["frame"])
T = TypeVar("T")


def get_frame_card_repository(
    connection: Annotated[object, Depends(get_db)],
) -> CardRepository:
    return PostgresCardRepository(connection)


def get_frame_service(
    repository: Annotated[CardRepository, Depends(get_frame_card_repository)],
    session_store: Annotated[FrameSessionStore, Depends(get_frame_session_store)],
    omnidesk_client: Annotated[
        OmnideskTicketClient, Depends(get_omnidesk_ticket_client)
    ],
) -> FrameService:
    return FrameService(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )


def get_current_frame_session(
    request: Request,
    service: Annotated[FrameService, Depends(get_frame_service)],
    token: Annotated[str | None, Header(alias=FRAME_TOKEN_HEADER)] = None,
) -> FrameSession:
    try:
        return service.get_session(token, _frame_request_origin(request))
    except FrameSessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="frame_session_not_authenticated",
        ) from exc
    except FrameSessionOriginMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="frame_session_origin_mismatch",
        ) from exc


@router.post(
    "/sessions",
    response_model=FrameSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_frame_session(
    payload: FrameSessionCreateRequest,
    request: Request,
    service: Annotated[FrameService, Depends(get_frame_service)],
) -> FrameSessionResponse:
    created_session = _handle_ticket_check(
        lambda: service.create_session(
            omnidesk_ticket_number=payload.omnidesk_ticket_number,
            origin=_frame_request_origin(request),
        )
    )
    return frame_session_response(created_session.session, created_session.token)


@router.get("/cards", response_model=FrameCardsResponse)
def list_frame_cards(
    session: Annotated[FrameSession, Depends(get_current_frame_session)],
    service: Annotated[FrameService, Depends(get_frame_service)],
) -> FrameCardsResponse:
    cards = _handle_ticket_check(lambda: service.list_cards(session))
    return frame_cards_response(session, cards)


@router.post(
    "/cards", response_model=FrameCardResponse, status_code=status.HTTP_201_CREATED
)
def create_frame_card(
    payload: FrameCardCreateRequest,
    request: Request,
    session: Annotated[FrameSession, Depends(get_current_frame_session)],
    service: Annotated[FrameService, Depends(get_frame_service)],
) -> FrameCardResponse:
    try:
        card = service.create_card(
            session=session,
            payload=payload,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except FrameCardConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.detail
        ) from exc
    except FrameCardValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.detail
        ) from exc
    except FrameTicketAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail
        ) from exc
    except OmnideskUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.detail
        ) from exc
    except OmnideskInvalidResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.detail
        ) from exc
    except OmnideskTicketReopenError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=exc.detail
        ) from exc
    return frame_card_response(card)


def _handle_ticket_check(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except FrameTicketAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=exc.detail
        ) from exc
    except OmnideskUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.detail
        ) from exc
    except OmnideskInvalidResponseError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.detail
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


def _frame_request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin:
        return _normalize_origin(origin)
    referer = request.headers.get("referer")
    if referer:
        return _normalize_origin(referer)
    return None


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return value

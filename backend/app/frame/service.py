from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.cards.constants import ActorType, CreatedSource
from app.cards.repository import CardRecord, CardRepository, ClientSyncData
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.frame.omnidesk import (
    OmnideskTicket,
    OmnideskTicketClient,
    OmnideskTicketReopenError,
)
from app.frame.schemas import FrameCardCreateRequest
from app.frame.sessions import CreatedFrameSession, FrameSession, FrameSessionStore


class FrameSessionNotFoundError(Exception):
    pass


class FrameSessionOriginMismatchError(Exception):
    pass


class FrameTicketAccessError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class FrameCardConflictError(Exception):
    def __init__(self, detail: str = "active_card_exists_for_ticket") -> None:
        self.detail = detail
        super().__init__(detail)


class FrameCardValidationError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class FrameService:
    def __init__(
        self,
        *,
        repository: CardRepository,
        session_store: FrameSessionStore,
        omnidesk_client: OmnideskTicketClient,
    ) -> None:
        self.repository = repository
        self.session_store = session_store
        self.omnidesk_client = omnidesk_client
        self.card_service = CardService(repository)

    def create_session(
        self,
        *,
        omnidesk_ticket_number: str,
        origin: str | None,
    ) -> CreatedFrameSession:
        ticket = self._get_available_ticket(omnidesk_ticket_number)
        if not ticket.user_id:
            raise FrameTicketAccessError("ticket_client_missing")
        return self.session_store.create_session(
            omnidesk_ticket_number=ticket.number,
            omnidesk_user_id=ticket.user_id,
            omnidesk_company_id=ticket.company_id,
            origin=origin,
        )

    def get_session(
        self, token: str | None, request_origin: str | None
    ) -> FrameSession:
        if not token:
            raise FrameSessionNotFoundError
        session = self.session_store.get_session(token)
        if session is None:
            raise FrameSessionNotFoundError
        if session.origin and request_origin and request_origin != session.origin:
            raise FrameSessionOriginMismatchError
        return session

    def list_cards(self, session: FrameSession) -> list[CardRecord]:
        self._validate_current_ticket(session)
        return self.repository.list_cards_by_ticket(session.omnidesk_ticket_number)

    def create_card(
        self,
        *,
        session: FrameSession,
        payload: FrameCardCreateRequest,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        ticket = self._validate_current_ticket(session)
        ticket = self._ensure_ticket_open(ticket)
        self._validate_planning_window(payload.planned_start_at)

        if self.repository.has_active_card_for_ticket(ticket.number):
            raise FrameCardConflictError

        client = self.repository.get_or_create_client(
            ClientSyncData(
                omnidesk_user_id=ticket.user_id or session.omnidesk_user_id,
                omnidesk_company_id=ticket.company_id,
                display_name=ticket.client_display_name,
                preferred_contact_type_code=payload.client_contact_type_code,
                preferred_contact_value=payload.client_contact_value
                or ticket.client_contact_value,
                last_confirmed_timezone=payload.client_timezone_at_creation,
                timezone_source_code=payload.timezone_source_code,
            )
        )
        card_payload = CardCreateRequest(
            omnidesk_ticket_number=ticket.number,
            planned_start_at=payload.planned_start_at,
            planned_duration_minutes=payload.planned_duration_minutes,
            client_id=client.id,
            client_timezone_at_creation=payload.client_timezone_at_creation,
            timezone_source_code=payload.timezone_source_code,
            client_contact_type_code=payload.client_contact_type_code,
            client_contact_value=payload.client_contact_value
            or ticket.client_contact_value,
            description=payload.description,
        )
        return self.card_service.create_card(
            card_payload,
            actor_user_id=None,
            actor_type=ActorType.FRAME_CLIENT,
            created_source=CreatedSource.FRAME,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _validate_current_ticket(self, session: FrameSession) -> OmnideskTicket:
        ticket = self._get_available_ticket(session.omnidesk_ticket_number)
        if ticket.user_id != session.omnidesk_user_id:
            raise FrameTicketAccessError("ticket_client_mismatch")
        return ticket

    def _get_available_ticket(self, ticket_number: str) -> OmnideskTicket:
        ticket = self.omnidesk_client.get_ticket(ticket_number)
        if ticket is None or ticket.deleted or ticket.spam:
            raise FrameTicketAccessError("ticket_not_available")
        return ticket

    def _ensure_ticket_open(self, ticket: OmnideskTicket) -> OmnideskTicket:
        if ticket.status != "closed":
            return ticket
        self.omnidesk_client.reopen_ticket(ticket.number)
        reopened = self.omnidesk_client.get_ticket(ticket.number)
        if (
            reopened is None
            or reopened.deleted
            or reopened.spam
            or reopened.user_id != ticket.user_id
        ):
            raise FrameTicketAccessError("ticket_not_available")
        if reopened.status != "open":
            raise OmnideskTicketReopenError("omnidesk_ticket_not_open_after_reopen")
        return reopened

    def _validate_planning_window(self, planned_start_at: datetime) -> None:
        now = datetime.now(UTC)
        if planned_start_at < now + timedelta(minutes=120):
            raise FrameCardValidationError("planned_start_too_soon")
        if planned_start_at > now + timedelta(days=14):
            raise FrameCardValidationError("planned_start_too_far")

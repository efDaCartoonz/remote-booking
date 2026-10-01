from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.admin.planning_settings import get_planning_settings
from app.cards.constants import ActorType, CardStatus, CreatedSource
from app.cards.repository import CardRecord, CardRepository, ClientSyncData
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.frame.omnidesk import (
    OmnideskTicket,
    OmnideskTicketClient,
    OmnideskTicketClientChangedError,
    OmnideskTicketMismatchError,
    OmnideskTicketNotFoundError,
    OmnideskTicketReopenError,
    validate_ticket_response,
)
from app.cancellation.schemas import CancellationLinkResponse
from app.cancellation.service import CancellationService
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
        omnidesk_case_id: str,
        omnidesk_ticket_number: str,
        origin: str | None,
    ) -> CreatedFrameSession:
        ticket = self._get_available_ticket(omnidesk_case_id, omnidesk_ticket_number)
        if not ticket.user_id:
            raise FrameTicketAccessError("ticket_client_missing")
        return self.session_store.create_session(
            omnidesk_case_id=ticket.case_id,
            omnidesk_ticket_number=ticket.number,
            omnidesk_user_id=ticket.user_id,
            omnidesk_company_id=ticket.company_id,
            origin=origin,
            client_display_name=ticket.client_display_name,
            client_company_name=ticket.client_company_name,
            client_contact_value=ticket.client_contact_value,
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
        all_cards = self.repository.list_cards_by_ticket(session.omnidesk_ticket_number)
        can_create = not any(_is_active_card(card) for card in all_cards)

        client_ownership: dict[int, bool] = {}
        owned_cards: list[CardRecord] = []
        for card in all_cards:
            if card.client_id is None:
                continue
            if card.client_id not in client_ownership:
                client = self.repository.get_client_by_id(card.client_id)
                client_ownership[card.client_id] = (
                    client is not None
                    and client.omnidesk_user_id == session.omnidesk_user_id
                )
            if client_ownership[card.client_id]:
                owned_cards.append(card)

        class FrameCardList(list):
            can_create: bool

        result = FrameCardList(owned_cards)
        result.can_create = can_create
        return result

    def request_cancellation_link(
        self,
        *,
        session: FrameSession,
        card_id: UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> CancellationLinkResponse:
        self._validate_current_ticket(session)
        cancellation_service = CancellationService(
            repository=self.repository,
            card_service=self.card_service,
        )
        return cancellation_service.request_cancellation_link(
            session=session,
            card_id=card_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

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

        final_client_name = (
            payload.client_name.strip()
            if payload.client_name and payload.client_name.strip()
            else ticket.client_display_name
        )
        final_company_name = (
            payload.client_company_name.strip()
            if payload.client_company_name and payload.client_company_name.strip()
            else ticket.client_company_name
        )
        final_contact_value = (
            payload.client_contact_value.strip()
            if payload.client_contact_value and payload.client_contact_value.strip()
            else ticket.client_contact_value
        )

        client = self.repository.get_or_create_client(
            ClientSyncData(
                omnidesk_user_id=ticket.user_id or session.omnidesk_user_id,
                omnidesk_company_id=ticket.company_id,
                display_name=ticket.client_display_name,
                preferred_contact_type_code=payload.client_contact_type_code,
                preferred_contact_value=final_contact_value,
                last_confirmed_timezone=payload.client_timezone_at_creation,
                timezone_source_code=payload.timezone_source_code,
            )
        )
        card_payload = CardCreateRequest(
            omnidesk_ticket_number=ticket.number,
            planned_start_at=payload.planned_start_at,
            planned_duration_minutes=payload.planned_duration_minutes,
            client_id=client.id,
            client_name=final_client_name,
            client_company_name=final_company_name,
            client_timezone_at_creation=payload.client_timezone_at_creation,
            timezone_source_code=payload.timezone_source_code,
            client_contact_type_code=payload.client_contact_type_code,
            client_contact_value=final_contact_value,
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
        ticket = self._get_available_ticket(
            session.omnidesk_case_id, session.omnidesk_ticket_number
        )
        if (
            not ticket.user_id
            or not session.omnidesk_user_id
            or ticket.user_id != session.omnidesk_user_id
        ):
            raise FrameTicketAccessError("ticket_client_mismatch")
        return ticket

    def _get_available_ticket(self, case_id: str, ticket_number: str) -> OmnideskTicket:
        try:
            ticket = self.omnidesk_client.get_ticket_by_case_id(case_id)
        except OmnideskTicketNotFoundError as exc:
            raise FrameTicketAccessError("ticket_not_available") from exc
        except OmnideskTicketMismatchError as exc:
            raise FrameTicketAccessError(exc.detail) from exc
        if ticket is None:
            raise FrameTicketAccessError("ticket_not_available")
        try:
            return validate_ticket_response(
                ticket, case_id=case_id, case_number=ticket_number
            )
        except OmnideskTicketMismatchError as exc:
            raise FrameTicketAccessError(exc.detail) from exc
        except OmnideskTicketNotFoundError as exc:
            raise FrameTicketAccessError(exc.detail) from exc

    def _ensure_ticket_open(self, ticket: OmnideskTicket) -> OmnideskTicket:
        if ticket.status != "closed":
            return ticket
        self.omnidesk_client.reopen_ticket(ticket.case_id)
        try:
            reopened = self.omnidesk_client.get_ticket_by_case_id(ticket.case_id)
        except OmnideskTicketNotFoundError as exc:
            raise FrameTicketAccessError("ticket_not_available") from exc
        except OmnideskTicketMismatchError as exc:
            raise FrameTicketAccessError(exc.detail) from exc
        if reopened is None:
            raise FrameTicketAccessError("ticket_not_available")
        try:
            return validate_ticket_response(
                reopened,
                case_id=ticket.case_id,
                case_number=ticket.number,
                expected_user_id=ticket.user_id,
                require_open=True,
            )
        except (
            OmnideskTicketMismatchError,
            OmnideskTicketNotFoundError,
            OmnideskTicketClientChangedError,
        ) as exc:
            raise FrameTicketAccessError(
                getattr(exc, "detail", "ticket_not_available")
            ) from exc
        except OmnideskTicketReopenError:
            raise

    def _validate_planning_window(self, planned_start_at: datetime) -> None:
        connection = getattr(self.repository, "connection", None)
        planning = get_planning_settings(connection)
        now = datetime.now(UTC)
        if planned_start_at < now + timedelta(minutes=planning.min_lead_minutes):
            raise FrameCardValidationError("planned_start_too_soon")
        if planned_start_at > now + timedelta(days=planning.horizon_days):
            raise FrameCardValidationError("planned_start_too_far")


def _is_active_card(card: CardRecord) -> bool:
    return CardStatus(card.status_code) not in {
        CardStatus.COMPLETED,
        CardStatus.CANCELLED,
    }

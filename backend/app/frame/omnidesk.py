from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class OmnideskTicket:
    number: str
    user_id: str | None
    status: str
    deleted: bool = False
    spam: bool = False
    company_id: str | None = None
    client_display_name: str | None = None
    client_contact_value: str | None = None


class OmnideskTicketClient(Protocol):
    def get_ticket(self, ticket_number: str) -> OmnideskTicket | None: ...

    def reopen_ticket(self, ticket_number: str) -> OmnideskTicket: ...


class OmnideskUnavailableError(Exception):
    def __init__(self, detail: str = "omnidesk_unavailable") -> None:
        self.detail = detail
        super().__init__(detail)


class OmnideskTicketReopenError(Exception):
    def __init__(self, detail: str = "omnidesk_ticket_reopen_failed") -> None:
        self.detail = detail
        super().__init__(detail)


class NotConfiguredOmnideskTicketClient:
    def get_ticket(self, ticket_number: str) -> OmnideskTicket | None:
        raise OmnideskUnavailableError("omnidesk_client_not_configured")

    def reopen_ticket(self, ticket_number: str) -> OmnideskTicket:
        raise OmnideskUnavailableError("omnidesk_client_not_configured")


def get_omnidesk_ticket_client() -> OmnideskTicketClient:
    return NotConfiguredOmnideskTicketClient()

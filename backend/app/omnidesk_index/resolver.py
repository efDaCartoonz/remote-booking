from __future__ import annotations

from app.frame.omnidesk import (
    OmnideskTicket,
    OmnideskTicketClient,
    OmnideskTicketClientChangedError,
    OmnideskTicketMismatchError,
    OmnideskTicketNotFoundError,
    OmnideskTicketReopenError,
    validate_ticket_response,
)
from app.omnidesk_index.repository import (
    CaseIndexRepository,
    CaseIndexTicketAmbiguous,
    CaseIndexTicketNotFound,
)


class PublicTicketResolutionError(Exception):
    def __init__(self, *, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def resolve_ticket_by_case_number(
    connection, client: OmnideskTicketClient, case_number: str
) -> OmnideskTicket:
    """Resolve a public ticket number without exposing the internal case id."""
    try:
        case_id = CaseIndexRepository(connection).resolve_case_id(case_number)
    except CaseIndexTicketNotFound as exc:
        raise PublicTicketResolutionError(
            status_code=404, detail="omnidesk_ticket_not_found"
        ) from exc
    except CaseIndexTicketAmbiguous as exc:
        raise PublicTicketResolutionError(
            status_code=409, detail="omnidesk_ticket_ambiguous"
        ) from exc

    return resolve_ticket_by_case_id(client, case_id, case_number)


def resolve_ticket_by_case_id(
    client: OmnideskTicketClient, case_id: str, case_number: str
) -> OmnideskTicket:
    try:
        ticket = client.get_ticket_by_case_id(case_id)
        ticket = validate_ticket_response(
            ticket, case_id=case_id, case_number=case_number
        )
    except (OmnideskTicketNotFoundError, OmnideskTicketMismatchError) as exc:
        raise PublicTicketResolutionError(status_code=404, detail=exc.detail) from exc

    if ticket.status != "closed":
        return ticket

    original_user_id = ticket.user_id
    try:
        client.reopen_ticket(case_id)
        return validate_ticket_response(
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
        raise PublicTicketResolutionError(status_code=409, detail=exc.detail) from exc

from dataclasses import replace

import pytest
from app.api.manager import _manager_ticket
from app.frame.omnidesk import OmnideskTicket


class ReopenClient:
    def __init__(self, initial: OmnideskTicket, reopened: OmnideskTicket):
        self.initial = initial
        self.reopened = reopened
        self.reads = 0

    def get_ticket_by_case_id(self, case_id: str):
        self.reads += 1
        return self.initial if self.reads == 1 else self.reopened

    def reopen_ticket(self, case_id: str):
        return self.reopened


def ticket(**changes):
    values = {
        "case_id": "2000",
        "number": "123-456789",
        "user_id": "client-1",
        "status": "closed",
    }
    values.update(changes)
    return OmnideskTicket(
        **values,
    )


@pytest.mark.parametrize(
    "changes, expected",
    [
        ({"case_id": "other"}, "omnidesk_ticket_id_number_mismatch"),
        ({"number": "999-000001"}, "omnidesk_ticket_id_number_mismatch"),
        ({"deleted": True}, "ticket_not_available"),
        ({"spam": True}, "ticket_not_available"),
        ({"status": "closed"}, "omnidesk_ticket_not_open_after_reopen"),
        ({"user_id": "client-2", "status": "open"}, "omnidesk_ticket_client_changed"),
    ],
)
def test_manager_rejects_invalid_post_reopen_response(changes, expected) -> None:
    client = ReopenClient(ticket(), replace(ticket(), **changes))
    with pytest.raises(Exception) as error:
        _manager_ticket(client, "2000", "123-456789")
    assert getattr(error.value, "detail", None) == expected


def test_manager_accepts_valid_post_reopen_response() -> None:
    client = ReopenClient(ticket(), replace(ticket(), status="open"))
    result = _manager_ticket(client, "2000", "123-456789")
    assert result.status == "open"
    assert result.user_id == "client-1"

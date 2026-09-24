from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.frame.omnidesk import (
    HttpOmnideskTicketClient,
    OmnideskInvalidResponseError,
    OmnideskTicketMismatchError,
    OmnideskTicketNotFoundError,
    OmnideskTicketReopenError,
    OmnideskUnavailableError,
)


def make_omnidesk_case(
    *,
    case_id: int = 2000,
    case_number: str = "123-456789",
    status: str = "open",
    user_id: int = 123,
    deleted: bool = False,
    spam: bool = False,
) -> dict[str, object]:
    return {
        "case": {
            "case_id": case_id,
            "case_number": case_number,
            "subject": "Need help",
            "user_id": user_id,
            "status": status,
            "deleted": deleted,
            "spam": spam,
            "recipient": "client@example.test",
            "user_full_name": "Client Name",
            "user_company_id": 456,
            "category_id": 789,
            "case_type": "support",
        }
    }


def make_client(handler: httpx.MockTransport) -> HttpOmnideskTicketClient:
    http_client = httpx.Client(
        base_url="https://iridi.omnidesk.ru",
        auth=("staff@example.test", "test-api-key"),
        transport=handler,
    )
    return HttpOmnideskTicketClient(
        base_url="https://iridi.omnidesk.ru",
        staff_email="staff@example.test",
        api_key="test-api-key",
        timeout_seconds=2.5,
        http_client=http_client,
    )


def test_http_omnidesk_client_reads_ticket_by_case_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/cases/2000.json"
        assert request.url.query == b""
        expected_auth = base64.b64encode(b"staff@example.test:test-api-key").decode()
        assert request.headers["authorization"] == f"Basic {expected_auth}"
        return httpx.Response(
            200,
            json=make_omnidesk_case(),
            headers={"api_calls_left": "499"},
        )

    ticket = make_client(httpx.MockTransport(handler)).get_ticket_by_case_id("2000")

    assert ticket is not None
    assert ticket.case_id == "2000"
    assert ticket.number == "123-456789"
    assert ticket.user_id == "123"
    assert ticket.status == "open"
    assert ticket.deleted is False
    assert ticket.spam is False
    assert ticket.company_id == "456"
    assert ticket.client_display_name == "Client Name"
    assert ticket.client_contact_value == "client@example.test"
    assert ticket.case_type == "support"
    assert ticket.case_category == "789"


def test_http_omnidesk_client_raises_not_found_for_missing_ticket() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(OmnideskTicketNotFoundError):
        client.get_ticket_by_case_id("2000")


def test_http_omnidesk_client_rejects_case_id_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=make_omnidesk_case(case_id=9999))

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(
        OmnideskTicketMismatchError, match="omnidesk_ticket_case_id_mismatch"
    ):
        client.get_ticket_by_case_id("2000")


def test_http_omnidesk_client_rejects_invalid_ticket_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"case": {"case_number": "123-456789", "case_id": 2000}},
        )

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(OmnideskInvalidResponseError):
        client.get_ticket_by_case_id("2000")


def test_http_omnidesk_client_reports_unavailable_omnidesk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable", request=request)

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(OmnideskUnavailableError, match="omnidesk_unavailable"):
        client.get_ticket_by_case_id("2000")


def test_http_omnidesk_client_reopens_closed_ticket() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "PUT"
        assert request.url.path == "/api/cases/2000.json"
        assert json.loads(request.content) == {"case": {"status": "open"}}
        return httpx.Response(200, json=make_omnidesk_case(status="open"))

    ticket = make_client(httpx.MockTransport(handler)).reopen_ticket("2000")

    assert [request.method for request in requests] == ["PUT"]
    assert ticket.status == "open"


def test_http_omnidesk_client_reports_reopen_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "cannot reopen"})

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(OmnideskTicketReopenError):
        client.reopen_ticket("2000")


def test_http_omnidesk_client_assigns_staff() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "PUT"
        assert request.url.path == "/api/cases/2000.json"
        assert json.loads(request.content) == {"case": {"staff_id": 42}}
        expected_auth = base64.b64encode(b"staff@example.test:test-api-key").decode()
        assert request.headers["authorization"] == f"Basic {expected_auth}"
        return httpx.Response(200, json={"case": {"case_id": 2000, "staff_id": 42}})

    client = make_client(httpx.MockTransport(handler))
    client.assign_staff("2000", 42)

    assert len(requests) == 1


def test_http_omnidesk_client_adds_internal_note_with_staff_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/cases/2000/messages.json"
        assert json.loads(request.content) == {
            "message": {
                "content": "Work completed successfully",
                "note": True,
                "staff_id": 42,
            }
        }
        return httpx.Response(
            201,
            json={
                "message": {
                    "message_id": 501,
                    "content": "Work completed successfully",
                    "note": True,
                    "staff_id": 42,
                }
            },
        )

    client = make_client(httpx.MockTransport(handler))
    client.add_internal_note("2000", "Work completed successfully", staff_id=42)

    assert len(requests) == 1


def test_http_omnidesk_client_adds_internal_note_without_staff_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/cases/2000/messages.json"
        assert json.loads(request.content) == {
            "message": {
                "content": "Automated internal note",
                "note": True,
            }
        }
        return httpx.Response(
            201,
            json={
                "message": {
                    "message_id": 502,
                    "content": "Automated internal note",
                    "note": True,
                }
            },
        )

    client = make_client(httpx.MockTransport(handler))
    client.add_internal_note("2000", "Automated internal note", staff_id=None)

    assert len(requests) == 1


def test_http_omnidesk_client_sends_public_message_with_staff_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/cases/2000/messages.json"
        assert json.loads(request.content) == {
            "message": {
                "content": "Your booking is scheduled",
                "note": False,
                "staff_id": 42,
            }
        }
        return httpx.Response(
            201,
            json={
                "message": {
                    "message_id": 503,
                    "content": "Your booking is scheduled",
                    "note": False,
                    "staff_id": 42,
                }
            },
        )

    client = make_client(httpx.MockTransport(handler))
    client.send_public_message("2000", "Your booking is scheduled", staff_id=42)

    assert len(requests) == 1


def test_http_omnidesk_client_sends_public_message_without_staff_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/cases/2000/messages.json"
        assert json.loads(request.content) == {
            "message": {
                "content": "Reminder: session in 15 minutes",
                "note": False,
            }
        }
        return httpx.Response(
            201,
            json={
                "message": {
                    "message_id": 504,
                    "content": "Reminder: session in 15 minutes",
                    "note": False,
                }
            },
        )

    client = make_client(httpx.MockTransport(handler))
    client.send_public_message("2000", "Reminder: session in 15 minutes", staff_id=None)

    assert len(requests) == 1


def test_http_omnidesk_client_write_methods_handle_errors() -> None:
    def handler_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "case not found"})

    client_404 = make_client(httpx.MockTransport(handler_404))
    with pytest.raises(OmnideskTicketNotFoundError):
        client_404.assign_staff("2000", 42)
    with pytest.raises(OmnideskTicketNotFoundError):
        client_404.add_internal_note("2000", "note")
    with pytest.raises(OmnideskTicketNotFoundError):
        client_404.send_public_message("2000", "msg")

    def handler_429(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limit reached"})

    client_429 = make_client(httpx.MockTransport(handler_429))
    with pytest.raises(OmnideskUnavailableError, match="omnidesk_rate_limited"):
        client_429.assign_staff("2000", 42)
    with pytest.raises(OmnideskUnavailableError, match="omnidesk_rate_limited"):
        client_429.add_internal_note("2000", "note")
    with pytest.raises(OmnideskUnavailableError, match="omnidesk_rate_limited"):
        client_429.send_public_message("2000", "msg")

    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal error"})

    client_500 = make_client(httpx.MockTransport(handler_500))
    with pytest.raises(OmnideskUnavailableError, match="omnidesk_unavailable"):
        client_500.assign_staff("2000", 42)

    def handler_400(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid parameter"})

    client_400 = make_client(httpx.MockTransport(handler_400))
    with pytest.raises(OmnideskInvalidResponseError, match="omnidesk_http_error"):
        client_400.assign_staff("2000", 42)

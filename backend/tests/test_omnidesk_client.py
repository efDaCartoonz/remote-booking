from __future__ import annotations

import base64
import json

import httpx
import pytest
from app.frame.omnidesk import (
    HttpOmnideskTicketClient,
    OmnideskInvalidResponseError,
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


def test_http_omnidesk_client_reads_ticket_by_number() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/cases.json"
        assert request.url.params["case_number"] == "^123-456789^"
        assert request.url.params["limit"] == "10"
        expected_auth = base64.b64encode(b"staff@example.test:test-api-key").decode()
        assert request.headers["authorization"] == f"Basic {expected_auth}"
        return httpx.Response(
            200,
            json={
                "0": make_omnidesk_case(case_number="000-000001"),
                "1": make_omnidesk_case(),
                "total_count": 2,
            },
            headers={"api_calls_left": "499"},
        )

    ticket = make_client(httpx.MockTransport(handler)).get_ticket("123-456789")

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
        return httpx.Response(200, json={"total_count": 0})

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(OmnideskTicketNotFoundError):
        client.get_ticket("123-456789")


def test_http_omnidesk_client_rejects_invalid_ticket_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"0": {"case": {"case_number": "123-456789", "case_id": 2000}}},
        )

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(OmnideskInvalidResponseError):
        client.get_ticket("123-456789")


def test_http_omnidesk_client_reports_unavailable_omnidesk() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable", request=request)

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(OmnideskUnavailableError, match="omnidesk_unavailable"):
        client.get_ticket("123-456789")


def test_http_omnidesk_client_reopens_closed_ticket() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"0": make_omnidesk_case(status="closed"), "total_count": 1},
            )
        assert request.method == "PUT"
        assert request.url.path == "/api/cases/2000.json"
        assert json.loads(request.content) == {"case": {"status": "open"}}
        return httpx.Response(200, json=make_omnidesk_case(status="open"))

    ticket = make_client(httpx.MockTransport(handler)).reopen_ticket("123-456789")

    assert [request.method for request in requests] == ["GET", "PUT"]
    assert ticket.status == "open"


def test_http_omnidesk_client_reports_reopen_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"0": make_omnidesk_case(status="closed"), "total_count": 1},
            )
        return httpx.Response(400, json={"error": "cannot reopen"})

    client = make_client(httpx.MockTransport(handler))

    with pytest.raises(OmnideskTicketReopenError):
        client.reopen_ticket("123-456789")

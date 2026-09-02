from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.core.config import settings


@dataclass(frozen=True)
class OmnideskTicket:
    number: str
    user_id: str | None
    status: str
    case_id: str | None = None
    deleted: bool = False
    spam: bool = False
    company_id: str | None = None
    client_display_name: str | None = None
    client_contact_value: str | None = None
    case_type: str | None = None
    case_category: str | None = None


class OmnideskTicketClient(Protocol):
    def get_ticket_by_id(
        self, case_id: str, ticket_number: str
    ) -> OmnideskTicket | None: ...

    def reopen_ticket(self, case_id: str, ticket_number: str) -> OmnideskTicket: ...


class OmnideskUnavailableError(Exception):
    def __init__(self, detail: str = "omnidesk_unavailable") -> None:
        self.detail = detail
        super().__init__(detail)


class OmnideskInvalidResponseError(Exception):
    def __init__(self, detail: str = "omnidesk_invalid_response") -> None:
        self.detail = detail
        super().__init__(detail)


class OmnideskTicketNotFoundError(Exception):
    def __init__(self, detail: str = "omnidesk_ticket_not_found") -> None:
        self.detail = detail
        super().__init__(detail)


class OmnideskTicketMismatchError(Exception):
    def __init__(self, detail: str = "omnidesk_ticket_id_number_mismatch") -> None:
        self.detail = detail
        super().__init__(detail)


class OmnideskTicketReopenError(Exception):
    def __init__(self, detail: str = "omnidesk_ticket_reopen_failed") -> None:
        self.detail = detail
        super().__init__(detail)


class HttpOmnideskTicketClient:
    def __init__(
        self,
        *,
        base_url: str,
        staff_email: str,
        api_key: str,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            auth=(staff_email, api_key),
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def get_ticket_by_id(
        self, case_id: str, ticket_number: str
    ) -> OmnideskTicket | None:
        payload = self._request(
            "GET",
            f"/api/cases/{quote(case_id, safe='')}.json",
        )
        ticket = _parse_ticket(payload.get("case"))
        if ticket.number != ticket_number:
            raise OmnideskTicketMismatchError
        return ticket

    def reopen_ticket(self, case_id: str, ticket_number: str) -> OmnideskTicket:
        try:
            payload = self._request(
                "PUT",
                f"/api/cases/{quote(case_id, safe='')}.json",
                json={"case": {"status": "open"}},
            )
            reopened = _parse_ticket(payload.get("case"))
        except OmnideskUnavailableError:
            raise
        except (
            OmnideskInvalidResponseError,
            OmnideskTicketMismatchError,
            OmnideskTicketNotFoundError,
        ) as exc:
            raise OmnideskTicketReopenError from exc

        if reopened.case_id != case_id:
            raise OmnideskTicketReopenError("omnidesk_reopened_ticket_id_mismatch")
        if reopened.number != ticket_number:
            raise OmnideskTicketReopenError("omnidesk_reopened_ticket_mismatch")
        if reopened.status != "open":
            raise OmnideskTicketReopenError("omnidesk_ticket_not_open_after_reopen")
        return reopened

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise OmnideskUnavailableError("omnidesk_timeout") from exc
        except httpx.RequestError as exc:
            raise OmnideskUnavailableError from exc

        if response.status_code == 404:
            raise OmnideskTicketNotFoundError
        if response.status_code in {401, 403}:
            raise OmnideskUnavailableError("omnidesk_auth_failed")
        if response.status_code == 429:
            raise OmnideskUnavailableError("omnidesk_rate_limited")
        if response.status_code >= 500:
            raise OmnideskUnavailableError
        if response.status_code >= 400:
            raise OmnideskInvalidResponseError("omnidesk_http_error")

        try:
            payload = response.json()
        except ValueError as exc:
            raise OmnideskInvalidResponseError from exc
        if not isinstance(payload, dict):
            raise OmnideskInvalidResponseError
        return payload


class NotConfiguredOmnideskTicketClient:
    def get_ticket_by_id(
        self, case_id: str, ticket_number: str
    ) -> OmnideskTicket | None:
        raise OmnideskUnavailableError("omnidesk_client_not_configured")

    def reopen_ticket(self, case_id: str, ticket_number: str) -> OmnideskTicket:
        raise OmnideskUnavailableError("omnidesk_client_not_configured")


def get_omnidesk_ticket_client() -> OmnideskTicketClient:
    if settings.omnidesk_api_key and settings.omnidesk_staff_email:
        return HttpOmnideskTicketClient(
            base_url=settings.omnidesk_base_url,
            staff_email=settings.omnidesk_staff_email,
            api_key=settings.omnidesk_api_key,
            timeout_seconds=settings.omnidesk_timeout_seconds,
        )
    return NotConfiguredOmnideskTicketClient()


def _parse_ticket(case: Any) -> OmnideskTicket:
    if not isinstance(case, dict):
        raise OmnideskInvalidResponseError

    number = _required_string(case.get("case_number"))
    status = _required_string(case.get("status"))
    case_id = _required_string(case.get("case_id"))

    return OmnideskTicket(
        number=number,
        case_id=case_id,
        user_id=_optional_string(case.get("user_id")),
        status=status,
        deleted=_optional_bool(case.get("deleted")),
        spam=_optional_bool(case.get("spam")),
        company_id=_first_string(
            case,
            ("company_id", "user_company_id", "customer_company_id"),
        ),
        client_display_name=_first_string(
            case,
            (
                "user_full_name",
                "user_name",
                "customer_full_name",
                "company_name",
                "user_company_name",
            ),
        ),
        client_contact_value=_first_string(
            case,
            (
                "user_email",
                "user_phone",
                "user_whatsapp_phone",
                "recipient",
            ),
        ),
        case_type=_first_string(case, ("type", "case_type")),
        case_category=_first_string(case, ("category", "category_id", "case_category")),
    )


def _required_string(value: Any) -> str:
    string_value = _optional_string(value)
    if string_value is None:
        raise OmnideskInvalidResponseError
    return string_value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, int):
        return str(value)
    return None


def _first_string(data: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _optional_string(data.get(key))
        if value is not None:
            return value
    return None


def _optional_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False

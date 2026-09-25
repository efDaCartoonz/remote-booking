from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
import pytest

from app.integrations.omnidesk_outbox import (
    OutboxIntent,
    SuppressedIntent,
    _process_intent,
)


class FakeOmnideskTicketClient:
    def __init__(self) -> None:
        self.public_messages: list[tuple[str, str, int | None]] = []
        self.internal_notes: list[tuple[str, str, int | None]] = []

    def send_public_message(
        self, case_id: str, content: str, staff_id: int | None = None
    ) -> None:
        self.public_messages.append((case_id, content, staff_id))

    def add_internal_note(
        self, case_id: str, content: str, staff_id: int | None = None
    ) -> None:
        self.internal_notes.append((case_id, content, staff_id))


class FakeTicket:
    def __init__(self, case_id: str = "case_100") -> None:
        self.case_id = case_id


class FakeOutboxRepository:
    def __init__(
        self,
        card_info: dict[str, Any] | None = None,
        staff_id: int | None = None,
    ) -> None:
        self.card_info = card_info or {
            "status_code": 6,
            "planned_start_at": datetime.now(UTC),
            "public_notification_enabled": True,
            "cancellation_public_notification_enabled": True,
        }
        self.staff_id = staff_id

    def resolve_ticket(self, client: Any, ticket_number: str) -> Any:
        return FakeTicket(f"case_{ticket_number}")

    def get_card_info(self, card_id: int) -> dict[str, Any] | None:
        return self.card_info

    def get_user_omnidesk_staff_id(self, user_id: int) -> int | None:
        return self.staff_id


def test_process_intent_cancellation_public_notification_success() -> None:
    repo = FakeOutboxRepository(
        card_info={
            "status_code": 6,
            "cancellation_public_notification_enabled": True,
        }
    )
    client = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456",
        action_type="cancellation_public_notification",
        payload={"content": "Заявка на удаленное подключение отменена."},
        attempts=0,
    )

    _process_intent(repo, client, intent)

    assert len(client.public_messages) == 1
    case_id, content, staff_id = client.public_messages[0]
    assert case_id == "case_123-456"
    assert content == "Заявка на удаленное подключение отменена."
    assert staff_id is None


def test_process_intent_cancellation_public_notification_suppressed_when_disabled() -> (
    None
):
    repo = FakeOutboxRepository(
        card_info={
            "status_code": 6,
            "cancellation_public_notification_enabled": False,
        }
    )
    client = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456",
        action_type="cancellation_public_notification",
        payload={"content": "Заявка отменена."},
        attempts=0,
    )

    with pytest.raises(SuppressedIntent) as exc_info:
        _process_intent(repo, client, intent)

    assert str(exc_info.value) == "cancellation_public_notification_disabled"
    assert len(client.public_messages) == 0


def test_process_intent_cancellation_public_notification_suppressed_when_card_not_cancelled() -> (
    None
):
    repo = FakeOutboxRepository(
        card_info={
            "status_code": 2,  # Confirmed, not cancelled
            "cancellation_public_notification_enabled": True,
        }
    )
    client = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456",
        action_type="cancellation_public_notification",
        payload={"content": "Заявка отменена."},
        attempts=0,
    )

    with pytest.raises(SuppressedIntent) as exc_info:
        _process_intent(repo, client, intent)

    assert str(exc_info.value) == "cancellation_public_notification_card_not_cancelled"
    assert len(client.public_messages) == 0


def test_process_intent_cancellation_public_notification_independent_of_15min_setting() -> (
    None
):
    # 15-minute public warning is disabled, but cancellation notification is enabled
    repo = FakeOutboxRepository(
        card_info={
            "status_code": 6,
            "public_notification_enabled": False,
            "cancellation_public_notification_enabled": True,
        }
    )
    client = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456",
        action_type="cancellation_public_notification",
        payload={"content": "Заявка на удаленное подключение отменена."},
        attempts=0,
    )

    _process_intent(repo, client, intent)
    assert len(client.public_messages) == 1
    assert client.public_messages[0][1] == "Заявка на удаленное подключение отменена."


def test_process_intent_cancellation_public_notification_suppressed_when_content_missing() -> (
    None
):
    repo = FakeOutboxRepository(
        card_info={
            "status_code": 6,
            "cancellation_public_notification_enabled": True,
        }
    )
    client = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456",
        action_type="cancellation_public_notification",
        payload={},
        attempts=0,
    )

    with pytest.raises(SuppressedIntent) as exc_info:
        _process_intent(repo, client, intent)

    assert str(exc_info.value) == "cancellation_public_notification_content_missing"
    assert len(client.public_messages) == 0

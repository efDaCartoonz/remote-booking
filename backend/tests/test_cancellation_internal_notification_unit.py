from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.assignments.manager_escalation import ManagerRecipient
from app.cards.constants import ActorType, CardEventType, CardStatus
from app.cards.repository import CardRecord
from app.cards.service import CardService
from app.notifications import (
    NOTIFICATION_EVENT_CODES,
    NotificationIntent,
    RecordingNotificationService,
    _render_message,
)


class FakeCardRepository:
    def __init__(self, card: CardRecord, managers: list[ManagerRecipient]) -> None:
        self.card = card
        self.managers = managers
        self.card_events: list[dict[str, Any]] = []

    def get_card_by_public_id_for_update(self, public_id: UUID) -> CardRecord | None:
        if self.card.public_id == public_id:
            return self.card
        return None

    def get_current_assignment_cycle_for_update(self, card_id: int) -> Any | None:
        return None

    def update_card_status(self, public_id: UUID, data: Any) -> CardRecord | None:
        if self.card.public_id == public_id:
            self.card = replace(self.card, status_code=int(data.status))
            return self.card
        return None

    def add_card_event(
        self,
        *,
        card_id: int,
        event_type: CardEventType,
        actor_user_id: int | None,
        actor_type: ActorType,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        comment: str | None = None,
    ) -> int:
        self.card_events.append(
            {
                "card_id": card_id,
                "event_type": event_type,
                "actor_user_id": actor_user_id,
            }
        )
        return len(self.card_events)

    def add_audit_log(self, **kwargs: Any) -> None:
        pass

    def list_active_manager_recipients(self) -> list[ManagerRecipient]:
        return self.managers


def _make_card(
    *,
    card_id: int = 1,
    public_id: UUID | None = None,
    l1_owner_id: int | None = 101,
    l2_engineer_id: int | None = 201,
) -> CardRecord:
    return CardRecord(
        id=card_id,
        public_id=public_id or uuid4(),
        number="RDM-1001",
        omnidesk_ticket_number="123-456789",
        client_id=1,
        status_code=int(CardStatus.CONFIRMED),
        criticality_code=1,
        urgency_code=1,
        planned_start_at=datetime.now(UTC),
        planned_duration_minutes=30,
        client_timezone_at_creation="UTC",
        timezone_source_code=1,
        actual_start_at=None,
        actual_end_at=None,
        l1_owner_id=l1_owner_id,
        l2_engineer_id=l2_engineer_id,
        assignment_method_code=1,
        unsuccessful_cycle_count=0,
        client_contact_type_code=1,
        client_contact_value="test@example.com",
        description="Test card",
        urgent_reason=None,
        out_of_hours_flag=False,
        retroactive_flag=False,
        overdue_flag=False,
        result_code=None,
        engineer_report=None,
        created_source_code=1,
        created_by_id=101,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_card_cancellation_notifies_active_managers_l1_and_l2() -> None:
    card = _make_card(l1_owner_id=101, l2_engineer_id=201)
    managers = [
        ManagerRecipient(
            user_id=301, telegram_chat_id="tg301", bitrix24_user_id="bx301"
        ),
        ManagerRecipient(
            user_id=302, telegram_chat_id="tg302", bitrix24_user_id="bx302"
        ),
    ]
    repo = FakeCardRepository(card, managers)
    notifications = RecordingNotificationService()
    service = CardService(repository=repo, notifications=notifications)

    cancelled = service.cancel_card(
        card.public_id,
        actor_user_id=301,
        comment="Cancelled by manager",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert cancelled.status_code == int(CardStatus.CANCELLED)
    recipients = [
        (n.recipient_user_id, n.channel, n.payload["assignment"])
        for n in notifications.notifications
    ]

    # Managers (301, 302) x 2 channels
    assert (301, "telegram", "manager_escalation") in recipients
    assert (301, "bitrix24", "manager_escalation") in recipients
    assert (302, "telegram", "manager_escalation") in recipients
    assert (302, "bitrix24", "manager_escalation") in recipients

    # L1 (101) x 2 channels
    assert (101, "telegram", "l1") in recipients
    assert (101, "bitrix24", "l1") in recipients

    # L2 (201) x 2 channels
    assert (201, "telegram", "l2") in recipients
    assert (201, "bitrix24", "l2") in recipients

    for n in notifications.notifications:
        assert n.event == "card_cancelled"


def test_card_cancellation_deduplicates_when_manager_is_also_l1() -> None:
    # User 101 is both L1 owner AND an active manager
    card = _make_card(l1_owner_id=101, l2_engineer_id=None)
    managers = [
        ManagerRecipient(
            user_id=101, telegram_chat_id="tg101", bitrix24_user_id="bx101"
        ),
    ]
    repo = FakeCardRepository(card, managers)
    notifications = RecordingNotificationService()
    service = CardService(repository=repo, notifications=notifications)

    service.cancel_card(
        card.public_id,
        actor_user_id=101,
        comment="Cancelled",
        ip_address=None,
        user_agent=None,
    )

    user_101_notifications = [
        n for n in notifications.notifications if n.recipient_user_id == 101
    ]
    # RecordingNotificationService deduplicates by dedupe_key (event:source_id:user_id:channel)
    # So user 101 receives exactly 1 telegram and 1 bitrix24 notification
    assert len(user_101_notifications) == 2
    channels = {n.channel for n in user_101_notifications}
    assert channels == {"telegram", "bitrix24"}


def test_card_cancelled_event_rendering_and_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert NOTIFICATION_EVENT_CODES["card_cancelled"] == 9
    monkeypatch.setattr(
        "app.notifications.settings.notification_card_base_url",
        "https://rdm.example.test",
    )

    intent = NotificationIntent(
        id=42,
        card_id=10,
        recipient_user_id=101,
        channel_code=0,
        event_type_code=9,
        attempts=1,
        locked_at=datetime.now(UTC),
        recipient="tg_12345",
        card_number="RDM-500",
        card_public_id="00000000-0000-0000-0000-000000000001",
        omnidesk_ticket_number="TK-999",
        client_display_name="Acme Corp",
        planned_start_at=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        planned_duration_minutes=45,
        recipient_timezone="UTC",
    )

    rendered = _render_message(intent)
    assert "отменена" in rendered
    assert "RDM-500" in rendered
    assert "TK-999" in rendered
    assert "/cards/00000000-0000-0000-0000-000000000001" in rendered

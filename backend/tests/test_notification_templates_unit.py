from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.cards.constants import CardStatus
from app.notifications import (
    NOTIFICATION_CHANNEL_CODES,
    NOTIFICATION_EVENT_CODES,
    NotificationIntent,
    PermanentDeliveryError,
    _render_message,
    deliver_pending_notifications,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


class StubAdapter:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, str, str]] = []

    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        self.sent_messages.append((recipient, text, idempotency_key))


class InMemoryRuntimeRepository:
    def __init__(
        self,
        intents: list[NotificationIntent],
        templates: dict[str, str | None] | None = None,
        raise_on_template: bool = False,
    ) -> None:
        self.intents = list(intents)
        self.templates = templates or {}
        self.raise_on_template = raise_on_template
        self.sent: list[int] = []
        self.failed: list[tuple[int, str]] = []

    def recover_stale_locks(self, *, now: datetime, max_attempts: int) -> int:
        return 0

    def claim_one(
        self, *, now: datetime, max_attempts: int
    ) -> NotificationIntent | None:
        if not self.intents:
            return None
        return self.intents.pop(0)

    def mark_sent(self, intent: NotificationIntent) -> None:
        self.sent.append(intent.id)

    def mark_retry(
        self, intent: NotificationIntent, *, reason: str, next_attempt_at: datetime
    ) -> None:
        pass

    def mark_failed(self, intent: NotificationIntent, *, reason: str) -> None:
        self.failed.append((intent.id, reason))

    def get_notification_template(self, code: str) -> str | None:
        if self.raise_on_template:
            raise RuntimeError("database_connection_lost")
        return self.templates.get(code)


@pytest.fixture(autouse=True)
def notification_settings(monkeypatch: pytest.MonkeyPatch):
    values = SimpleNamespace(
        notification_max_attempts=3,
        notification_retry_seconds=60,
        notification_lock_seconds=300,
        notification_http_timeout_seconds=5.0,
        notification_card_base_url="https://rdm.example.com",
        telegram_bot_token="test-token",
        telegram_api_url="https://telegram.example",
        bitrix24_bot_webhook_url="https://bitrix.example/hook",
        bitrix24_bot_id="bot-1",
        bitrix24_bot_client_id="client-1",
    )
    monkeypatch.setattr("app.notifications.settings", values)
    return values


def make_test_intent(
    *,
    event_type_code: int = NOTIFICATION_EVENT_CODES["l1_followup"],
    channel_code: int = NOTIFICATION_CHANNEL_CODES["telegram"],
    with_client: bool = True,
    with_timestamp: bool = True,
    card_status_code: int = int(CardStatus.CONFIRMED),
    source_event_comment: str | None = None,
    recipient_timezone: str = "Europe/Moscow",
) -> NotificationIntent:
    return NotificationIntent(
        id=42,
        card_id=10,
        recipient_user_id=101,
        channel_code=channel_code,
        event_type_code=event_type_code,
        attempts=1,
        locked_at=NOW,
        recipient="test_recipient",
        card_number="RDM-000042",
        card_public_id="00000000-0000-0000-0000-000000000042",
        omnidesk_ticket_number="123-456789",
        client_display_name="ООО Ромашка" if with_client else None,
        planned_start_at=NOW if with_timestamp else None,
        planned_duration_minutes=45,
        recipient_timezone=recipient_timezone,
        card_status_code=card_status_code,
        source_event_comment=source_event_comment,
    )


# ---------------------------------------------------------------------------
# 1. Default template reproduction test for all 8 events
# ---------------------------------------------------------------------------


def test_default_templates_match_previous_text_for_all_events() -> None:
    # 1. l1_followup
    intent_l1_followup = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["l1_followup"]
    )
    text = _render_message(intent_l1_followup)
    expected_l1_followup = (
        "Карточка RDM-000042; клиент ООО Ромашка; тикет 123-456789; "
        "25.09.2026 15:00; 45 мин. https://rdm.example.com/cards/00000000-0000-0000-0000-000000000042"
    )
    assert text == expected_l1_followup

    # 2. l2_reminder
    intent_l2_reminder = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["l2_reminder"]
    )
    assert _render_message(intent_l2_reminder) == expected_l1_followup

    # 3. l1_reminder
    intent_l1_reminder = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["l1_reminder"]
    )
    assert _render_message(intent_l1_reminder) == expected_l1_followup

    # 4. urgent_collision
    intent_collision = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["urgent_collision"]
    )
    assert _render_message(intent_collision) == expected_l1_followup

    # 5. card_ended_automatically
    intent_ended = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["card_ended_automatically"]
    )
    assert _render_message(intent_ended) == expected_l1_followup

    # 6. omnidesk_staff_mapping_missing
    intent_mapping = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["omnidesk_staff_mapping_missing"]
    )
    expected_mapping = (
        "Не настроена связь исполнителя RDM с сотрудником Omnidesk. "
        "Проверьте назначение в карточке RDM-000042; клиент ООО Ромашка; тикет 123-456789; "
        "25.09.2026 15:00; 45 мин. https://rdm.example.com/cards/00000000-0000-0000-0000-000000000042"
    )
    assert _render_message(intent_mapping) == expected_mapping

    # 7. card_cancelled
    intent_cancelled = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["card_cancelled"]
    )
    expected_cancelled = (
        "Карточка RDM-000042 отменена; клиент ООО Ромашка; тикет 123-456789; "
        "25.09.2026 15:00; 45 мин. https://rdm.example.com/cards/00000000-0000-0000-0000-000000000042"
    )
    assert _render_message(intent_cancelled) == expected_cancelled

    # 8. manager_escalation
    intent_manager = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["manager_escalation"],
        card_status_code=int(CardStatus.REJECTED),
        source_event_comment="all_l2_candidates_rejected: busy on site",
    )
    expected_manager = (
        "Карточка RDM-000042; клиент ООО Ромашка; тикет 123-456789; 25.09.2026 15:00; "
        "45 мин; причина: все кандидаты L2 отказались (busy on site); "
        "текущий статус: Отклонено; действие: проверьте отказ и назначьте L2 или согласуйте новое время. "
        "https://rdm.example.com/cards/00000000-0000-0000-0000-000000000042"
    )
    assert _render_message(intent_manager) == expected_manager


def test_rendering_without_client_and_without_timestamp() -> None:
    intent = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["card_cancelled"],
        with_client=False,
        with_timestamp=False,
    )
    text = _render_message(intent)
    assert "Карточка RDM-000042 отменена;" in text
    assert "; клиент" not in text
    assert "не указано" in text


# ---------------------------------------------------------------------------
# 2. Custom template application
# ---------------------------------------------------------------------------


def test_custom_template_applied_via_parameter_and_dict() -> None:
    intent = make_test_intent(event_type_code=NOTIFICATION_EVENT_CODES["l1_followup"])

    # Via custom_template parameter
    custom_str = (
        "Внимание! Заявка {card_number} (тикет {ticket}) начнется в {timestamp}. {url}"
    )
    rendered_custom = _render_message(intent, custom_template=custom_str)
    assert rendered_custom == (
        "Внимание! Заявка RDM-000042 (тикет 123-456789) начнется в 25.09.2026 15:00. "
        "https://rdm.example.com/cards/00000000-0000-0000-0000-000000000042"
    )

    # Via templates dict with code key
    templates_dict = {
        "l1_followup.telegram": "Telegram: {card_number} {duration} мин",
    }
    rendered_dict = _render_message(intent, templates=templates_dict)
    assert rendered_dict == "Telegram: RDM-000042 45 мин"


# ---------------------------------------------------------------------------
# 3. Fallback on unknown placeholders, invalid syntax, empty templates, security
# ---------------------------------------------------------------------------


def test_fallback_to_default_on_unknown_placeholder() -> None:
    intent = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["card_cancelled"]
    )
    bad_template = "Отмена карточки {card_number} с секретом {unknown_secret_param}"

    rendered = _render_message(intent, custom_template=bad_template)
    # Should safely fallback to default template
    assert rendered.startswith("Карточка RDM-000042 отменена")
    assert "unknown_secret_param" not in rendered


def test_fallback_to_default_on_syntax_error() -> None:
    intent = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["card_cancelled"]
    )
    bad_template = "Текст {card_number не закрыт"

    rendered = _render_message(intent, custom_template=bad_template)
    assert rendered.startswith("Карточка RDM-000042 отменена")


def test_fallback_to_default_on_blank_template() -> None:
    intent = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["card_cancelled"]
    )

    assert _render_message(intent, custom_template="").startswith(
        "Карточка RDM-000042 отменена"
    )
    assert _render_message(intent, custom_template="   \n  ").startswith(
        "Карточка RDM-000042 отменена"
    )


def test_security_attribute_and_item_access_prevented() -> None:
    intent = make_test_intent(event_type_code=NOTIFICATION_EVENT_CODES["l1_followup"])

    # Attempt attribute traversal
    attr_template = "Хак {card_number.__class__} {url}"
    rendered_attr = _render_message(intent, custom_template=attr_template)
    assert rendered_attr.startswith("Карточка RDM-000042")
    assert "class" not in rendered_attr

    # Attempt item indexing
    item_template = "Хак {card_number[0]} {url}"
    rendered_item = _render_message(intent, custom_template=item_template)
    assert rendered_item.startswith("Карточка RDM-000042")


def test_missing_card_base_url_raises_permanent_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.notifications.settings.notification_card_base_url", "")
    intent = make_test_intent()
    with pytest.raises(
        PermanentDeliveryError, match="^notification_card_url_not_configured$"
    ):
        _render_message(intent)


# ---------------------------------------------------------------------------
# 4. Runtime Delivery integration with repository template lookup
# ---------------------------------------------------------------------------


def test_deliver_pending_notifications_uses_repository_template() -> None:
    intent = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["card_cancelled"]
    )
    custom_tpl = "Рантайм-шаблон: {card_number}{client_suffix} | {url}"
    repo = InMemoryRuntimeRepository(
        [intent],
        templates={"card_cancelled.telegram": custom_tpl},
    )
    adapter = StubAdapter()

    delivered_count = deliver_pending_notifications(
        repo, {NOTIFICATION_CHANNEL_CODES["telegram"]: adapter}, now=NOW
    )

    assert delivered_count == 1
    assert len(adapter.sent_messages) == 1
    _, delivered_text, _ = adapter.sent_messages[0]
    assert delivered_text == (
        "Рантайм-шаблон: RDM-000042; клиент ООО Ромашка | "
        "https://rdm.example.com/cards/00000000-0000-0000-0000-000000000042"
    )


def test_deliver_pending_notifications_falls_back_when_template_lookup_fails() -> None:
    intent = make_test_intent(
        event_type_code=NOTIFICATION_EVENT_CODES["card_cancelled"]
    )
    # Repository raises exception on template retrieval
    repo = InMemoryRuntimeRepository([intent], raise_on_template=True)
    adapter = StubAdapter()

    delivered_count = deliver_pending_notifications(
        repo, {NOTIFICATION_CHANNEL_CODES["telegram"]: adapter}, now=NOW
    )

    assert delivered_count == 1
    assert len(adapter.sent_messages) == 1
    _, delivered_text, _ = adapter.sent_messages[0]
    # Successfully sent default template
    assert delivered_text.startswith("Карточка RDM-000042 отменена")

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.notifications import (
    Bitrix24Adapter,
    NotificationIntent,
    PermanentDeliveryError,
    TelegramAdapter,
    TemporaryDeliveryError,
    deliver_pending_notifications,
)
from app.worker import celery_app

NOW = datetime(2026, 9, 4, 10, tzinfo=UTC)


@dataclass
class FakeRuntimeRepository:
    intents: list[NotificationIntent]

    def __post_init__(self) -> None:
        self.recoveries: list[tuple[datetime, int]] = []
        self.claims: list[int] = []
        self.sent: list[int] = []
        self.retries: list[tuple[int, str, datetime]] = []
        self.failed: list[tuple[int, str]] = []

    def recover_stale_locks(self, *, now: datetime, max_attempts: int) -> int:
        self.recoveries.append((now, max_attempts))
        return 0

    def claim_one(
        self, *, now: datetime, max_attempts: int
    ) -> NotificationIntent | None:
        if not self.intents:
            return None
        intent = self.intents.pop(0)
        self.claims.append(intent.id)
        return intent

    def mark_sent(self, intent: NotificationIntent) -> None:
        self.sent.append(intent.id)

    def mark_retry(
        self, intent: NotificationIntent, *, reason: str, next_attempt_at: datetime
    ) -> None:
        self.retries.append((intent.id, reason, next_attempt_at))

    def mark_failed(self, intent: NotificationIntent, *, reason: str) -> None:
        self.failed.append((intent.id, reason))


class RecordingAdapter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        self.calls.append((recipient, text, idempotency_key))
        if self.error is not None:
            raise self.error


@pytest.fixture(autouse=True)
def notification_settings(monkeypatch):
    values = SimpleNamespace(
        notification_max_attempts=3,
        notification_retry_seconds=60,
        notification_lock_seconds=300,
        notification_http_timeout_seconds=5.0,
        notification_card_base_url="https://rdm.example",
        telegram_bot_token="test-token",
        telegram_api_url="https://telegram.example",
        bitrix24_bot_webhook_url="https://bitrix.example/hook",
    )
    monkeypatch.setattr("app.notifications.settings", values)
    return values


def make_intent(
    *,
    intent_id: int = 1,
    channel_code: int = 0,
    attempts: int = 1,
    recipient: str | None = "recipient-1",
) -> NotificationIntent:
    return NotificationIntent(
        id=intent_id,
        card_id=10,
        recipient_user_id=20,
        channel_code=channel_code,
        attempts=attempts,
        locked_at=NOW,
        recipient=recipient,
        card_number="RDM-000010",
        card_public_id="00000000-0000-0000-0000-000000000010",
        omnidesk_ticket_number="999-000010",
        client_display_name="Test Client",
        planned_start_at=NOW,
        planned_duration_minutes=60,
        recipient_timezone="UTC",
    )


@pytest.mark.parametrize(
    ("adapter", "recipient", "expected_url", "expects_idempotency_key"),
    [
        (
            TelegramAdapter(),
            "telegram-chat",
            "https://telegram.example/bottest-token/sendMessage",
            False,
        ),
        (Bitrix24Adapter(), "bitrix-user", "https://bitrix.example/hook", True),
    ],
)
def test_channel_adapters_deliver_with_timeout_and_mocked_http(
    monkeypatch, adapter, recipient, expected_url, expects_idempotency_key
) -> None:
    calls = []
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **kwargs: (
            calls.append((url, kwargs)) or SimpleNamespace(status_code=200)
        ),
    )

    adapter.send(recipient=recipient, text="safe message", idempotency_key="rdm:1")

    assert calls[0][0] == expected_url
    assert calls[0][1]["timeout"] == 5.0
    if expects_idempotency_key:
        assert "rdm:1" in calls[0][1]["json"].values()


def test_temporary_error_schedules_retry_without_persisting_external_detail() -> None:
    repository = FakeRuntimeRepository([make_intent()])
    adapter = RecordingAdapter(TemporaryDeliveryError("telegram_unavailable"))

    assert deliver_pending_notifications(repository, {0: adapter}, now=NOW) == 0

    assert repository.retries == [
        (1, "telegram_unavailable", NOW + timedelta(seconds=60))
    ]
    assert repository.failed == []


def test_permanent_error_is_terminal() -> None:
    repository = FakeRuntimeRepository([make_intent()])
    adapter = RecordingAdapter(PermanentDeliveryError("telegram_rejected"))

    deliver_pending_notifications(repository, {0: adapter}, now=NOW)

    assert repository.failed == [(1, "telegram_rejected")]
    assert repository.retries == []


def test_third_temporary_failure_is_terminal() -> None:
    repository = FakeRuntimeRepository([make_intent(attempts=3)])
    adapter = RecordingAdapter(TemporaryDeliveryError("bitrix24_temporary_error"))

    deliver_pending_notifications(repository, {0: adapter}, now=NOW)

    assert repository.failed == [(1, "bitrix24_temporary_error")]


def test_delivered_or_empty_queue_is_not_processed_again() -> None:
    repository = FakeRuntimeRepository([make_intent()])
    adapter = RecordingAdapter()

    assert deliver_pending_notifications(repository, {0: adapter}, now=NOW) == 1
    assert deliver_pending_notifications(repository, {0: adapter}, now=NOW) == 0
    assert repository.sent == [1]
    assert len(adapter.calls) == 1


def test_missing_recipient_never_calls_adapter() -> None:
    repository = FakeRuntimeRepository([make_intent(recipient=None)])
    adapter = RecordingAdapter()

    deliver_pending_notifications(repository, {0: adapter}, now=NOW)

    assert adapter.calls == []
    assert repository.failed == [(1, "notification_recipient_not_configured")]


def test_unsupported_channel_never_calls_adapter() -> None:
    repository = FakeRuntimeRepository([make_intent(channel_code=99)])

    deliver_pending_notifications(repository, {}, now=NOW)

    assert repository.failed == [(1, "unsupported_notification_channel")]


def test_recovery_runs_before_claim_and_two_workers_claim_once() -> None:
    repository = FakeRuntimeRepository([make_intent()])
    adapter = RecordingAdapter()

    assert deliver_pending_notifications(repository, {0: adapter}, now=NOW) == 1
    assert deliver_pending_notifications(repository, {0: adapter}, now=NOW) == 0

    assert repository.recoveries == [(NOW, 3), (NOW, 3)]
    assert repository.claims == [1]
    assert repository.sent == [1]


def test_safe_adapter_errors_hide_transport_detail(monkeypatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            httpx.RequestError("token=must-not-be-exposed")
        ),
    )

    with pytest.raises(TemporaryDeliveryError, match="^telegram_unavailable$"):
        TelegramAdapter().send(recipient="recipient", text="x", idempotency_key="x")


def test_missing_channel_configuration_is_permanent(notification_settings) -> None:
    notification_settings.telegram_bot_token = ""

    with pytest.raises(PermanentDeliveryError, match="^telegram_not_configured$"):
        TelegramAdapter().send(recipient="recipient", text="x", idempotency_key="x")


def test_worker_registers_notification_task_and_periodic_schedule() -> None:
    entry = celery_app.conf.beat_schedule["deliver-notifications"]

    assert entry["task"] == "app.worker.deliver_notifications"
    assert entry["options"]["queue"] == "notifications"
    assert celery_app.tasks["app.worker.deliver_notifications"]


def test_message_is_minimal_and_contains_card_link() -> None:
    repository = FakeRuntimeRepository([make_intent()])
    adapter = RecordingAdapter()

    deliver_pending_notifications(repository, {0: adapter}, now=NOW)

    _, message, _ = adapter.calls[0]
    assert "RDM-000010" in message
    assert "999-000010" in message
    assert "Test Client" in message
    assert "https://rdm.example/cards/" in message
    assert "recipient-1" not in message

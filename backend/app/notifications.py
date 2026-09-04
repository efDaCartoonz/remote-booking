from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
import json
import logging
from datetime import datetime, timezone, timedelta

import httpx

import psycopg
from app.core.config import settings

logger = logging.getLogger(__name__)
PENDING, SENT, FAILED, CANCELLED = 0, 1, 2, 3

NOTIFICATION_EVENT_CODES = {"l1_followup": 3}
NOTIFICATION_CHANNEL_CODES = {"telegram": 0, "bitrix24": 1}


@dataclass(frozen=True)
class Notification:
    event: str
    card_id: int
    recipient_user_id: int
    channel: str


class NotificationService(Protocol):
    def notify(
        self, *, event: str, card_id: int, recipient_user_id: int, channel: str
    ) -> None: ...


class RecordingNotificationService:
    """MVP adapter: records intent, never performs external delivery."""

    def __init__(self) -> None:
        self.notifications: list[Notification] = []
        self._keys: set[tuple[str, int, int, str]] = set()

    def notify(
        self, *, event: str, card_id: int, recipient_user_id: int, channel: str
    ) -> None:
        key = (event, card_id, recipient_user_id, channel)
        if key not in self._keys:
            self._keys.add(key)
            self.notifications.append(
                Notification(event, card_id, recipient_user_id, channel)
            )


class PostgresNotificationService:
    """Persists idempotent internal-delivery intents for a later runtime adapter."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def notify(
        self, *, event: str, card_id: int, recipient_user_id: int, channel: str
    ) -> None:
        event_code = NOTIFICATION_EVENT_CODES[event]
        channel_code = NOTIFICATION_CHANNEL_CODES[channel]
        setting_column = {
            "telegram": "notify_telegram",
            "bitrix24": "notify_bitrix24",
        }[channel]
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO notifications (
                    card_id,
                    recipient_user_id,
                    channel_code,
                    event_type_code
                )
                SELECT %(card_id)s, %(recipient_user_id)s, %(channel_code)s, %(event_code)s
                WHERE COALESCE(
                    (
                        SELECT {setting_column}
                        FROM user_settings
                        WHERE user_id = %(recipient_user_id)s
                    ),
                    true
                )
                ON CONFLICT (card_id, recipient_user_id, channel_code, event_type_code)
                DO NOTHING
                """,
                {
                    "card_id": card_id,
                    "recipient_user_id": recipient_user_id,
                    "channel_code": channel_code,
                    "event_code": event_code,
                },
            )


class TemporaryDeliveryError(Exception):
    pass


class PermanentDeliveryError(Exception):
    pass


class ChannelAdapter(Protocol):
    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None: ...


class TelegramAdapter:
    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        if not settings.telegram_bot_token or not recipient:
            raise PermanentDeliveryError("telegram_not_configured")
        try:
            response = httpx.post(
                f"{settings.telegram_api_url.rstrip('/')}/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": recipient, "text": text}, timeout=5,
            )
        except httpx.RequestError as exc:
            raise TemporaryDeliveryError("telegram_unavailable") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TemporaryDeliveryError("telegram_temporary_error")
        if response.status_code >= 400:
            raise PermanentDeliveryError("telegram_rejected")


class Bitrix24Adapter:
    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        if not settings.bitrix24_webhook_url or not recipient:
            raise PermanentDeliveryError("bitrix24_not_configured")
        try:
            response = httpx.post(settings.bitrix24_webhook_url, json={
                "USER_ID": recipient, "MESSAGE": text, "MESSAGE_ID": idempotency_key,
            }, timeout=5)
        except httpx.RequestError as exc:
            raise TemporaryDeliveryError("bitrix24_unavailable") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise TemporaryDeliveryError("bitrix24_temporary_error")
        if response.status_code >= 400:
            raise PermanentDeliveryError("bitrix24_rejected")


def deliver_notification_intent(connection: psycopg.Connection, adapters: dict[int, ChannelAdapter]) -> bool:
    """Claim one intent atomically, deliver it, and persist only safe diagnostics."""
    with connection.cursor() as cursor:
        cursor.execute("""UPDATE notifications SET locked_at = now(), attempts = attempts + 1
            WHERE id = (SELECT id FROM notifications WHERE status_code = 0
              AND (scheduled_at IS NULL OR scheduled_at <= now())
              AND (next_attempt_at IS NULL OR next_attempt_at <= now())
              AND attempts < %(max)s AND locked_at IS NULL ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING id, card_id, recipient_user_id, channel_code, payload, attempts""", {"max": settings.notification_max_attempts})
        row = cursor.fetchone()
        connection.commit()
    if not row:
        return False
    intent_id, card_id, recipient_id, channel, payload, attempts = row
    data: dict[str, Any] = payload if isinstance(payload, dict) else json.loads(payload or "{}")
    text = str(data.get("message", "notification"))[:4000]
    recipient = str(data.get("recipient") or recipient_id or "")
    try:
        adapters[channel].send(recipient=recipient, text=text, idempotency_key=f"rdm-notification:{intent_id}")
    except PermanentDeliveryError as exc:
        _finish_notification(connection, intent_id, FAILED, str(exc), attempts)
    except TemporaryDeliveryError as exc:
        if attempts >= settings.notification_max_attempts:
            _finish_notification(connection, intent_id, FAILED, str(exc), attempts)
        else:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE notifications SET locked_at = NULL, next_attempt_at = %s, error_message = %s WHERE id = %s",
                               (datetime.now(timezone.utc) + timedelta(seconds=settings.notification_retry_seconds * attempts), str(exc), intent_id))
            connection.commit()
    else:
        _finish_notification(connection, intent_id, SENT, None, attempts)
    return True


def _finish_notification(connection: psycopg.Connection, intent_id: int, status: int, error: str | None, attempts: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute("UPDATE notifications SET status_code = %s, sent_at = CASE WHEN %s = 1 THEN now() ELSE sent_at END, locked_at = NULL, error_message = %s WHERE id = %s",
                       (status, status, error, intent_id))
        cursor.execute(
            "INSERT INTO audit_log (actor_type_code, action_code, entity_type, entity_id, new_values) VALUES (2, 30, 'notification', %s, %s)",
            (intent_id, json.dumps({"status": "sent" if status == SENT else "failed", "attempts": attempts, "reason": error})),
        )
    connection.commit()

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import psycopg

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

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Notification:
    event: str
    card_id: int
    recipient_user_id: int
    channel: str


class NotificationService(Protocol):
    def notify(self, *, event: str, card_id: int, recipient_user_id: int, channel: str) -> None: ...


class RecordingNotificationService:
    """MVP adapter: records intent, never performs external delivery."""

    def __init__(self) -> None:
        self.notifications: list[Notification] = []
        self._keys: set[tuple[str, int, int, str]] = set()

    def notify(self, *, event: str, card_id: int, recipient_user_id: int, channel: str) -> None:
        key = (event, card_id, recipient_user_id, channel)
        if key not in self._keys:
            self._keys.add(key)
            self.notifications.append(Notification(event, card_id, recipient_user_id, channel))

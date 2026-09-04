from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.notifications import NotificationService


@dataclass(frozen=True)
class DueReminder:
    id: int
    card_id: int
    kind: str
    owner_id: int | None
    anchor_at: datetime
    interval_seconds: int
    escalation_after_count: int
    last_count: int
    escalation_sent: bool


class ReminderRepository(Protocol):
    def claim_due(self, *, now: datetime, limit: int) -> list[DueReminder]: ...
    def current(self, reminder: DueReminder) -> bool: ...
    def record_timer_event(self, *, card_id: int, kind: str, count: int) -> int: ...
    def recipients(self, *, user_id: int) -> list[tuple[str, str]]: ...
    def managers(self) -> list[tuple[int, str, str]]: ...
    def advance(self, *, reminder_id: int, next_due_at: datetime, count: int, escalation_sent: bool, close: bool = False) -> None: ...


class ReminderService:
    def __init__(self, repository: ReminderRepository, notifications: NotificationService):
        self.repository = repository
        self.notifications = notifications

    def scan(self, *, now: datetime, batch_size: int) -> int:
        now = now.astimezone(UTC)
        created = 0
        for reminder in self.repository.claim_due(now=now, limit=min(max(batch_size, 1), 500)):
            if not self.repository.current(reminder):
                self.repository.advance(reminder_id=reminder.id, next_due_at=now, count=reminder.last_count, escalation_sent=reminder.escalation_sent, close=True)
                continue
            elapsed = max(0, int((now - reminder.anchor_at.astimezone(UTC)).total_seconds()))
            count = max(reminder.last_count + 1, elapsed // reminder.interval_seconds)
            event_id = self.repository.record_timer_event(card_id=reminder.card_id, kind=reminder.kind, count=count)
            for channel, _ in self.repository.recipients(user_id=reminder.owner_id or 0):
                if self.notifications.notify(event=reminder.kind, card_id=reminder.card_id, source_event_id=event_id, source_event_type=4, recipient_user_id=reminder.owner_id or 0, channel=channel, payload={"card_id": reminder.card_id, "assignment": "l2" if reminder.kind == "l2_reminder" else "l1"}):
                    created += 1
            escalate = reminder.kind == "l2_reminder" and count >= reminder.escalation_after_count and not reminder.escalation_sent
            if escalate:
                for manager_id, channel, _ in self.repository.managers():
                    if self.notifications.notify(event="manager_escalation", card_id=reminder.card_id, source_event_id=event_id, source_event_type=4, recipient_user_id=manager_id, channel=channel, payload={"card_id": reminder.card_id, "assignment": "manager_escalation"}):
                        created += 1
            self.repository.advance(reminder_id=reminder.id, next_due_at=reminder.anchor_at + timedelta(seconds=(count + 1) * reminder.interval_seconds), count=count, escalation_sent=reminder.escalation_sent or escalate)
        return created

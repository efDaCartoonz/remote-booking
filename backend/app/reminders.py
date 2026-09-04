from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from contextlib import nullcontext
from typing import Protocol

from app.notifications import NotificationService
from psycopg.types.json import Jsonb


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
    last_escalated_at: datetime | None = None
    manager_repeat_seconds: int = 1800


class ReminderRepository(Protocol):
    def claim_due(self, *, now: datetime, limit: int) -> list[DueReminder]: ...
    def current(self, reminder: DueReminder) -> bool: ...
    def record_timer_event(self, *, card_id: int, kind: str, count: int) -> int: ...
    def recipients(self, *, user_id: int) -> list[tuple[str, str]]: ...
    def managers(self) -> list[tuple[int, str, str]]: ...
    def advance(self, *, reminder_id: int, next_due_at: datetime, count: int, escalation_sent: bool, last_escalated_at: datetime | None = None, close: bool = False) -> None: ...


class ReminderService:
    def __init__(self, repository: ReminderRepository, notifications: NotificationService):
        self.repository = repository
        self.notifications = notifications

    def scan(self, *, now: datetime, batch_size: int) -> int:
        now = now.astimezone(UTC)
        created = 0
        for reminder in self.repository.claim_due(now=now, limit=min(max(batch_size, 1), 500)):
            try:
                with getattr(self.repository, "savepoint", nullcontext)():
                    created += self._scan_one(reminder, now)
            except Exception as exc:
                logger.warning("reminder_scan_failed id=%s error=%s", reminder.id, type(exc).__name__)
        return created

    def _scan_one(self, reminder: DueReminder, now: datetime) -> int:
            created = 0
            if not self.repository.current(reminder):
                self.repository.advance(reminder_id=reminder.id, next_due_at=now, count=reminder.last_count, escalation_sent=reminder.escalation_sent, close=True)
                return 0
            elapsed = max(0, int((now - reminder.anchor_at.astimezone(UTC)).total_seconds()))
            count = max(reminder.last_count + 1, elapsed // reminder.interval_seconds)
            event_id = self.repository.record_timer_event(card_id=reminder.card_id, kind=reminder.kind, count=count)
            for channel, _ in self.repository.recipients(user_id=reminder.owner_id or 0):
                if self.notifications.notify(event=reminder.kind, card_id=reminder.card_id, source_event_id=event_id, source_event_type=4, recipient_user_id=reminder.owner_id or 0, channel=channel, payload={"card_id": reminder.card_id, "assignment": "l2" if reminder.kind == "l2_reminder" else "l1"}):
                    created += 1
            escalate = count >= reminder.escalation_after_count and (
                (reminder.kind == "l2_reminder" and not reminder.escalation_sent)
                or (reminder.kind == "l1_reminder" and (not reminder.escalation_sent or reminder.last_escalated_at is None or now >= reminder.last_escalated_at + timedelta(seconds=reminder.manager_repeat_seconds)))
            )
            if escalate:
                for manager_id, channel, _ in self.repository.managers():
                    if self.notifications.notify(event="manager_escalation", card_id=reminder.card_id, source_event_id=event_id, source_event_type=4, recipient_user_id=manager_id, channel=channel, payload={"card_id": reminder.card_id, "assignment": "manager_escalation"}):
                        created += 1
            self.repository.advance(reminder_id=reminder.id, next_due_at=reminder.anchor_at + timedelta(seconds=(count + 1) * reminder.interval_seconds), count=count, escalation_sent=reminder.escalation_sent or escalate, last_escalated_at=now if escalate else reminder.last_escalated_at)
            return created


class PostgresReminderRepository:
    def __init__(self, connection):
        self.connection = connection

    def savepoint(self):
        """An inner transaction becomes a PostgreSQL savepoint inside scanner batch."""
        return self.connection.transaction()

    def claim_due(self, *, now: datetime, limit: int) -> list[DueReminder]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT id, card_id, kind, owner_id, anchor_at, interval_seconds, escalation_after_count, last_count, escalation_sent, last_escalated_at, COALESCE((settings_snapshot->>'manager_repeat_seconds')::integer, 1800) AS manager_repeat_seconds FROM reminder_schedules WHERE closed_at IS NULL AND next_due_at <= %(now)s ORDER BY next_due_at, id LIMIT %(limit)s FOR UPDATE SKIP LOCKED", {"now": now, "limit": min(max(limit, 1), 500)})
            return [DueReminder(**dict(row)) for row in cursor.fetchall()]

    def current(self, reminder: DueReminder) -> bool:
        with self.connection.cursor() as cursor:
            if reminder.kind == "l2_reminder":
                cursor.execute("UPDATE connection_cards SET overdue_at = now() WHERE id = %(card)s AND status_code = 1 AND overdue_at IS NULL AND planned_start_at + planned_duration_minutes * interval '1 minute' <= now() RETURNING id", {"card": reminder.card_id})
                if cursor.fetchone() is not None:
                    cursor.execute("INSERT INTO card_events (card_id, event_type_code, actor_type_code, new_values, comment) VALUES (%(card)s, 4, 2, %(values)s, 'l2_overdue')", {"card": reminder.card_id, "values": Jsonb({"overdue": True})})
                    cursor.execute("INSERT INTO audit_log (actor_type_code, action_code, entity_type, entity_id, new_values) VALUES (2, 1, 'connection_card', %(card)s, %(values)s)", {"card": reminder.card_id, "values": Jsonb({"overdue": True})})
                    return False
            cursor.execute("SELECT EXISTS (SELECT 1 FROM connection_cards c WHERE c.id = %(card)s AND c.status_code IN (1, 4) AND ((%(kind)s = 'l2_reminder' AND c.l2_engineer_id = %(owner)s) OR (%(kind)s = 'l1_reminder' AND c.l1_owner_id = %(owner)s))) AS active", {"card": reminder.card_id, "kind": reminder.kind, "owner": reminder.owner_id})
            return bool(cursor.fetchone()["active"])

    def record_timer_event(self, *, card_id: int, kind: str, count: int) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute("INSERT INTO card_events (card_id, event_type_code, actor_type_code, new_values, comment) VALUES (%(card)s, 4, 2, %(values)s, %(comment)s) RETURNING id", {"card": card_id, "values": Jsonb({"timer": kind, "count": count}), "comment": "timer_reminder"})
            event_id = int(cursor.fetchone()["id"])
            cursor.execute("INSERT INTO audit_log (actor_type_code, action_code, entity_type, entity_id, new_values) VALUES (2, 1, 'reminder_schedule', %(card)s, %(values)s)", {"card": card_id, "values": Jsonb({"kind": kind, "count": count, "event_id": event_id})})
            return event_id

    def recipients(self, *, user_id: int) -> list[tuple[str, str]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT telegram_chat_id, bitrix24_user_id FROM user_settings WHERE user_id = %(id)s", {"id": user_id})
            row = cursor.fetchone()
        return [(channel, value) for channel, value in (("telegram", row["telegram_chat_id"]), ("bitrix24", row["bitrix24_user_id"])) if value] if row else []

    def managers(self) -> list[tuple[int, str, str]]:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT u.id, us.telegram_chat_id, us.bitrix24_user_id FROM users u JOIN user_roles ur ON ur.user_id=u.id AND ur.role_id=3 LEFT JOIN user_settings us ON us.user_id=u.id WHERE u.is_active ORDER BY u.id")
            return [(r["id"], channel, value) for r in cursor.fetchall() for channel, value in (("telegram", r["telegram_chat_id"]), ("bitrix24", r["bitrix24_user_id"])) if value]

    def advance(self, *, reminder_id: int, next_due_at: datetime, count: int, escalation_sent: bool, last_escalated_at: datetime | None = None, close: bool = False) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE reminder_schedules SET next_due_at=%(due)s, last_count=%(count)s, escalation_sent=%(sent)s, last_escalated_at=%(last)s, closed_at=CASE WHEN %(close)s THEN now() ELSE closed_at END WHERE id=%(id)s AND closed_at IS NULL", {"id": reminder_id, "due": next_due_at, "count": count, "sent": escalation_sent, "last": last_escalated_at, "close": close})
logger = logging.getLogger(__name__)

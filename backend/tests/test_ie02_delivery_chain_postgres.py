"""IE-02 end-to-end reminder-to-delivery coverage on isolated PostgreSQL."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.core.config import settings
from app.notifications import (
    PostgresNotificationRuntimeRepository,
    PostgresNotificationService,
    TemporaryDeliveryError,
    deliver_pending_notifications,
)
from app.reminders import PostgresReminderRepository, ReminderService


pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1",
    reason="PostgreSQL integration runner only",
)


def _database_url() -> str:
    return (
        os.getenv("PSYCOPG_DATABASE_URL")
        or os.environ.get("DATABASE_URL", "").replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        or "postgresql://nimda:nimda@postgres:5432/rdm"
    )


class _FailOnceAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        self.calls.append((recipient, text, idempotency_key))
        if len(self.calls) == 1:
            raise TemporaryDeliveryError("delivery_timeout")


def test_ie02_reminder_persists_and_delivers_exact_postgres_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    token = uuid.uuid4().hex
    username = f"ie02-chain-{token}"
    card_number = f"IE02-{token}"
    ticket_number = f"927-{int(token[:6], 16) % 1_000_000:06d}"
    connection: psycopg.Connection | None = None
    recipient_id: int | None = None
    card_id: int | None = None
    source_event_id: int | None = None
    dedupe_key: str | None = None

    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
        now = datetime.now(UTC)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) "
                "VALUES (%s, 'integration-test', 'IE-02 chain test') RETURNING id",
                (username,),
            )
            recipient_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, timezone) "
                "VALUES (%s, %s, 'UTC')",
                (recipient_id, f"chat-{token}"),
            )
            cursor.execute(
                """INSERT INTO connection_cards (
                       number, omnidesk_ticket_number, status_code, planned_start_at,
                       planned_duration_minutes, l1_owner_id
                   ) VALUES (%s, %s, 1, %s, 60, %s) RETURNING id""",
                (card_number, ticket_number, now + timedelta(days=1), recipient_id),
            )
            card_id = cursor.fetchone()["id"]
            cursor.execute(
                """INSERT INTO reminder_schedules (
                       card_id, kind, owner_id, anchor_at, interval_seconds,
                       escalation_after_count, next_due_at, settings_snapshot
                   ) VALUES (%s, 'l1_reminder', %s, %s, 60, 10, %s, %s)
                   """,
                (
                    card_id,
                    recipient_id,
                    now - timedelta(minutes=2),
                    now - timedelta(seconds=1),
                    Jsonb({"l1_mode": "post_informed"}),
                ),
            )
        connection.commit()

        scanner = ReminderService(
            PostgresReminderRepository(connection),
            PostgresNotificationService(connection),
        )
        assert scanner.scan(now=now, batch_size=1) == 1
        assert scanner.scan(now=now, batch_size=1) == 0

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, event_type_code, comment FROM card_events "
                "WHERE card_id = %s AND comment = 'timer_reminder'",
                (card_id,),
            )
            event = cursor.fetchone()
            assert event is not None
            source_event_id = event["id"]
            assert event["event_type_code"] == 4
            dedupe_key = (
                f"notification:l1_reminder:{source_event_id}:{recipient_id}:telegram"
            )
            cursor.execute(
                "SELECT id, card_id, recipient_user_id, channel_code, event_type_code, "
                "source_event_id, source_event_type_code, status_code "
                "FROM notifications WHERE dedupe_key = %s",
                (dedupe_key,),
            )
            notification = cursor.fetchone()
            assert notification is not None
            assert notification["card_id"] == card_id
            assert notification["recipient_user_id"] == recipient_id
            assert notification["channel_code"] == 0
            assert notification["event_type_code"] == 5
            assert notification["source_event_id"] == source_event_id
            assert notification["source_event_type_code"] == 4
            assert notification["status_code"] == 0
            notification_id = notification["id"]

        monkeypatch.setattr(
            settings, "notification_card_base_url", "https://rdm.invalid"
        )
        adapter = _FailOnceAdapter()
        runtime = PostgresNotificationRuntimeRepository(connection)
        assert deliver_pending_notifications(runtime, {0: adapter}, now=now) == 0
        assert len(adapter.calls) == 1
        assert adapter.calls[0][0] == f"chat-{token}"
        assert adapter.calls[0][2] == f"rdm-notification:{notification_id}"

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status_code, attempts, locked_at, next_attempt_at, error_message "
                "FROM notifications WHERE id = %s",
                (notification_id,),
            )
            retry_state = cursor.fetchone()
            assert retry_state["status_code"] == 0
            assert retry_state["attempts"] == 1
            assert retry_state["locked_at"] is None
            assert retry_state["next_attempt_at"] > now
            assert retry_state["error_message"] == "delivery_timeout"
            cursor.execute(
                "SELECT status_code FROM connection_cards WHERE id = %s", (card_id,)
            )
            assert cursor.fetchone()["status_code"] == 1

        assert (
            deliver_pending_notifications(
                runtime,
                {0: adapter},
                now=retry_state["next_attempt_at"] - timedelta(microseconds=1),
            )
            == 0
        )
        assert len(adapter.calls) == 1
        assert (
            deliver_pending_notifications(
                runtime, {0: adapter}, now=retry_state["next_attempt_at"]
            )
            == 1
        )
        assert len(adapter.calls) == 2
        assert adapter.calls[1][2] == f"rdm-notification:{notification_id}"

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status_code, attempts, locked_at, next_attempt_at, error_message "
                "FROM notifications WHERE id = %s",
                (notification_id,),
            )
            assert dict(cursor.fetchone()) == {
                "status_code": 1,
                "attempts": 2,
                "locked_at": None,
                "next_attempt_at": None,
                "error_message": None,
            }
            cursor.execute(
                "SELECT new_values->>'status' AS status, "
                "new_values->>'attempts' AS attempts, "
                "new_values->>'reason' AS reason FROM audit_log "
                "WHERE entity_type = 'notification' AND entity_id = %s ORDER BY id",
                (notification_id,),
            )
            assert [dict(row) for row in cursor.fetchall()] == [
                {"status": "pending", "attempts": "1", "reason": "delivery_timeout"},
                {"status": "sent", "attempts": "2", "reason": None},
            ]
            cursor.execute(
                "SELECT status_code FROM connection_cards WHERE id = %s", (card_id,)
            )
            assert cursor.fetchone()["status_code"] == 1
    finally:
        if connection is not None:
            connection.close()
        # The runtime commits independently; cleanup follows only generated keys.
        with psycopg.connect(database_url) as cleanup, cleanup.cursor() as cursor:
            if dedupe_key is not None:
                cursor.execute(
                    "DELETE FROM audit_log WHERE entity_type = 'notification' "
                    "AND entity_id IN (SELECT id FROM notifications WHERE dedupe_key = %s)",
                    (dedupe_key,),
                )
                cursor.execute(
                    "DELETE FROM notifications WHERE dedupe_key = %s", (dedupe_key,)
                )
            if source_event_id is not None:
                cursor.execute(
                    "DELETE FROM audit_log WHERE entity_type = 'reminder_schedule' "
                    "AND new_values->>'event_id' = %s",
                    (str(source_event_id),),
                )
            if card_id is not None:
                cursor.execute("DELETE FROM connection_cards WHERE id = %s", (card_id,))
            if recipient_id is not None:
                cursor.execute("DELETE FROM users WHERE id = %s", (recipient_id,))

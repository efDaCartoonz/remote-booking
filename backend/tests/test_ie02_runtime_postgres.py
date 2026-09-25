"""IE-02 integration coverage for the durable notification worker runtime."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.core.config import settings
from app.notifications import (
    PostgresNotificationRuntimeRepository,
    PostgresNotificationService,
    TemporaryDeliveryError,
    deliver_pending_notifications,
)


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
            # Deliberately safe, bounded reason. Raw adapter response details
            # must never be copied into notifications.error_message.
            raise TemporaryDeliveryError("delivery_timeout")


def test_ie02_postgres_notification_runtime_retries_due_intent_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    token = uuid.uuid4().hex
    username = f"ie02-{token}"
    card_number = f"IE02-{token}"
    ticket_number = f"928-{int(token[:6], 16) % 1_000_000:06d}"
    dedupe_key: str | None = None
    connection: psycopg.Connection | None = None
    user_id: int | None = None
    card_id: int | None = None
    notification_id: int | None = None

    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, full_name)
                VALUES (%s, 'integration-test', 'IE-02 runtime test')
                RETURNING id
                """,
                (username,),
            )
            user_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, timezone) "
                "VALUES (%s, %s, 'UTC')",
                (user_id, f"chat-{token}"),
            )
            cursor.execute(
                """
                INSERT INTO connection_cards (
                    number, omnidesk_ticket_number, status_code,
                    planned_start_at, planned_duration_minutes
                )
                VALUES (%s, %s, 1, %s, 60)
                RETURNING id
                """,
                (card_number, ticket_number, datetime.now(UTC) + timedelta(days=1)),
            )
            card_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO card_events (card_id, event_type_code, actor_type_code)
                VALUES (%s, 1, 2)
                RETURNING id
                """,
                (card_id,),
            )
            source_event_id = cursor.fetchone()["id"]
        dedupe_key = f"notification:l1_reminder:{source_event_id}:{user_id}:telegram"
        connection.commit()

        notification_service = PostgresNotificationService(connection)
        common = {
            "event": "l1_reminder",
            "card_id": card_id,
            "source_event_id": source_event_id,
            "source_event_type": 1,
            "recipient_user_id": user_id,
            "channel": "telegram",
            "payload": {"card_id": card_id, "assignment": "l1"},
        }
        assert notification_service.notify(**common) is True
        assert notification_service.notify(**common) is False
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM notifications WHERE source_event_id = %s",
                (source_event_id,),
            )
            notification_id = cursor.fetchone()["id"]
            cursor.execute(
                "SELECT count(*) AS count FROM notifications "
                "WHERE source_event_id = %s",
                (source_event_id,),
            )
            assert cursor.fetchone()["count"] == 1

        monkeypatch.setattr(
            settings, "notification_card_base_url", "https://rdm.invalid"
        )
        adapter = _FailOnceAdapter()
        repository = PostgresNotificationRuntimeRepository(connection)
        now = datetime.now(UTC)

        # A real claim increments attempts and commits the lock before adapter I/O.
        assert deliver_pending_notifications(repository, {0: adapter}, now=now) == 0
        assert len(adapter.calls) == 1
        assert adapter.calls[0][0] == f"chat-{token}"
        assert adapter.calls[0][2] == f"rdm-notification:{notification_id}"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status_code, attempts, locked_at, next_attempt_at, error_message
                FROM notifications WHERE id = %s
                """,
                (notification_id,),
            )
            first_state = cursor.fetchone()
            assert first_state["status_code"] == 0
            assert first_state["attempts"] == 1
            assert first_state["locked_at"] is None
            assert first_state["next_attempt_at"] > now
            assert first_state["error_message"] == "delivery_timeout"
            cursor.execute(
                """
                SELECT c.status_code,
                       (SELECT count(*) FROM card_events e
                        WHERE e.id = %s AND e.card_id = c.id) AS source_event_count
                FROM connection_cards c
                WHERE c.id = %s
                """,
                (source_event_id, card_id),
            )
            business_state = cursor.fetchone()
            assert business_state == {"status_code": 1, "source_event_count": 1}

            cursor.execute(
                """
                SELECT new_values->>'status' AS status,
                       new_values->>'attempts' AS attempts,
                       new_values->>'reason' AS reason
                FROM audit_log
                WHERE entity_type = 'notification' AND entity_id = %s
                ORDER BY id
                """,
                (notification_id,),
            )
            assert [dict(row) for row in cursor.fetchall()] == [
                {"status": "pending", "attempts": "1", "reason": "delivery_timeout"}
            ]

        # The retry gate prevents an early second adapter call; due time permits it.
        assert (
            deliver_pending_notifications(
                repository,
                {0: adapter},
                now=first_state["next_attempt_at"] - timedelta(microseconds=1),
            )
            == 0
        )
        assert len(adapter.calls) == 1
        assert (
            deliver_pending_notifications(
                repository, {0: adapter}, now=first_state["next_attempt_at"]
            )
            == 1
        )
        assert len(adapter.calls) == 2
        assert adapter.calls[1][2] == f"rdm-notification:{notification_id}"

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status_code, attempts, locked_at, next_attempt_at, error_message
                FROM notifications WHERE id = %s
                """,
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
                """
                SELECT new_values->>'status' AS status,
                       new_values->>'attempts' AS attempts,
                       new_values->>'reason' AS reason
                FROM audit_log
                WHERE entity_type = 'notification' AND entity_id = %s
                ORDER BY id
                """,
                (notification_id,),
            )
            assert [dict(row) for row in cursor.fetchall()] == [
                {"status": "pending", "attempts": "1", "reason": "delivery_timeout"},
                {"status": "sent", "attempts": "2", "reason": None},
            ]
    finally:
        if connection is not None:
            connection.close()
        # Runtime repository methods commit their own transactions, so remove
        # only rows identified by this test's generated unique identifiers.
        try:
            with psycopg.connect(database_url) as cleanup, cleanup.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM audit_log WHERE entity_type = 'notification' "
                    "AND entity_id IN (SELECT id FROM notifications "
                    "WHERE dedupe_key = %s)",
                    (dedupe_key,),
                )
                if dedupe_key is not None:
                    cursor.execute(
                        "DELETE FROM notifications WHERE dedupe_key = %s",
                        (dedupe_key,),
                    )
                cursor.execute(
                    "DELETE FROM connection_cards WHERE number = %s", (card_number,)
                )
                cursor.execute("DELETE FROM users WHERE username = %s", (username,))
        except psycopg.OperationalError:
            # Preserve the original failure if the database itself became
            # unavailable; cleanup is safe to retry manually by these IDs.
            pass

"""IE-02 integration coverage for notification runtime failure modes and retries."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.core.config import settings
from app.notifications import (
    PermanentDeliveryError,
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


class _PermanentFailureAdapter:
    def __init__(self, reason: str = "telegram_rejected") -> None:
        self.reason = reason
        self.calls: list[tuple[str, str, str]] = []

    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        self.calls.append((recipient, text, idempotency_key))
        raise PermanentDeliveryError(self.reason)


class _TemporaryFailureAdapter:
    def __init__(self, reason: str = "telegram_temporary_error") -> None:
        self.reason = reason
        self.calls: list[tuple[str, str, str]] = []

    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        self.calls.append((recipient, text, idempotency_key))
        raise TemporaryDeliveryError(self.reason)


def _seed_notification(
    connection: psycopg.Connection, token: str
) -> tuple[int, int, int, str, str, str, int]:
    username = f"ie02-fail-{token}"
    card_number = f"IE02-FAIL-{token}"
    ticket_number = f"928-{int(token[:6], 16) % 1_000_000:06d}"
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO users (username, password_hash, full_name)
            VALUES (%s, 'integration-test', 'IE-02 failure matrix test')
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

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM notifications WHERE source_event_id = %s",
            (source_event_id,),
        )
        notification_id = cursor.fetchone()["id"]

    return (
        user_id,
        card_id,
        notification_id,
        username,
        card_number,
        dedupe_key,
        source_event_id,
    )


def _cleanup_seeded_data(
    database_url: str,
    dedupe_key: str | None,
    card_number: str | None,
    username: str | None,
) -> None:
    try:
        with psycopg.connect(database_url) as cleanup, cleanup.cursor() as cursor:
            if dedupe_key is not None:
                cursor.execute(
                    "DELETE FROM audit_log WHERE entity_type = 'notification' "
                    "AND entity_id IN (SELECT id FROM notifications WHERE dedupe_key = %s)",
                    (dedupe_key,),
                )
                cursor.execute(
                    "DELETE FROM notifications WHERE dedupe_key = %s",
                    (dedupe_key,),
                )
            if card_number is not None:
                cursor.execute(
                    "DELETE FROM connection_cards WHERE number = %s", (card_number,)
                )
            if username is not None:
                cursor.execute("DELETE FROM users WHERE username = %s", (username,))
    except psycopg.OperationalError:
        pass


def test_ie02_postgres_notification_runtime_permanent_failure_marks_failed_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    token = uuid.uuid4().hex
    dedupe_key: str | None = None
    username: str | None = None
    card_number: str | None = None
    connection: psycopg.Connection | None = None

    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
        (
            user_id,
            card_id,
            notification_id,
            username,
            card_number,
            dedupe_key,
            source_event_id,
        ) = _seed_notification(connection, token)

        monkeypatch.setattr(
            settings, "notification_card_base_url", "https://rdm.invalid"
        )
        adapter = _PermanentFailureAdapter("telegram_rejected")
        repository = PostgresNotificationRuntimeRepository(connection)
        now = datetime.now(UTC)

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
            state = cursor.fetchone()
            assert state["status_code"] == 2  # FAILED
            assert state["attempts"] == 1
            assert state["locked_at"] is None
            assert state["next_attempt_at"] is None
            assert state["error_message"] == "telegram_rejected"

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
            audit_logs = [dict(row) for row in cursor.fetchall()]
            assert audit_logs == [
                {"status": "failed", "attempts": "1", "reason": "telegram_rejected"}
            ]

        assert (
            deliver_pending_notifications(
                repository, {0: adapter}, now=now + timedelta(hours=1)
            )
            == 0
        )
        assert len(adapter.calls) == 1
        assert (
            repository.claim_one(
                now=now + timedelta(hours=1),
                max_attempts=settings.notification_max_attempts,
            )
            is None
        )
    finally:
        if connection is not None:
            connection.close()
        _cleanup_seeded_data(database_url, dedupe_key, card_number, username)


def test_ie02_postgres_notification_runtime_repeated_temporary_failure_exhausts_retries_and_marks_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _database_url()
    token = uuid.uuid4().hex
    dedupe_key: str | None = None
    username: str | None = None
    card_number: str | None = None
    connection: psycopg.Connection | None = None

    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
        (
            user_id,
            card_id,
            notification_id,
            username,
            card_number,
            dedupe_key,
            source_event_id,
        ) = _seed_notification(connection, token)

        monkeypatch.setattr(
            settings, "notification_card_base_url", "https://rdm.invalid"
        )
        adapter = _TemporaryFailureAdapter("telegram_temporary_error")
        repository = PostgresNotificationRuntimeRepository(connection)
        max_attempts = settings.notification_max_attempts
        current_now = datetime.now(UTC)

        # Attempt 1 through max_attempts - 1 (retryable pending attempts)
        for attempt in range(1, max_attempts):
            assert (
                deliver_pending_notifications(repository, {0: adapter}, now=current_now)
                == 0
            )
            assert len(adapter.calls) == attempt

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT status_code, attempts, locked_at, next_attempt_at, error_message
                    FROM notifications WHERE id = %s
                    """,
                    (notification_id,),
                )
                state = cursor.fetchone()
                assert state["status_code"] == 0  # PENDING
                assert state["attempts"] == attempt
                assert state["locked_at"] is None
                assert state["next_attempt_at"] > current_now
                assert state["error_message"] == "telegram_temporary_error"
                next_attempt_at = state["next_attempt_at"]

            # Early retry attempt before next_attempt_at is skipped by retry gate
            assert (
                deliver_pending_notifications(
                    repository,
                    {0: adapter},
                    now=next_attempt_at - timedelta(microseconds=1),
                )
                == 0
            )
            assert len(adapter.calls) == attempt

            current_now = next_attempt_at

        # Final attempt (attempt == max_attempts): retries exhausted -> FAILED
        assert (
            deliver_pending_notifications(repository, {0: adapter}, now=current_now)
            == 0
        )
        assert len(adapter.calls) == max_attempts

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT status_code, attempts, locked_at, next_attempt_at, error_message
                FROM notifications WHERE id = %s
                """,
                (notification_id,),
            )
            final_state = cursor.fetchone()
            assert final_state["status_code"] == 2  # FAILED
            assert final_state["attempts"] == max_attempts
            assert final_state["locked_at"] is None
            assert final_state["next_attempt_at"] is None
            assert final_state["error_message"] == "telegram_temporary_error"

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
            audit_logs = [dict(row) for row in cursor.fetchall()]
            expected_audits = [
                {
                    "status": "pending",
                    "attempts": str(a),
                    "reason": "telegram_temporary_error",
                }
                for a in range(1, max_attempts)
            ] + [
                {
                    "status": "failed",
                    "attempts": str(max_attempts),
                    "reason": "telegram_temporary_error",
                }
            ]
            assert audit_logs == expected_audits

        # Verify no further claim or delivery on subsequent calls
        assert (
            deliver_pending_notifications(
                repository, {0: adapter}, now=current_now + timedelta(hours=1)
            )
            == 0
        )
        assert len(adapter.calls) == max_attempts
        assert (
            repository.claim_one(
                now=current_now + timedelta(hours=1), max_attempts=max_attempts
            )
            is None
        )
    finally:
        if connection is not None:
            connection.close()
        _cleanup_seeded_data(database_url, dedupe_key, card_number, username)

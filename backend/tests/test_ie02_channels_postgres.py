"""PostgreSQL integration tests for IE-02 channel toggles and two-channel worker batch delivery."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.core.config import settings
from app.notifications import (
    NOTIFICATION_CHANNEL_CODES,
    PostgresNotificationRuntimeRepository,
    PostgresNotificationService,
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


class StubChannelAdapter:
    """In-memory stub channel adapter recording delivery attempts."""

    def __init__(self) -> None:
        self.deliveries: list[dict[str, str]] = []

    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        self.deliveries.append(
            {
                "recipient": recipient,
                "text": text,
                "idempotency_key": idempotency_key,
            }
        )


def test_user_settings_channel_toggle_suppresses_disabled_channel_retaining_enabled() -> (
    None
):
    """User_settings channel toggles strictly govern intent persistence:

    disabled channel is suppressed while enabled channel is retained and persisted.
    Flipping toggles updates the behavior accordingly.
    """
    database_url = _database_url()
    connection: psycopg.Connection | None = None
    token = uuid4().hex[:12]
    user_id: int | None = None
    card_id: int | None = None
    event1_id: int | None = None
    event2_id: int | None = None
    event3_id: int | None = None

    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
        now = datetime.now(UTC).replace(microsecond=0)

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', 'Channel Toggle User') RETURNING id",
                (f"toggle-user-{token}",),
            )
            user_id = cursor.fetchone()["id"]

            # Initial state: notify_telegram = True, notify_bitrix24 = False
            cursor.execute(
                """INSERT INTO user_settings
                   (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24)
                   VALUES (%s, %s, %s, true, false)""",
                (user_id, f"tg-{token}", f"bx-{token}"),
            )

            cursor.execute(
                """INSERT INTO connection_cards
                   (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes)
                   VALUES (%s, 1, %s, 60) RETURNING id""",
                (f"992-{uuid4().int % 1_000_000:06d}", now + timedelta(days=1)),
            )
            card_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO card_events (card_id, event_type_code, actor_type_code) VALUES (%s, 1, 2) RETURNING id",
                (card_id,),
            )
            event1_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO card_events (card_id, event_type_code, actor_type_code) VALUES (%s, 1, 2) RETURNING id",
                (card_id,),
            )
            event2_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO card_events (card_id, event_type_code, actor_type_code) VALUES (%s, 1, 2) RETURNING id",
                (card_id,),
            )
            event3_id = cursor.fetchone()["id"]
        connection.commit()

        service = PostgresNotificationService(connection)
        tg_code = NOTIFICATION_CHANNEL_CODES["telegram"]  # 0
        bx_code = NOTIFICATION_CHANNEL_CODES["bitrix24"]  # 1

        # Phase 1: notify_telegram=True, notify_bitrix24=False
        # Telegram attempt: retained and persisted
        tg_ok = service.notify(
            event="l1_reminder",
            card_id=card_id,
            source_event_id=event1_id,
            source_event_type=1,
            recipient_user_id=user_id,
            channel="telegram",
            payload={"card_id": card_id, "assignment": "l1"},
        )
        assert tg_ok is True

        # Bitrix24 attempt: suppressed by user_settings
        bx_ok = service.notify(
            event="l1_reminder",
            card_id=card_id,
            source_event_id=event1_id,
            source_event_type=1,
            recipient_user_id=user_id,
            channel="bitrix24",
            payload={"card_id": card_id, "assignment": "l1"},
        )
        assert bx_ok is False

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT channel_code FROM notifications WHERE card_id = %s AND source_event_id = %s",
                (card_id, event1_id),
            )
            rows = cursor.fetchall()
            assert len(rows) == 1
            assert rows[0]["channel_code"] == tg_code

        # Phase 2: Flip toggles -> notify_telegram=False, notify_bitrix24=True
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE user_settings SET notify_telegram = false, notify_bitrix24 = true WHERE user_id = %s",
                (user_id,),
            )
        connection.commit()

        # Telegram attempt: now suppressed
        tg_ok2 = service.notify(
            event="l1_reminder",
            card_id=card_id,
            source_event_id=event2_id,
            source_event_type=1,
            recipient_user_id=user_id,
            channel="telegram",
            payload={"card_id": card_id, "assignment": "l1"},
        )
        assert tg_ok2 is False

        # Bitrix24 attempt: now retained and persisted
        bx_ok2 = service.notify(
            event="l1_reminder",
            card_id=card_id,
            source_event_id=event2_id,
            source_event_type=1,
            recipient_user_id=user_id,
            channel="bitrix24",
            payload={"card_id": card_id, "assignment": "l1"},
        )
        assert bx_ok2 is True

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT channel_code FROM notifications WHERE card_id = %s AND source_event_id = %s",
                (card_id, event2_id),
            )
            rows = cursor.fetchall()
            assert len(rows) == 1
            assert rows[0]["channel_code"] == bx_code

        # Phase 3: Both toggles disabled -> notify_telegram=False, notify_bitrix24=False
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE user_settings SET notify_telegram = false, notify_bitrix24 = false WHERE user_id = %s",
                (user_id,),
            )
        connection.commit()

        tg_ok3 = service.notify(
            event="l1_reminder",
            card_id=card_id,
            source_event_id=event3_id,
            source_event_type=1,
            recipient_user_id=user_id,
            channel="telegram",
            payload={"card_id": card_id, "assignment": "l1"},
        )
        bx_ok3 = service.notify(
            event="l1_reminder",
            card_id=card_id,
            source_event_id=event3_id,
            source_event_type=1,
            recipient_user_id=user_id,
            channel="bitrix24",
            payload={"card_id": card_id, "assignment": "l1"},
        )
        assert tg_ok3 is False
        assert bx_ok3 is False

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS count FROM notifications WHERE card_id = %s AND source_event_id = %s",
                (card_id, event3_id),
            )
            assert cursor.fetchone()["count"] == 0
    finally:
        if connection is not None:
            try:
                with connection.cursor() as cursor:
                    if card_id is not None:
                        cursor.execute(
                            "DELETE FROM audit_log WHERE entity_type = 'notification' "
                            "AND entity_id IN (SELECT id FROM notifications WHERE card_id = %s)",
                            (card_id,),
                        )
                        cursor.execute(
                            "DELETE FROM notifications WHERE card_id = %s", (card_id,)
                        )
                        cursor.execute(
                            "DELETE FROM card_events WHERE card_id = %s", (card_id,)
                        )
                        cursor.execute(
                            "DELETE FROM connection_cards WHERE id = %s", (card_id,)
                        )
                    if user_id is not None:
                        cursor.execute(
                            "DELETE FROM user_settings WHERE user_id = %s", (user_id,)
                        )
                        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                connection.commit()
            except Exception:
                pass
            connection.close()


def test_two_channel_worker_batch_delivers_both_with_stubs_and_avoids_duplicate_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime worker delivers pending intents across multiple enabled channels

    in a single batch using stub adapters, marks them SENT with audit logs,
    and avoids duplicate claim on subsequent runs.
    """
    database_url = _database_url()
    connection: psycopg.Connection | None = None
    token = uuid4().hex[:12]
    user_id: int | None = None
    card_id: int | None = None
    source_event_id: int | None = None
    tg_intent_id: int | None = None
    bx_intent_id: int | None = None

    monkeypatch.setattr(
        settings, "notification_card_base_url", "https://rdm.example.com"
    )

    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
        now = datetime.now(UTC).replace(microsecond=0)

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', 'Two Channel Worker User') RETURNING id",
                (f"batch-user-{token}",),
            )
            user_id = cursor.fetchone()["id"]

            cursor.execute(
                """INSERT INTO user_settings
                   (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24, timezone)
                   VALUES (%s, %s, %s, true, true, 'UTC')""",
                (user_id, f"tg-chat-{token}", f"bx-user-{token}"),
            )

            cursor.execute(
                """INSERT INTO connection_cards
                   (number, omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes)
                   VALUES (%s, %s, 1, %s, 45) RETURNING id""",
                (
                    f"RDM-{token[:6]}",
                    f"993-{uuid4().int % 1_000_000:06d}",
                    now + timedelta(hours=2),
                ),
            )
            card_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO card_events (card_id, event_type_code, actor_type_code) VALUES (%s, 1, 2) RETURNING id",
                (card_id,),
            )
            source_event_id = cursor.fetchone()["id"]
        connection.commit()

        service = PostgresNotificationService(connection)
        assert (
            service.notify(
                event="l2_reminder",
                card_id=card_id,
                source_event_id=source_event_id,
                source_event_type=1,
                recipient_user_id=user_id,
                channel="telegram",
                payload={"card_id": card_id, "assignment": "l2"},
            )
            is True
        )
        assert (
            service.notify(
                event="l2_reminder",
                card_id=card_id,
                source_event_id=source_event_id,
                source_event_type=1,
                recipient_user_id=user_id,
                channel="bitrix24",
                payload={"card_id": card_id, "assignment": "l2"},
            )
            is True
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, channel_code FROM notifications WHERE card_id = %s ORDER BY channel_code",
                (card_id,),
            )
            intents = {row["channel_code"]: row["id"] for row in cursor.fetchall()}
            assert len(intents) == 2
            tg_intent_id = intents[0]
            bx_intent_id = intents[1]

        # Prepare stub adapters
        tg_stub = StubChannelAdapter()
        bx_stub = StubChannelAdapter()
        adapters = {0: tg_stub, 1: bx_stub}
        repository = PostgresNotificationRuntimeRepository(connection)

        # Batch delivery execution
        delivered_count = deliver_pending_notifications(
            repository, adapters, now=now, limit=10
        )
        assert delivered_count == 2

        # Assert stub adapter calls
        assert len(tg_stub.deliveries) == 1
        assert tg_stub.deliveries[0]["recipient"] == f"tg-chat-{token}"
        assert (
            tg_stub.deliveries[0]["idempotency_key"]
            == f"rdm-notification:{tg_intent_id}"
        )

        assert len(bx_stub.deliveries) == 1
        assert bx_stub.deliveries[0]["recipient"] == f"bx-user-{token}"
        assert (
            bx_stub.deliveries[0]["idempotency_key"]
            == f"rdm-notification:{bx_intent_id}"
        )

        # Assert DB notification states after delivery
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT id, status_code, attempts, locked_at, sent_at, error_message
                   FROM notifications
                   WHERE id IN (%s, %s)
                   ORDER BY channel_code""",
                (tg_intent_id, bx_intent_id),
            )
            states = cursor.fetchall()
            assert len(states) == 2
            for row in states:
                assert row["status_code"] == 1  # SENT
                assert row["attempts"] == 1
                assert row["locked_at"] is None
                assert row["sent_at"] is not None
                assert row["error_message"] is None

            # Assert audit log entries for notification delivery
            cursor.execute(
                """SELECT entity_id, new_values->>'status' AS status, new_values->>'attempts' AS attempts
                   FROM audit_log
                   WHERE entity_type = 'notification' AND entity_id IN (%s, %s)
                   ORDER BY entity_id""",
                (tg_intent_id, bx_intent_id),
            )
            audit_rows = cursor.fetchall()
            assert len(audit_rows) == 2
            assert all(row["status"] == "sent" for row in audit_rows)
            assert all(row["attempts"] == "1" for row in audit_rows)

        # Re-run deliver_pending_notifications: verify no duplicate claim occurs
        second_run_count = deliver_pending_notifications(
            repository, adapters, now=now + timedelta(seconds=10), limit=10
        )
        assert second_run_count == 0

        # Confirm no extra deliveries recorded by stub adapters
        assert len(tg_stub.deliveries) == 1
        assert len(bx_stub.deliveries) == 1
    finally:
        if connection is not None:
            try:
                with connection.cursor() as cursor:
                    if card_id is not None:
                        cursor.execute(
                            "DELETE FROM audit_log WHERE entity_type = 'notification' "
                            "AND entity_id IN (SELECT id FROM notifications WHERE card_id = %s)",
                            (card_id,),
                        )
                        cursor.execute(
                            "DELETE FROM notifications WHERE card_id = %s", (card_id,)
                        )
                        cursor.execute(
                            "DELETE FROM card_events WHERE card_id = %s", (card_id,)
                        )
                        cursor.execute(
                            "DELETE FROM connection_cards WHERE id = %s", (card_id,)
                        )
                    if user_id is not None:
                        cursor.execute(
                            "DELETE FROM user_settings WHERE user_id = %s", (user_id,)
                        )
                        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
                connection.commit()
            except Exception:
                pass
            connection.close()

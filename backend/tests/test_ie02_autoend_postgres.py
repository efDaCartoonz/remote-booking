"""PostgreSQL integration tests for IE-02 auto-end notification intents."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import CardEventType, CardStatus, RoleId
from app.cards.extension import process_due_in_progress_sessions
from app.notifications import NOTIFICATION_EVENT_CODES, PostgresNotificationService

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


def test_autoend_at_12h_persists_exact_card_ended_automatically_intents() -> None:
    """Auto-end at 12h transitions status to COMPLETED_PENDING_RESULT and persists

    exact card_ended_automatically intents for assigned L2 and active managers
    per their enabled channels, referencing the single STATUS_CHANGED source event.
    """
    database_url = _database_url()
    connection: psycopg.Connection | None = None
    suffix = uuid4().hex[:12]
    ticket_number = f"991-{uuid4().int % 1_000_000:06d}"
    card_id: int | None = None
    l2_id: int | None = None
    manager1_id: int | None = None
    manager2_id: int | None = None

    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
        now = datetime.now(UTC).replace(microsecond=0)

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"autoend-l2-{suffix}", f"Autoend L2 {suffix}"),
            )
            l2_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"autoend-m1-{suffix}", f"Autoend Manager1 {suffix}"),
            )
            manager1_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"autoend-m2-{suffix}", f"Autoend Manager2 {suffix}"),
            )
            manager2_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s), (%s, %s), (%s, %s)",
                (
                    l2_id,
                    int(RoleId.L2),
                    manager1_id,
                    int(RoleId.MANAGER),
                    manager2_id,
                    int(RoleId.MANAGER),
                ),
            )

            # L2 and Manager1 have both telegram and bitrix24 enabled.
            # Manager2 has only telegram enabled (bitrix24 disabled).
            cursor.execute(
                """INSERT INTO user_settings
                   (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24)
                   VALUES
                   (%s, %s, %s, true, true),
                   (%s, %s, %s, true, true),
                   (%s, %s, %s, true, false)""",
                (
                    l2_id,
                    f"tg-l2-{suffix}",
                    f"bx-l2-{suffix}",
                    manager1_id,
                    f"tg-m1-{suffix}",
                    f"bx-m1-{suffix}",
                    manager2_id,
                    f"tg-m2-{suffix}",
                    f"bx-m2-{suffix}",
                ),
            )

            past_start = now - timedelta(hours=12, minutes=5)
            cursor.execute(
                """INSERT INTO connection_cards
                   (omnidesk_ticket_number, status_code, planned_start_at,
                    planned_duration_minutes, l2_engineer_id)
                   VALUES (%s, %s, %s, 720, %s) RETURNING id""",
                (ticket_number, int(CardStatus.IN_PROGRESS), past_start, l2_id),
            )
            card_id = cursor.fetchone()["id"]
        connection.commit()

        # Run session extension processor
        extended_count = process_due_in_progress_sessions(connection, now=now)
        assert extended_count == 0  # 720m duration auto-ends, does not extend

        with connection.cursor() as cursor:
            # 1. Verify card status transitioned to COMPLETED_PENDING_RESULT (5)
            cursor.execute(
                "SELECT status_code FROM connection_cards WHERE id = %s",
                (card_id,),
            )
            card_row = cursor.fetchone()
            assert card_row is not None
            assert card_row["status_code"] == int(CardStatus.COMPLETED_PENDING_RESULT)

            # 2. Verify exact STATUS_CHANGED event was created with auto-end comment
            cursor.execute(
                """SELECT id, event_type_code, comment
                   FROM card_events
                   WHERE card_id = %s AND comment = 'завершено автоматически'
                   ORDER BY id DESC LIMIT 1""",
                (card_id,),
            )
            event_row = cursor.fetchone()
            assert event_row is not None
            assert event_row["event_type_code"] == int(CardEventType.STATUS_CHANGED)
            source_event_id = event_row["id"]

            # 3. Query all notifications generated for this card
            cursor.execute(
                """SELECT recipient_user_id, channel_code, event_type_code,
                          source_event_id, source_event_type_code, payload, dedupe_key
                   FROM notifications
                   WHERE card_id = %s
                   ORDER BY recipient_user_id, channel_code""",
                (card_id,),
            )
            persisted = [dict(row) for row in cursor.fetchall()]

            # Expected intents:
            # - Assigned L2: telegram (0), bitrix24 (1) -> 2 intents
            # - Manager 1: telegram (0), bitrix24 (1) -> 2 intents
            # - Manager 2: telegram (0) only -> 1 intent (bitrix24 suppressed by settings)
            expected_recipients_channels = {
                (l2_id, 0, "l2"),
                (l2_id, 1, "l2"),
                (manager1_id, 0, "manager_escalation"),
                (manager1_id, 1, "manager_escalation"),
                (manager2_id, 0, "manager_escalation"),
            }

            actual_recipients_channels = {
                (
                    row["recipient_user_id"],
                    row["channel_code"],
                    row["payload"]["assignment"],
                )
                for row in persisted
            }
            assert actual_recipients_channels == expected_recipients_channels
            assert len(persisted) == len(expected_recipients_channels)

            # Verify common attributes across all intents
            event_code_autoend = NOTIFICATION_EVENT_CODES["card_ended_automatically"]
            assert event_code_autoend == 7
            for row in persisted:
                assert row["event_type_code"] == event_code_autoend
                assert row["source_event_id"] == source_event_id
                assert row["source_event_type_code"] == int(
                    CardEventType.STATUS_CHANGED
                )
                assert row["payload"]["card_id"] == card_id

                channel_name = "telegram" if row["channel_code"] == 0 else "bitrix24"
                expected_dedupe_key = f"notification:card_ended_automatically:{source_event_id}:{row['recipient_user_id']}:{channel_name}"
                assert row["dedupe_key"] == expected_dedupe_key

            # 4. Verify duplicate claim / replay idempotency: rerun processor & manual notify attempt
            assert process_due_in_progress_sessions(connection, now=now) == 0

            notifications_service = PostgresNotificationService(connection)
            assert (
                notifications_service.notify(
                    event="card_ended_automatically",
                    card_id=card_id,
                    source_event_id=source_event_id,
                    source_event_type=int(CardEventType.STATUS_CHANGED),
                    recipient_user_id=l2_id,
                    channel="telegram",
                    payload={"card_id": card_id, "assignment": "l2"},
                )
                is False
            )

            cursor.execute(
                "SELECT count(*) AS count FROM notifications WHERE card_id = %s",
                (card_id,),
            )
            assert cursor.fetchone()["count"] == len(expected_recipients_channels)
    finally:
        if connection is not None:
            user_ids = [u for u in (l2_id, manager1_id, manager2_id) if u is not None]
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
                    if user_ids:
                        cursor.execute(
                            "DELETE FROM user_roles WHERE user_id = ANY(%s)",
                            (user_ids,),
                        )
                        cursor.execute(
                            "DELETE FROM user_settings WHERE user_id = ANY(%s)",
                            (user_ids,),
                        )
                        cursor.execute(
                            "DELETE FROM users WHERE id = ANY(%s)", (user_ids,)
                        )
                connection.commit()
            except Exception:
                pass
            connection.close()


def test_autoend_boundary_710m_session_persists_card_ended_automatically_intents() -> (
    None
):
    """A 710m in-progress session with a 15m extension interval auto-ends without extension

    and persists exact card_ended_automatically intents referencing the STATUS_CHANGED event.
    """
    database_url = _database_url()
    connection: psycopg.Connection | None = None
    suffix = uuid4().hex[:12]
    ticket_number = f"991-{uuid4().int % 1_000_000:06d}"
    card_id: int | None = None
    l2_id: int | None = None

    try:
        connection = psycopg.connect(database_url, row_factory=dict_row)
        now = datetime.now(UTC).replace(microsecond=0)

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"autoend-710m-{suffix}", f"Autoend 710m {suffix}"),
            )
            l2_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (l2_id, int(RoleId.L2)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, notify_telegram, notify_bitrix24) VALUES (%s, %s, true, false)",
                (l2_id, f"tg-710m-{suffix}"),
            )
            past_start = now - timedelta(minutes=710)
            cursor.execute(
                """INSERT INTO connection_cards
                   (omnidesk_ticket_number, status_code, planned_start_at,
                    planned_duration_minutes, l2_engineer_id)
                   VALUES (%s, %s, %s, 710, %s) RETURNING id""",
                (ticket_number, int(CardStatus.IN_PROGRESS), past_start, l2_id),
            )
            card_id = cursor.fetchone()["id"]
        connection.commit()

        # 710m + 15m interval = 725m > 720m -> triggers auto-end
        extended_count = process_due_in_progress_sessions(connection, now=now)
        assert extended_count == 0

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status_code, planned_duration_minutes FROM connection_cards WHERE id = %s",
                (card_id,),
            )
            card_row = cursor.fetchone()
            assert card_row["status_code"] == int(CardStatus.COMPLETED_PENDING_RESULT)
            assert card_row["planned_duration_minutes"] == 710

            cursor.execute(
                "SELECT id FROM card_events WHERE card_id = %s AND comment = 'завершено автоматически'",
                (card_id,),
            )
            event_row = cursor.fetchone()
            assert event_row is not None
            source_event_id = event_row["id"]

            cursor.execute(
                "SELECT channel_code, event_type_code, source_event_id FROM notifications WHERE card_id = %s AND recipient_user_id = %s",
                (card_id, l2_id),
            )
            rows = cursor.fetchall()
            assert len(rows) == 1
            assert rows[0]["channel_code"] == 0  # telegram
            assert rows[0]["event_type_code"] == 7  # card_ended_automatically
            assert rows[0]["source_event_id"] == source_event_id
    finally:
        if connection is not None:
            try:
                with connection.cursor() as cursor:
                    if card_id is not None:
                        cursor.execute(
                            "DELETE FROM notifications WHERE card_id = %s", (card_id,)
                        )
                        cursor.execute(
                            "DELETE FROM card_events WHERE card_id = %s", (card_id,)
                        )
                        cursor.execute(
                            "DELETE FROM connection_cards WHERE id = %s", (card_id,)
                        )
                    if l2_id is not None:
                        cursor.execute(
                            "DELETE FROM user_roles WHERE user_id = %s", (l2_id,)
                        )
                        cursor.execute(
                            "DELETE FROM user_settings WHERE user_id = %s", (l2_id,)
                        )
                        cursor.execute("DELETE FROM users WHERE id = %s", (l2_id,))
                connection.commit()
            except Exception:
                pass
            connection.close()

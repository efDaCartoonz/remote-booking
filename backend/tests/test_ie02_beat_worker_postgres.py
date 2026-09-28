"""Exercise IE-02 reminder scanning via versioned Celery Beat and worker through Redis."""

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.cards.constants import CardStatus, RoleId
from app.worker import celery_app

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_IE02_BEAT_WORKER") != "1",
    reason="isolated Beat/worker gate only",
)


def test_beat_dispatches_due_reminder_scan_and_worker_creates_intents() -> None:
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)
    token = uuid.uuid4().hex
    card_id: int | None = None
    l2_user_id: int | None = None
    mgr_user_id: int | None = None

    try:
        now = datetime.now(UTC).replace(microsecond=0)
        ticket_number = f"999-{uuid.uuid4().int % 900000 + 100000:06d}"

        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                # 1. Create synthetic L2 engineer
                cursor.execute(
                    "INSERT INTO users (username, password_hash, full_name) "
                    "VALUES (%s, 'test', 'IE02 Beat Gate L2') RETURNING id",
                    (f"ie02-beat-l2-{token}",),
                )
                l2_user_id = cursor.fetchone()["id"]

                # 2. Create synthetic Manager
                cursor.execute(
                    "INSERT INTO users (username, password_hash, full_name) "
                    "VALUES (%s, 'test', 'IE02 Beat Gate Mgr') RETURNING id",
                    (f"ie02-beat-mgr-{token}",),
                )
                mgr_user_id = cursor.fetchone()["id"]

                # 3. Assign roles
                cursor.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s), (%s, %s)",
                    (l2_user_id, int(RoleId.L2), mgr_user_id, int(RoleId.MANAGER)),
                )

                # 4. Create user settings with configured & enabled notification channels
                cursor.execute(
                    "INSERT INTO user_settings "
                    "(user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24) "
                    "VALUES (%s, %s, %s, true, true), (%s, %s, %s, true, false)",
                    (
                        l2_user_id,
                        f"tg-l2-{token}",
                        f"bx-l2-{token}",
                        mgr_user_id,
                        f"tg-mgr-{token}",
                        f"bx-mgr-{token}",
                    ),
                )

                # 5. Create synthetic connection card assigned to L2 engineer
                cursor.execute(
                    "INSERT INTO connection_cards "
                    "(number, omnidesk_ticket_number, status_code, planned_start_at, "
                    "planned_duration_minutes, l2_engineer_id) "
                    "VALUES (%s, %s, %s, %s, 60, %s) RETURNING id",
                    (
                        f"IE02-{token}",
                        ticket_number,
                        int(CardStatus.ASSIGNED),
                        now + timedelta(days=1),
                        l2_user_id,
                    ),
                )
                card_id = cursor.fetchone()["id"]

                # 6. Insert DUE reminder schedule (next_due_at in the past)
                interval = 60
                anchor = now - timedelta(seconds=interval * 2)
                cursor.execute(
                    "INSERT INTO reminder_schedules "
                    "(card_id, kind, owner_id, anchor_at, interval_seconds, "
                    "escalation_after_count, next_due_at, last_count, settings_snapshot) "
                    "VALUES (%s, 'l2_reminder', %s, %s, %s, 2, %s, 1, %s) RETURNING id",
                    (
                        card_id,
                        l2_user_id,
                        anchor,
                        interval,
                        now - timedelta(seconds=1),
                        Jsonb({"kind": "l2_reminder", "owner_id": l2_user_id}),
                    ),
                )

            connection.commit()

        # The scan task is NOT called directly by this test: only live Celery Beat
        # dispatches scan_reminders through Redis, and only the versioned worker scanner
        # processes due reminders into card_events + notifications intents.
        deadline = time.monotonic() + 145
        source_event_id: int | None = None
        while time.monotonic() < deadline:
            with psycopg.connect(database_url, row_factory=dict_row) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id, new_values FROM card_events "
                        "WHERE card_id = %s AND comment = 'timer_reminder'",
                        (card_id,),
                    )
                    event_row = cursor.fetchone()

                    cursor.execute(
                        "SELECT recipient_user_id, channel_code, event_type_code, "
                        "source_event_id, source_event_type_code, status_code, payload "
                        "FROM notifications WHERE card_id = %s "
                        "ORDER BY recipient_user_id, channel_code",
                        (card_id,),
                    )
                    intent_rows = [dict(r) for r in cursor.fetchall()]

                    cursor.execute(
                        "SELECT last_count, escalation_sent FROM reminder_schedules WHERE card_id = %s",
                        (card_id,),
                    )
                    schedule_row = cursor.fetchone()

            if (
                event_row is not None
                and len(intent_rows) >= 3  # 2 L2 channels + 1 Manager channel
                and schedule_row["last_count"] == 2
                and schedule_row["escalation_sent"] is True
            ):
                source_event_id = event_row["id"]
                assert event_row["new_values"] == {"timer": "l2_reminder", "count": 2}

                # Verify exact notification intents created for L2 and Manager
                expected_intents = {
                    (
                        l2_user_id,
                        0,
                        4,
                    ),  # L2 Telegram (event_type_code = 4: L2 reminder)
                    (l2_user_id, 1, 4),  # L2 Bitrix24
                    (
                        mgr_user_id,
                        0,
                        2,
                    ),  # Manager Telegram (event_type_code = 2: escalation)
                }
                actual_intents = {
                    (r["recipient_user_id"], r["channel_code"], r["event_type_code"])
                    for r in intent_rows
                }
                assert actual_intents == expected_intents
                assert all(
                    r["status_code"] == 0 for r in intent_rows
                )  # All intents pending (status 0)
                assert all(r["source_event_id"] == source_event_id for r in intent_rows)
                assert all(r["source_event_type_code"] == 4 for r in intent_rows)
                break

            time.sleep(2)
        else:
            pytest.fail(
                "Celery Beat did not cause worker scanner to process due IE-02 reminder within 145s"
            )

        # Explicitly verify notification delivery remains DISABLED in this gate.
        # Sending deliver_notifications task to worker must return 0 (delivery disabled)
        # and leave all DB notifications in pending status (0) without external calls.
        result = celery_app.send_task("app.worker.deliver_notifications")
        assert result.get(timeout=20) == 0

        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status_code, attempts, locked_at, error_message "
                    "FROM notifications WHERE card_id = %s",
                    (card_id,),
                )
                post_delivery_rows = [dict(r) for r in cursor.fetchall()]
                assert len(post_delivery_rows) >= 3
                for row in post_delivery_rows:
                    assert row["status_code"] == 0
                    assert row["attempts"] == 0
                    assert row["locked_at"] is None
                    assert row["error_message"] is None

    finally:
        if card_id is not None or l2_user_id is not None:
            with psycopg.connect(database_url) as cleanup:
                with cleanup.cursor() as cursor:
                    if card_id is not None:
                        cursor.execute(
                            "DELETE FROM audit_log WHERE "
                            "(entity_type = 'reminder_schedule' AND entity_id = %s) OR "
                            "(entity_type = 'notification' AND entity_id IN ("
                            "SELECT id FROM notifications WHERE card_id = %s))",
                            (card_id, card_id),
                        )
                        cursor.execute(
                            "DELETE FROM notifications WHERE card_id = %s",
                            (card_id,),
                        )
                        cursor.execute(
                            "DELETE FROM card_events WHERE card_id = %s",
                            (card_id,),
                        )
                        cursor.execute(
                            "DELETE FROM reminder_schedules WHERE card_id = %s",
                            (card_id,),
                        )
                        cursor.execute(
                            "DELETE FROM connection_cards WHERE id = %s",
                            (card_id,),
                        )
                    user_ids = [
                        uid for uid in (l2_user_id, mgr_user_id) if uid is not None
                    ]
                    if user_ids:
                        cursor.execute(
                            "DELETE FROM user_settings WHERE user_id = ANY(%s)",
                            (user_ids,),
                        )
                        cursor.execute(
                            "DELETE FROM user_roles WHERE user_id = ANY(%s)",
                            (user_ids,),
                        )
                        cursor.execute(
                            "DELETE FROM users WHERE id = ANY(%s)",
                            (user_ids,),
                        )
                cleanup.commit()

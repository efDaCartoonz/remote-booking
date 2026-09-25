"""Cross-check persisted L2 reminder events and notification intents."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.cards.constants import RoleId
from app.notifications import PostgresNotificationService
from app.reminders import PostgresReminderRepository, ReminderService

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture
def database_url() -> str:
    return os.getenv("PSYCOPG_DATABASE_URL") or os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def test_second_l2_reminder_persists_exact_channel_intents_and_advances_schedule(
    database_url: str,
) -> None:
    """The second due reminder reaches L2 and enabled manager channels once."""
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        now = datetime.now(UTC).replace(microsecond=0)
        suffix = uuid4().hex[:12]
        ticket_suffix = f"{uuid4().int % 1_000_000:06d}"
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"ie02-l2-{suffix}", f"IE02 L2 {suffix}"),
            )
            l2_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"ie02-manager-{suffix}", f"IE02 Manager {suffix}"),
            )
            manager_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s), (%s, %s)",
                (l2_id, int(RoleId.L2), manager_id, int(RoleId.MANAGER)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24) VALUES (%s, %s, %s, true, true), (%s, %s, %s, true, false)",
                (
                    l2_id,
                    f"tg-l2-{suffix}",
                    f"bx-l2-{suffix}",
                    manager_id,
                    f"tg-manager-{suffix}",
                    f"bx-manager-{suffix}",
                ),
            )
            cursor.execute(
                """INSERT INTO connection_cards
                   (omnidesk_ticket_number, status_code, planned_start_at,
                    planned_duration_minutes, l2_engineer_id)
                   VALUES (%s, 1, %s, 60, %s) RETURNING id""",
                (f"998-{ticket_suffix}", now + timedelta(days=1), l2_id),
            )
            card_id = cursor.fetchone()["id"]

            interval = 60
            anchor = now - timedelta(seconds=interval * 2)
            cursor.execute(
                """INSERT INTO reminder_schedules
                   (card_id, kind, owner_id, anchor_at, interval_seconds,
                    escalation_after_count, next_due_at, last_count,
                    settings_snapshot)
                   VALUES (%s, 'l2_reminder', %s, %s, %s, 2, %s, 1, %s)
                   RETURNING id""",
                (
                    card_id,
                    l2_id,
                    anchor,
                    interval,
                    now - timedelta(seconds=1),
                    Jsonb({"kind": "l2_reminder", "owner_id": l2_id}),
                ),
            )
            reminder_id = cursor.fetchone()["id"]

        notifications = PostgresNotificationService(connection)
        scanner = ReminderService(PostgresReminderRepository(connection), notifications)
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT COALESCE(sum(
                          (CASE WHEN us.telegram_chat_id IS NOT NULL AND us.telegram_chat_id <> '' AND us.notify_telegram THEN 1 ELSE 0 END) +
                          (CASE WHEN us.bitrix24_user_id IS NOT NULL AND us.bitrix24_user_id <> '' AND us.notify_bitrix24 THEN 1 ELSE 0 END)
                       ), 0) AS channels
                   FROM users u
                   JOIN user_roles ur ON ur.user_id=u.id AND ur.role_id=%s
                   JOIN user_settings us ON us.user_id=u.id
                   WHERE u.is_active""",
                (int(RoleId.MANAGER),),
            )
            expected_intents = 2 + cursor.fetchone()["channels"]
        assert scanner.scan(now=now, batch_size=10) == expected_intents
        # The schedule was advanced beyond the scan time, so replay cannot add intents.
        assert scanner.scan(now=now, batch_size=10) == 0

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, event_type_code, comment, new_values FROM card_events WHERE card_id=%s AND comment='timer_reminder'",
                (card_id,),
            )
            event = cursor.fetchone()
            assert event is not None
            assert event["new_values"] == {"timer": "l2_reminder", "count": 2}
            source_event_id = event["id"]

            cursor.execute(
                "SELECT recipient_user_id, channel_code, event_type_code, source_event_id, source_event_type_code, payload, dedupe_key FROM notifications WHERE card_id=%s ORDER BY recipient_user_id, channel_code",
                (card_id,),
            )
            persisted = [dict(row) for row in cursor.fetchall()]

            # The L2 has both channels. Managers are selected by role/settings;
            # notification preferences exclude any disabled channel.
            cursor.execute(
                """SELECT u.id, us.telegram_chat_id, us.bitrix24_user_id,
                          us.notify_telegram, us.notify_bitrix24
                   FROM users u
                   JOIN user_roles ur ON ur.user_id=u.id AND ur.role_id=%s
                   JOIN user_settings us ON us.user_id=u.id
                   WHERE u.is_active""",
                (int(RoleId.MANAGER),),
            )
            managers = cursor.fetchall()

            expected = {
                (l2_id, 0, 4),
                (l2_id, 1, 4),
            }
            expected.update(
                (row["id"], channel, 2)
                for row in managers
                for channel, configured, enabled in (
                    (0, row["telegram_chat_id"], row["notify_telegram"]),
                    (1, row["bitrix24_user_id"], row["notify_bitrix24"]),
                )
                if configured and enabled
            )
            actual = {
                (row["recipient_user_id"], row["channel_code"], row["event_type_code"])
                for row in persisted
            }
            assert actual == expected
            assert len(persisted) == len(expected)
            assert all(row["source_event_id"] == source_event_id for row in persisted)
            assert all(row["source_event_type_code"] == 4 for row in persisted)
            assert all(
                row["payload"]
                == {
                    "card_id": card_id,
                    "assignment": (
                        "l2" if row["event_type_code"] == 4 else "manager_escalation"
                    ),
                }
                for row in persisted
            )
            assert len({row["dedupe_key"] for row in persisted}) == len(expected)
            assert (manager_id, 0, 2) in actual
            assert (manager_id, 1, 2) not in actual

            # Replaying one identical persisted intent is idempotent at the DB boundary.
            assert not notifications.notify(
                event="l2_reminder",
                card_id=card_id,
                source_event_id=source_event_id,
                source_event_type=4,
                recipient_user_id=l2_id,
                channel="telegram",
                payload={"card_id": card_id, "assignment": "l2"},
            )
            cursor.execute(
                "SELECT next_due_at, last_count, escalation_sent, closed_at FROM reminder_schedules WHERE id=%s",
                (reminder_id,),
            )
            schedule = cursor.fetchone()
            assert schedule["next_due_at"] == anchor + timedelta(seconds=interval * 3)
            assert schedule["last_count"] == 2
            assert schedule["escalation_sent"] is True
            assert schedule["closed_at"] is None
            cursor.execute(
                "SELECT status_code, l2_engineer_id FROM connection_cards WHERE id=%s",
                (card_id,),
            )
            card = cursor.fetchone()
            assert card == {"status_code": 1, "l2_engineer_id": l2_id}
    finally:
        # Fixture rows, events and intents are confined to this transaction.
        connection.rollback()
        connection.close()


def test_l1_pre_informed_repeat_and_post_informed_escalation_gate(
    database_url: str,
) -> None:
    """L1 escalation repeats at its snapshotted delay; informed mode suppresses it."""
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        first_due = datetime.now(UTC).replace(microsecond=0)
        suffix = uuid4().hex[:12]
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"ie02-l1-{suffix}", f"IE02 L1 {suffix}"),
            )
            l1_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"ie02-l1-manager-{suffix}", f"IE02 L1 manager {suffix}"),
            )
            manager_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s), (%s, %s)",
                (l1_id, int(RoleId.L1), manager_id, int(RoleId.MANAGER)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24) VALUES (%s, %s, %s, true, true), (%s, %s, %s, true, false)",
                (
                    l1_id,
                    f"tg-l1-{suffix}",
                    f"bx-l1-{suffix}",
                    manager_id,
                    f"tg-l1-manager-{suffix}",
                    f"bx-l1-manager-{suffix}",
                ),
            )

            interval = 60
            anchor = first_due - timedelta(seconds=interval * 2)
            cards: dict[str, tuple[int, int]] = {}
            for mode, informed in (("pre_informed", False), ("post_informed", True)):
                ticket = f"997-{uuid4().int % 1_000_000:06d}"
                cursor.execute(
                    """INSERT INTO connection_cards
                       (omnidesk_ticket_number, status_code, planned_start_at,
                        planned_duration_minutes, l1_owner_id, client_informed)
                       VALUES (%s, 1, %s, 60, %s, %s) RETURNING id""",
                    (ticket, first_due + timedelta(days=1), l1_id, informed),
                )
                card_id = cursor.fetchone()["id"]
                cursor.execute(
                    """INSERT INTO reminder_schedules
                       (card_id, kind, owner_id, anchor_at, interval_seconds,
                        escalation_after_count, next_due_at, last_count,
                        settings_snapshot)
                       VALUES (%s, 'l1_reminder', %s, %s, %s, 2, %s, 1, %s)
                       RETURNING id""",
                    (
                        card_id,
                        l1_id,
                        anchor,
                        interval,
                        first_due - timedelta(seconds=1),
                        Jsonb(
                            {
                                "kind": "l1_reminder",
                                "owner_id": l1_id,
                                "l1_mode": mode,
                                "manager_repeat_seconds": 1800,
                            }
                        ),
                    ),
                )
                cards[mode] = (card_id, cursor.fetchone()["id"])

            cursor.execute(
                """SELECT COALESCE(sum(
                          (CASE WHEN us.telegram_chat_id IS NOT NULL AND us.telegram_chat_id <> '' AND us.notify_telegram THEN 1 ELSE 0 END) +
                          (CASE WHEN us.bitrix24_user_id IS NOT NULL AND us.bitrix24_user_id <> '' AND us.notify_bitrix24 THEN 1 ELSE 0 END)
                       ), 0) AS channels
                   FROM users u
                   JOIN user_roles ur ON ur.user_id=u.id AND ur.role_id=%s
                   JOIN user_settings us ON us.user_id=u.id
                   WHERE u.is_active""",
                (int(RoleId.MANAGER),),
            )
            manager_channels = cursor.fetchone()["channels"]

        scanner = ReminderService(
            PostgresReminderRepository(connection),
            PostgresNotificationService(connection),
        )
        l1_channels = 2
        expected_per_scan = len(cards) * l1_channels + manager_channels
        assert scanner.scan(now=first_due, batch_size=10) == expected_per_scan

        # At +31 minutes each interval-based schedule is due again. The pre-informed
        # snapshot permits another escalation after its 30-minute repeat delay.
        repeated_due = first_due + timedelta(minutes=31)
        assert scanner.scan(now=repeated_due, batch_size=10) == expected_per_scan
        assert scanner.scan(now=repeated_due, batch_size=10) == 0

        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT u.id, us.telegram_chat_id, us.bitrix24_user_id,
                          us.notify_telegram, us.notify_bitrix24
                   FROM users u
                   JOIN user_roles ur ON ur.user_id=u.id AND ur.role_id=%s
                   JOIN user_settings us ON us.user_id=u.id
                   WHERE u.is_active""",
                (int(RoleId.MANAGER),),
            )
            managers = cursor.fetchall()
            expected_manager_channels = {
                (row["id"], channel)
                for row in managers
                for channel, recipient, enabled in (
                    (0, row["telegram_chat_id"], row["notify_telegram"]),
                    (1, row["bitrix24_user_id"], row["notify_bitrix24"]),
                )
                if recipient and enabled
            }
            assert (manager_id, 0) in expected_manager_channels
            assert (manager_id, 1) not in expected_manager_channels

            for mode, (card_id, reminder_id) in cards.items():
                cursor.execute(
                    "SELECT id, new_values FROM card_events WHERE card_id=%s AND comment='timer_reminder' ORDER BY id",
                    (card_id,),
                )
                events = cursor.fetchall()
                assert len(events) == 2
                assert [event["new_values"] for event in events] == [
                    {"timer": "l1_reminder", "count": 2},
                    {"timer": "l1_reminder", "count": 33},
                ]
                source_ids = {event["id"] for event in events}

                cursor.execute(
                    """SELECT recipient_user_id, channel_code, event_type_code,
                              source_event_id, source_event_type_code, payload, dedupe_key
                       FROM notifications WHERE card_id=%s
                       ORDER BY source_event_id, recipient_user_id, channel_code""",
                    (card_id,),
                )
                persisted = [dict(row) for row in cursor.fetchall()]
                expected = {
                    (l1_id, channel, 5, source_event_id)
                    for source_event_id in source_ids
                    for channel in (0, 1)
                }
                if mode == "pre_informed":
                    expected.update(
                        (recipient_id, channel, 2, source_event_id)
                        for source_event_id in source_ids
                        for recipient_id, channel in expected_manager_channels
                    )
                actual = {
                    (
                        row["recipient_user_id"],
                        row["channel_code"],
                        row["event_type_code"],
                        row["source_event_id"],
                    )
                    for row in persisted
                }
                assert actual == expected
                assert len(persisted) == len(expected)
                assert all(row["source_event_id"] in source_ids for row in persisted)
                assert all(row["source_event_type_code"] == 4 for row in persisted)
                assert all(
                    row["payload"]
                    == {
                        "card_id": card_id,
                        "assignment": (
                            "l1"
                            if row["event_type_code"] == 5
                            else "manager_escalation"
                        ),
                    }
                    for row in persisted
                )
                assert len({row["dedupe_key"] for row in persisted}) == len(expected)

                cursor.execute(
                    "SELECT next_due_at, last_count, escalation_sent, last_escalated_at, closed_at FROM reminder_schedules WHERE id=%s",
                    (reminder_id,),
                )
                schedule = cursor.fetchone()
                assert schedule["next_due_at"] == anchor + timedelta(
                    seconds=34 * interval
                )
                assert schedule["last_count"] == 33
                assert schedule["closed_at"] is None
                if mode == "pre_informed":
                    assert schedule["escalation_sent"] is True
                    assert schedule["last_escalated_at"] == repeated_due
                else:
                    assert schedule["escalation_sent"] is False
                    assert schedule["last_escalated_at"] is None

                cursor.execute(
                    "SELECT status_code, l1_owner_id, client_informed FROM connection_cards WHERE id=%s",
                    (card_id,),
                )
                card = cursor.fetchone()
                assert card == {
                    "status_code": 1,
                    "l1_owner_id": l1_id,
                    "client_informed": mode == "post_informed",
                }
    finally:
        # Both cards, users and all dependent rows are rolled back together.
        connection.rollback()
        connection.close()

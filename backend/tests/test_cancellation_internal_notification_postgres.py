from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
import pytest

from app.admin.repository import AdministrativeRepository
from app.cards.constants import CardEventType, CardStatus, RoleId
from app.cards.repository import PostgresCardRepository
from app.cards.service import CardService
from app.notifications import (
    NOTIFICATION_EVENT_CODES,
    PostgresNotificationService,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("RDM_PG_INTEGRATION"),
    reason="RDM_PG_INTEGRATION environment variable not set",
)


@pytest.fixture
def database_url() -> str:
    return (
        os.getenv("PSYCOPG_DATABASE_URL")
        or os.environ.get("DATABASE_URL", "").replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        or "postgresql://nimda:nimda@postgres:5432/rdm"
    )


@pytest.fixture
def connection(database_url: str):
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        yield conn
        conn.rollback()


def next_unique_str(prefix: str = "user") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def next_valid_ticket() -> str:
    return f"{uuid4().int % 900 + 100}-{(uuid4().int % 900000) + 100000}"


def create_test_user(cursor: psycopg.Cursor, role_ids: list[int]) -> int:
    username = next_unique_str("user")
    cursor.execute(
        """
        INSERT INTO users (username, password_hash, full_name, is_active, omnidesk_staff_id)
        VALUES (%s, 'hash', 'Test User', true, %s)
        RETURNING id
        """,
        (username, str(uuid4().int % 10**15)),
    )
    user_id = cursor.fetchone()["id"]
    for role_id in role_ids:
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
            (user_id, role_id),
        )
    cursor.execute(
        """
        INSERT INTO user_settings (user_id, notify_telegram, notify_bitrix24, telegram_chat_id, bitrix24_user_id)
        VALUES (%s, true, true, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET notify_telegram = true, notify_bitrix24 = true
        """,
        (user_id, f"tg_{user_id}", f"bx_{user_id}"),
    )
    return user_id


def create_test_card(
    cursor: psycopg.Cursor,
    ticket_num: str,
    l1_id: int | None = None,
    l2_id: int | None = None,
) -> tuple[int, Any]:
    case_id = f"case_{ticket_num}"
    cursor.execute(
        "INSERT INTO omnidesk_case_index (case_id, case_number, status, omnidesk_created_at, omnidesk_updated_at) VALUES (%s, %s, %s, now(), now()) ON CONFLICT DO NOTHING",
        (case_id, ticket_num, "open"),
    )
    cursor.execute(
        """
        INSERT INTO connection_cards (
            omnidesk_ticket_number, status_code, criticality_code, urgency_code,
            planned_start_at, planned_duration_minutes, assignment_method_code,
            out_of_hours_flag, retroactive_flag, created_source_code,
            l1_owner_id, l2_engineer_id
        )
        VALUES (%s, 1, 0, 0, now() + interval '2 hours', 60, 0, false, false, 0, %s, %s)
        RETURNING id, public_id
        """,
        (ticket_num, l1_id, l2_id),
    )
    row = cursor.fetchone()
    return row["id"], row["public_id"]


def test_card_cancellation_persists_internal_intents_in_postgres(
    connection: psycopg.Connection,
) -> None:
    card_repo = PostgresCardRepository(connection)
    notif_service = PostgresNotificationService(connection)
    card_service = CardService(repository=card_repo, notifications=notif_service)
    ticket_num = next_valid_ticket()

    with connection.cursor() as cursor:
        manager_id = create_test_user(cursor, [int(RoleId.MANAGER)])
        l1_id = create_test_user(cursor, [int(RoleId.L1)])
        l2_id = create_test_user(cursor, [int(RoleId.L2)])
        card_id, public_id = create_test_card(
            cursor, ticket_num, l1_id=l1_id, l2_id=l2_id
        )
    manager_ids = {row.user_id for row in card_repo.list_active_manager_recipients()}

    cancelled_card = card_service.cancel_card(
        public_id,
        actor_user_id=manager_id,
        comment="Cancelled test",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert cancelled_card.status_code == int(CardStatus.CANCELLED)

    event_code_cancellation = NOTIFICATION_EVENT_CODES["card_cancelled"]
    assert event_code_cancellation == 9

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT card_id, recipient_user_id, channel_code, event_type_code,
                   source_event_id, source_event_type_code, payload, dedupe_key
            FROM notifications
            WHERE card_id = %s AND event_type_code = %s
            ORDER BY recipient_user_id, channel_code
            """,
            (card_id, event_code_cancellation),
        )
        rows = cursor.fetchall()
        cursor.execute(
            "SELECT id FROM card_events WHERE card_id = %s AND event_type_code = %s ORDER BY id DESC LIMIT 1",
            (card_id, int(CardEventType.STATUS_CHANGED)),
        )
        status_event_id = cursor.fetchone()["id"]

    recipients = {
        (row["recipient_user_id"], row["channel_code"], row["payload"]["assignment"])
        for row in rows
    }
    expected_assignments = dict.fromkeys(manager_ids, "manager_escalation")
    expected_assignments.setdefault(l1_id, "l1")
    expected_assignments.setdefault(l2_id, "l2")
    expected = {
        (user_id, channel, assignment)
        for user_id, assignment in expected_assignments.items()
        for channel in (0, 1)
    }
    assert manager_id in manager_ids
    assert recipients == expected
    assert len(rows) == len(expected)
    assert len({row["dedupe_key"] for row in rows}) == len(rows)
    for row in rows:
        assert row["source_event_id"] == status_event_id
        assert row["source_event_type_code"] == int(CardEventType.STATUS_CHANGED)
        assert row["payload"]["card_id"] == card_id


def test_cancellation_internal_notification_independent_of_public_toggle(
    connection: psycopg.Connection,
) -> None:
    card_repo = PostgresCardRepository(connection)
    admin_repo = AdministrativeRepository(connection)
    notif_service = PostgresNotificationService(connection)
    card_service = CardService(repository=card_repo, notifications=notif_service)
    ticket_num = next_valid_ticket()

    with connection.cursor() as cursor:
        manager_id = create_test_user(cursor, [int(RoleId.MANAGER)])
        card_id, public_id = create_test_card(cursor, ticket_num)

    # Disable public cancellation notification setting
    admin_repo.set_cancellation_public_notification_settings(
        enabled=False,
        template="Отменено",
        actor_user_id=manager_id,
    )

    # Cancel card
    card_service.cancel_card(
        public_id,
        actor_user_id=manager_id,
        comment="Test public toggle independence",
        ip_address=None,
        user_agent=None,
    )

    event_code_cancellation = NOTIFICATION_EVENT_CODES["card_cancelled"]

    # Internal notification MUST be created in notifications table regardless of public toggle
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, recipient_user_id, event_type_code, status_code
            FROM notifications
            WHERE card_id = %s AND event_type_code = %s
            """,
            (card_id, event_code_cancellation),
        )
        internal_rows = cursor.fetchall()

    assert len(internal_rows) >= 2  # Telegram + Bitrix24 for manager
    for row in internal_rows:
        assert row["event_type_code"] == 9


def test_cancellation_internal_notification_deduplication(
    connection: psycopg.Connection,
) -> None:
    card_repo = PostgresCardRepository(connection)
    notif_service = PostgresNotificationService(connection)
    card_service = CardService(repository=card_repo, notifications=notif_service)
    ticket_num = next_valid_ticket()

    with connection.cursor() as cursor:
        manager_id = create_test_user(cursor, [int(RoleId.MANAGER)])
        card_id, public_id = create_test_card(cursor, ticket_num)

    cancelled_card = card_service.cancel_card(
        public_id,
        actor_user_id=manager_id,
        comment="Cancellation 1",
        ip_address=None,
        user_agent=None,
    )

    event_code_cancellation = NOTIFICATION_EVENT_CODES["card_cancelled"]

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) as cnt FROM notifications WHERE card_id = %s AND event_type_code = %s",
            (card_id, event_code_cancellation),
        )
        count_1 = cursor.fetchone()["cnt"]

    # Re-running notification logic with same card & source event ID
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT source_event_id FROM notifications WHERE card_id = %s AND event_type_code = %s LIMIT 1",
            (card_id, event_code_cancellation),
        )
        source_event_id = cursor.fetchone()["source_event_id"]
    card_service._notify_card_cancellation(
        card=cancelled_card, source_event_id=source_event_id
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) as cnt FROM notifications WHERE card_id = %s AND event_type_code = %s",
            (card_id, event_code_cancellation),
        )
        count_2 = cursor.fetchone()["cnt"]

    # Deduplication ensures count does NOT increase
    assert count_1 == count_2

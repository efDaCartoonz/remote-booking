import os
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import CardStatus, RoleId
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.notifications import NOTIFICATION_EVENT_CODES, PostgresNotificationService
from app.reminders import PostgresReminderRepository, ReminderService

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture
def pg_tx():
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        u_suffix = uuid.uuid4().hex[:8]
        ticket_number = f"{uuid.uuid4().int % 900 + 100:03d}-{(uuid.uuid4().int % 900000 + 100000):06d}"

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, full_name, is_active)
                VALUES (%s, 'test', %s, true)
                RETURNING id
                """,
                (f"ie02_mgr_{u_suffix}", f"IE02 Manager {u_suffix}"),
            )
            mgr_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                INSERT INTO users (username, password_hash, full_name, is_active)
                VALUES (%s, 'test', %s, true)
                RETURNING id
                """,
                (f"ie02_l1_{u_suffix}", f"IE02 L1 {u_suffix}"),
            )
            l1_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                INSERT INTO users (username, password_hash, full_name, is_active)
                VALUES (%s, 'test', %s, true)
                RETURNING id
                """,
                (f"ie02_l2_{u_suffix}", f"IE02 L2 {u_suffix}"),
            )
            l2_id = cursor.fetchone()["id"]

            for uid, role in (
                (mgr_id, RoleId.MANAGER),
                (l1_id, RoleId.L1),
                (l2_id, RoleId.L2),
            ):
                cursor.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                    (uid, int(role)),
                )

            # Active recipient channels for manager escalation
            cursor.execute(
                """
                INSERT INTO user_settings (
                    user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24
                )
                VALUES (%s, %s, %s, true, true)
                """,
                (mgr_id, f"tg_mgr_{mgr_id}", f"bx_mgr_{mgr_id}"),
            )

            # Active recipient channels for L1 follow-up
            cursor.execute(
                """
                INSERT INTO user_settings (
                    user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24
                )
                VALUES (%s, %s, %s, true, true)
                """,
                (l1_id, f"tg_l1_{l1_id}", f"bx_l1_{l1_id}"),
            )

            for user_id, pool in ((l1_id, 1), (l2_id, 2)):
                cursor.execute(
                    """
                    INSERT INTO distribution_members (user_id, pool_code, is_enabled)
                    VALUES (%s, %s, true)
                    """,
                    (user_id, pool),
                )
                for weekday in range(1, 8):
                    cursor.execute(
                        """
                        INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone)
                        VALUES (%s, %s, '00:00', '23:59:59', 'UTC')
                        """,
                        (user_id, weekday),
                    )

        yield {
            "connection": connection,
            "mgr_id": mgr_id,
            "l1_id": l1_id,
            "l2_id": l2_id,
            "ticket_number": ticket_number,
        }
    finally:
        connection.rollback()
        connection.close()


def test_assigned_card_overdue_without_l2_decision_postgres(pg_tx):
    connection = pg_tx["connection"]
    mgr_id = pg_tx["mgr_id"]
    l1_id = pg_tx["l1_id"]
    l2_id = pg_tx["l2_id"]
    ticket_number = pg_tx["ticket_number"]

    service = CardService(PostgresCardRepository(connection))

    # 1. Create ASSIGNED card with synthetic unique ticket (NNN-NNNNNN format)
    card = service.create_card(
        CardCreateRequest(
            omnidesk_ticket_number=ticket_number,
            planned_start_at=datetime.now(UTC) + timedelta(days=1),
            planned_duration_minutes=60,
        ),
        actor_user_id=mgr_id,
        ip_address=None,
        user_agent=None,
    )
    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == l2_id

    # 2. Simulate reaching planned end without L2 decision
    now = datetime.now(UTC)
    planned_start = now - timedelta(hours=2)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE connection_cards
            SET planned_start_at=%s, planned_duration_minutes=60
            WHERE id=%s
            """,
            (planned_start, card.id),
        )
        cursor.execute(
            """
            UPDATE reminder_schedules
            SET next_due_at=%s
            WHERE card_id=%s AND kind='l2_reminder' AND closed_at IS NULL
            """,
            (now - timedelta(minutes=1), card.id),
        )

    # 3. Run actual ReminderService scan with PostgresNotificationService
    scanner = ReminderService(
        PostgresReminderRepository(connection),
        PostgresNotificationService(connection),
    )

    first_scan_intents = scanner.scan(now=now, batch_size=10)
    assert first_scan_intents > 0

    # 4. Assertions on card status & overdue flag
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT overdue_at, overdue_flag, status_code, l1_owner_id, l2_engineer_id
            FROM connection_cards
            WHERE id=%s
            """,
            (card.id,),
        )
        card_row = dict(cursor.fetchone())
        assert card_row["overdue_at"] is not None
        assert card_row["overdue_flag"] is True
        assert card_row["status_code"] == int(CardStatus.ASSIGNED)
        assert card_row["l1_owner_id"] == l1_id
        assert card_row["l2_engineer_id"] == l2_id

        # 5. Verify L2 reminder is closed and L1 reminder is active
        cursor.execute(
            """
            SELECT count(*) FROM reminder_schedules
            WHERE card_id=%s AND kind='l2_reminder' AND closed_at IS NOT NULL
            """,
            (card.id,),
        )
        assert cursor.fetchone()["count"] == 1

        cursor.execute(
            """
            SELECT count(*) FROM reminder_schedules
            WHERE card_id=%s AND kind='l2_reminder' AND closed_at IS NULL
            """,
            (card.id,),
        )
        assert cursor.fetchone()["count"] == 0

        cursor.execute(
            """
            SELECT owner_id, closed_at FROM reminder_schedules
            WHERE card_id=%s AND kind='l1_reminder'
            """,
            (card.id,),
        )
        l1_schedules = [dict(r) for r in cursor.fetchall()]
        assert len(l1_schedules) == 1
        assert l1_schedules[0]["owner_id"] == l1_id
        assert l1_schedules[0]["closed_at"] is None

        # 6. Verify real card_events IDs for l2_overdue and l1_assigned
        cursor.execute(
            "SELECT id, comment FROM card_events WHERE card_id=%s ORDER BY id",
            (card.id,),
        )
        events = {r["comment"]: r["id"] for r in cursor.fetchall()}
        assert "l2_overdue" in events
        assert "l1_assigned" in events
        overdue_event_id = events["l2_overdue"]
        l1_assigned_event_id = events["l1_assigned"]
        assert overdue_event_id > 0
        assert l1_assigned_event_id > 0

        # 7. Verify l1_followup intents for enabled channels
        cursor.execute(
            """
            SELECT id, recipient_user_id, channel_code, event_type_code, source_event_id, dedupe_key
            FROM notifications
            WHERE card_id=%s AND event_type_code=%s
            ORDER BY id
            """,
            (card.id, NOTIFICATION_EVENT_CODES["l1_followup"]),
        )
        l1_intents = [dict(r) for r in cursor.fetchall()]
        assert len(l1_intents) == 2
        assert {r["recipient_user_id"] for r in l1_intents} == {l1_id}
        assert {r["channel_code"] for r in l1_intents} == {0, 1}
        for intent in l1_intents:
            assert intent["source_event_id"] == l1_assigned_event_id

        # 8. Verify manager_escalation intents for active managers/channels
        cursor.execute(
            """
            SELECT id, recipient_user_id, channel_code, event_type_code, source_event_id, dedupe_key
            FROM notifications
            WHERE card_id=%s AND event_type_code=%s
            ORDER BY id
            """,
            (card.id, NOTIFICATION_EVENT_CODES["manager_escalation"]),
        )
        mgr_intents = [dict(r) for r in cursor.fetchall()]
        assert len(mgr_intents) == 2
        assert {r["recipient_user_id"] for r in mgr_intents} == {mgr_id}
        assert {r["channel_code"] for r in mgr_intents} == {0, 1}
        for intent in mgr_intents:
            assert intent["source_event_id"] == overdue_event_id

        # 9. Verify unique recipient/channel/event deduplication keys
        cursor.execute(
            "SELECT dedupe_key FROM notifications WHERE card_id=%s",
            (card.id,),
        )
        dedupe_keys = [r["dedupe_key"] for r in cursor.fetchall()]
        assert len(dedupe_keys) == 4
        assert len(set(dedupe_keys)) == 4
        for key in dedupe_keys:
            assert key.startswith("notification:")

    # 10. Run second scan and verify no duplicate intents are created
    second_scan_intents = scanner.scan(now=now, batch_size=10)
    assert second_scan_intents == 0

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM notifications WHERE card_id=%s",
            (card.id,),
        )
        assert cursor.fetchone()["count"] == 4

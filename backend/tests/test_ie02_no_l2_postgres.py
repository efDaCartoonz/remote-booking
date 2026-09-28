"""IE-02 PostgreSQL integration test for no eligible L2 at initial distribution."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import CardEventType, CardStatus, DistributionPool, RoleId
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.notifications import PostgresNotificationService

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture
def database_url() -> str:
    return os.getenv("PSYCOPG_DATABASE_URL") or os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def test_no_eligible_l2_initial_distribution_assigns_l1_and_persists_notifications(
    database_url: str,
    role_notification_channels,
) -> None:
    """Initial distribution with no eligible L2 sets card status to REJECTED,

    assigns an available L1, creates an active L1 reminder, and persists
    manager escalation and L1 follow-up notification intents for enabled
    channels.
    """
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        suffix = uuid4().hex[:12]
        ticket_number = (
            f"{uuid4().int % 900 + 100:03d}-{uuid4().int % 900000 + 100000:06d}"
        )
        now = datetime.now(UTC).replace(microsecond=0)
        planned_start_at = now + timedelta(days=1)

        with connection.cursor() as cursor:
            # Verify explicit no-L2 precondition
            cursor.execute(
                "SELECT count(*) FROM distribution_members WHERE pool_code = %s AND is_enabled = true",
                (int(DistributionPool.L2),),
            )
            assert cursor.fetchone()["count"] == 0

            # Insert Active Manager
            cursor.execute(
                """INSERT INTO users (username, password_hash, full_name)
                   VALUES (%s, 'test', %s) RETURNING id""",
                (f"ie02-mgr-{suffix}", f"IE02 Manager {suffix}"),
            )
            manager_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (manager_id, int(RoleId.MANAGER)),
            )

            cursor.execute(
                """INSERT INTO user_settings
                   (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24)
                   VALUES (%s, %s, %s, true, true)""",
                (manager_id, f"tg-mgr-{suffix}", f"bx-mgr-{suffix}"),
            )

            # Insert Available L1
            cursor.execute(
                """INSERT INTO users (username, password_hash, full_name)
                   VALUES (%s, 'test', %s) RETURNING id""",
                (f"ie02-l1-{suffix}", f"IE02 L1 {suffix}"),
            )
            l1_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (l1_id, int(RoleId.L1)),
            )

            cursor.execute(
                """INSERT INTO user_settings
                   (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24)
                   VALUES (%s, %s, %s, true, true)""",
                (l1_id, f"tg-l1-{suffix}", f"bx-l1-{suffix}"),
            )

            cursor.execute(
                """INSERT INTO distribution_members (user_id, pool_code, is_enabled)
                   VALUES (%s, %s, true)""",
                (l1_id, int(DistributionPool.L1)),
            )

            for weekday in range(1, 8):
                cursor.execute(
                    """INSERT INTO schedules
                       (user_id, weekday, start_time, end_time, timezone)
                       VALUES (%s, %s, '00:00:00', '23:59:59', 'UTC')""",
                    (l1_id, weekday),
                )

        repository = PostgresCardRepository(connection)
        notifications = PostgresNotificationService(connection)
        service = CardService(repository, notifications=notifications)

        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=ticket_number,
                planned_start_at=planned_start_at,
                planned_duration_minutes=60,
            ),
            actor_user_id=manager_id,
            ip_address=None,
            user_agent=None,
        )

        # Assertions on returned CardRecord
        assert CardStatus(card.status_code) == CardStatus.REJECTED
        assert card.l1_owner_id == l1_id
        assert card.l2_engineer_id is None
        assert card.unsuccessful_cycle_count == 1

        with connection.cursor() as cursor:
            # DB verification of card status and assignments
            cursor.execute(
                """SELECT status_code, l1_owner_id, l2_engineer_id, unsuccessful_cycle_count
                   FROM connection_cards WHERE id = %s""",
                (card.id,),
            )
            db_card = cursor.fetchone()
            assert db_card["status_code"] == int(CardStatus.REJECTED)
            assert db_card["l1_owner_id"] == l1_id
            assert db_card["l2_engineer_id"] is None
            assert db_card["unsuccessful_cycle_count"] == 1

            # Assert active L1 reminder schedule exists
            cursor.execute(
                """SELECT id, kind, owner_id, closed_at
                   FROM reminder_schedules
                   WHERE card_id = %s""",
                (card.id,),
            )
            reminders = cursor.fetchall()
            assert len(reminders) == 1
            l1_reminder = reminders[0]
            assert l1_reminder["kind"] == "l1_reminder"
            assert l1_reminder["owner_id"] == l1_id
            assert l1_reminder["closed_at"] is None

            # Retrieve card events to get real event IDs
            cursor.execute(
                """SELECT id, event_type_code, comment
                   FROM card_events
                   WHERE card_id = %s
                   ORDER BY id ASC""",
                (card.id,),
            )
            events = cursor.fetchall()
            mgr_escalation_event = next(
                e for e in events if e["comment"] == "no_available_l2_candidates"
            )
            assert mgr_escalation_event["event_type_code"] == int(
                CardEventType.STATUS_CHANGED
            )

            l1_assigned_event = next(e for e in events if e["comment"] == "l1_assigned")
            assert l1_assigned_event["event_type_code"] == int(
                CardEventType.ENGINEER_ASSIGNED
            )

            # Assert persisted notification intents in DB
            cursor.execute(
                """SELECT recipient_user_id, channel_code, event_type_code,
                          source_event_id, source_event_type_code, payload, dedupe_key
                   FROM notifications
                   WHERE card_id = %s
                   ORDER BY recipient_user_id, channel_code""",
                (card.id,),
            )
            persisted = [dict(row) for row in cursor.fetchall()]
            manager_channels = role_notification_channels(connection, (RoleId.MANAGER,))
            assert {(manager_id, 0), (manager_id, 1)} <= manager_channels
            assert len(persisted) == len(manager_channels) + 2

            manager_ids = {recipient_id for recipient_id, _ in manager_channels}
            mgr_intents = [
                n for n in persisted if n["recipient_user_id"] in manager_ids
            ]
            l1_intents = [n for n in persisted if n["recipient_user_id"] == l1_id]

            assert len(mgr_intents) == len(manager_channels)
            assert len(l1_intents) == 2
            assert {
                (intent["recipient_user_id"], intent["channel_code"])
                for intent in mgr_intents
            } == manager_channels

            for intent in mgr_intents:
                assert intent["event_type_code"] == 2  # manager_escalation
                assert intent["source_event_id"] == mgr_escalation_event["id"]
                assert intent["source_event_type_code"] == int(
                    CardEventType.STATUS_CHANGED
                )
                assert intent["payload"] == {
                    "card_id": card.id,
                    "assignment": "manager_escalation",
                }

            for intent in l1_intents:
                assert intent["event_type_code"] == 3  # l1_followup
                assert intent["source_event_id"] == l1_assigned_event["id"]
                assert intent["source_event_type_code"] == int(
                    CardEventType.ENGINEER_ASSIGNED
                )
                assert intent["payload"] == {
                    "card_id": card.id,
                    "assignment": "l1",
                }

            assert {n["channel_code"] for n in mgr_intents} == {0, 1}
            assert {n["channel_code"] for n in l1_intents} == {0, 1}

            # Exact dedupe cardinality
            dedupe_keys = {n["dedupe_key"] for n in persisted}
            assert len(dedupe_keys) == len(persisted)

        # Replay notification delivery attempt for captured event and verify deduplication
        replay_result = notifications.notify(
            event="l1_followup",
            card_id=card.id,
            source_event_id=l1_assigned_event["id"],
            source_event_type=int(CardEventType.ENGINEER_ASSIGNED),
            recipient_user_id=l1_id,
            channel="telegram",
            payload={"card_id": card.id, "assignment": "l1"},
        )
        assert replay_result is False

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM notifications WHERE card_id = %s",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == len(manager_channels) + 2

    finally:
        connection.rollback()
        connection.close()

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import CardEventType, DistributionPool, RoleId
from app.cards.create_policy import CreateScenario, validate_role_create
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.notifications import (
    NOTIFICATION_CHANNEL_CODES,
    NOTIFICATION_EVENT_CODES,
    PostgresNotificationService,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture
def database_url() -> str:
    url = os.getenv("PSYCOPG_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        pytest.skip(
            "PSYCOPG_DATABASE_URL or DATABASE_URL environment variable is required"
        )
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def test_postgres_l2_urgent_collision_persists_expected_notification_intents(
    database_url: str,
) -> None:
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        now = datetime.now(UTC).replace(microsecond=0)
        suffix = uuid4().hex[:12]
        ticket_normal = (
            f"{uuid4().int % 900 + 100:03d}-{uuid4().int % 900_000 + 100_000:06d}"
        )
        ticket_urgent = (
            f"{uuid4().int % 900 + 100:03d}-{uuid4().int % 900_000 + 100_000:06d}"
        )

        with connection.cursor() as cursor:
            # Create L1 owner
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"ie02-l1-{suffix}", f"IE02 L1 {suffix}"),
            )
            l1_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (l1_id, int(RoleId.L1)),
            )
            cursor.execute(
                "INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (%s, %s, true)",
                (l1_id, int(DistributionPool.L1)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24) "
                "VALUES (%s, %s, %s, true, true)",
                (l1_id, f"tg-l1-{suffix}", f"bx-l1-{suffix}"),
            )

            # Create L2 engineer A (initial engineer for normal card)
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"ie02-l2a-{suffix}", f"IE02 L2A {suffix}"),
            )
            l2_a_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (l2_a_id, int(RoleId.L2)),
            )
            cursor.execute(
                "INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (%s, %s, true)",
                (l2_a_id, int(DistributionPool.L2)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24) "
                "VALUES (%s, %s, %s, true, true)",
                (l2_a_id, f"tg-l2a-{suffix}", f"bx-l2a-{suffix}"),
            )

            # Create L2 engineer B (candidate for displaced card reassignment)
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"ie02-l2b-{suffix}", f"IE02 L2B {suffix}"),
            )
            l2_b_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (l2_b_id, int(RoleId.L2)),
            )
            cursor.execute(
                "INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (%s, %s, true)",
                (l2_b_id, int(DistributionPool.L2)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24) "
                "VALUES (%s, %s, %s, true, true)",
                (l2_b_id, f"tg-l2b-{suffix}", f"bx-l2b-{suffix}"),
            )

            # Schedules for L1 and both L2 engineers
            for user_id in (l1_id, l2_a_id, l2_b_id):
                for weekday in range(1, 8):
                    cursor.execute(
                        "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) "
                        "VALUES (%s, %s, '00:00', '23:59:59.999999', 'UTC')",
                        (user_id, weekday),
                    )

            # Create Manager
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', %s) RETURNING id",
                (f"ie02-mgr-{suffix}", f"IE02 Manager {suffix}"),
            )
            manager_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (manager_id, int(RoleId.MANAGER)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24) "
                "VALUES (%s, %s, %s, true, true)",
                (manager_id, f"tg-mgr-{suffix}", f"bx-mgr-{suffix}"),
            )

        notification_service = PostgresNotificationService(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository, notifications=notification_service)

        planned_start = now + timedelta(hours=3)

        # 1. Create normal card assigned to L2 A
        normal_card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=ticket_normal,
                planned_start_at=planned_start,
                planned_duration_minutes=60,
                l2_engineer_id=l2_a_id,
            ),
            actor_user_id=l1_id,
            ip_address=None,
            user_agent=None,
            manual_assignment=True,
            allow_out_of_hours=True,
        )

        # 2. Create urgent L2 card at the same start time (collides with normal card on L2 A)
        urgent_card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=ticket_urgent,
                planned_start_at=planned_start,
                planned_duration_minutes=60,
            ),
            actor_user_id=l2_a_id,
            ip_address=None,
            user_agent=None,
            allow_out_of_hours=True,
            role_create_plan=validate_role_create(
                scenario=CreateScenario.L2_URGENT,
                planned_start_at=planned_start,
                planned_duration_minutes=60,
                urgent_reason="urgent collision test",
                now=now,
            ),
        )

        # Assert urgent card is assigned to L2 A
        assert urgent_card.status_code == 1
        assert urgent_card.l2_engineer_id == l2_a_id

        with connection.cursor() as cursor:
            # Assert normal card was displaced and reassigned to L2 B
            cursor.execute(
                "SELECT status_code, l2_engineer_id FROM connection_cards WHERE id = %s",
                (normal_card.id,),
            )
            normal_db = cursor.fetchone()
            assert normal_db["status_code"] == 1
            assert normal_db["l2_engineer_id"] == l2_b_id

            # Find the card_events record for displaced card URGENT_COLLISION
            cursor.execute(
                "SELECT id, event_type_code, comment FROM card_events WHERE card_id = %s AND event_type_code = %s",
                (normal_card.id, int(CardEventType.URGENT_COLLISION)),
            )
            event_row = cursor.fetchone()
            assert event_row is not None
            assert event_row["comment"] == "urgent_collision"
            displaced_event_id = event_row["id"]

            # Query collision notification intents for the displaced card matching exact collision source_event_id and event_type_code
            cursor.execute(
                """
                SELECT recipient_user_id, channel_code, event_type_code, source_event_id,
                       source_event_type_code, payload, dedupe_key
                FROM notifications
                WHERE card_id = %s AND source_event_id = %s AND source_event_type_code = %s
                ORDER BY recipient_user_id, channel_code, event_type_code
                """,
                (
                    normal_card.id,
                    displaced_event_id,
                    int(CardEventType.URGENT_COLLISION),
                ),
            )
            intents = [dict(row) for row in cursor.fetchall()]

            # Exactly 6 notification intents expected:
            # (L1, L2A, Manager) x (Telegram, Bitrix24)
            assert len(intents) == 6

            for intent in intents:
                assert intent["source_event_id"] == displaced_event_id
                assert intent["source_event_type_code"] == int(
                    CardEventType.URGENT_COLLISION
                )

            # Build set of key fields to assert exact expected records
            intent_tuples = {
                (
                    intent["recipient_user_id"],
                    intent["channel_code"],
                    intent["event_type_code"],
                    intent["payload"]["assignment"],
                    intent["dedupe_key"],
                )
                for intent in intents
            }

            expected_tuples = {
                # Affected L1: urgent_collision event, assignment="urgent_collision"
                (
                    l1_id,
                    NOTIFICATION_CHANNEL_CODES["telegram"],
                    NOTIFICATION_EVENT_CODES["urgent_collision"],
                    "urgent_collision",
                    f"notification:urgent_collision:{displaced_event_id}:{l1_id}:telegram",
                ),
                (
                    l1_id,
                    NOTIFICATION_CHANNEL_CODES["bitrix24"],
                    NOTIFICATION_EVENT_CODES["urgent_collision"],
                    "urgent_collision",
                    f"notification:urgent_collision:{displaced_event_id}:{l1_id}:bitrix24",
                ),
                # Affected L2: urgent_collision event, assignment="l2"
                (
                    l2_a_id,
                    NOTIFICATION_CHANNEL_CODES["telegram"],
                    NOTIFICATION_EVENT_CODES["urgent_collision"],
                    "l2",
                    f"notification:urgent_collision:{displaced_event_id}:{l2_a_id}:telegram",
                ),
                (
                    l2_a_id,
                    NOTIFICATION_CHANNEL_CODES["bitrix24"],
                    NOTIFICATION_EVENT_CODES["urgent_collision"],
                    "l2",
                    f"notification:urgent_collision:{displaced_event_id}:{l2_a_id}:bitrix24",
                ),
                # Active Manager: manager_escalation event, assignment="manager_escalation"
                (
                    manager_id,
                    NOTIFICATION_CHANNEL_CODES["telegram"],
                    NOTIFICATION_EVENT_CODES["manager_escalation"],
                    "manager_escalation",
                    f"notification:manager_escalation:{displaced_event_id}:{manager_id}:telegram",
                ),
                (
                    manager_id,
                    NOTIFICATION_CHANNEL_CODES["bitrix24"],
                    NOTIFICATION_EVENT_CODES["manager_escalation"],
                    "manager_escalation",
                    f"notification:manager_escalation:{displaced_event_id}:{manager_id}:bitrix24",
                ),
            }

            assert intent_tuples == expected_tuples

        # Verify deduplication: re-calling notify with the same parameters returns False and does not insert duplicates
        duplicate_attempt = notification_service.notify(
            event="urgent_collision",
            card_id=normal_card.id,
            source_event_id=displaced_event_id,
            source_event_type=int(CardEventType.URGENT_COLLISION),
            recipient_user_id=l1_id,
            channel="telegram",
            payload={"card_id": normal_card.id, "assignment": "urgent_collision"},
        )
        assert duplicate_attempt is False

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) FROM notifications
                WHERE card_id = %s AND source_event_id = %s AND source_event_type_code = %s
                """,
                (
                    normal_card.id,
                    displaced_event_id,
                    int(CardEventType.URGENT_COLLISION),
                ),
            )
            assert cursor.fetchone()["count"] == 6
    finally:
        connection.rollback()
        connection.close()

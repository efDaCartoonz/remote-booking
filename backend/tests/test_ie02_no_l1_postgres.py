"""IE-02 PostgreSQL integration test for no eligible L2 and no available L1 at initial distribution."""

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


def test_no_eligible_l2_and_no_available_l1_initial_distribution_leaves_card_rejected(
    database_url: str,
) -> None:
    """Initial distribution with no eligible L2 and no available L1 sets card status to REJECTED,

    leaves l1_owner_id=NULL, creates no active L1 reminder, and persists distinct
    manager escalation notification intents for no L2 and no L1 events without L1 followup intents.
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
            # Verify explicit no-L2 and no-L1 preconditions for distribution pools
            cursor.execute(
                "SELECT count(*) FROM distribution_members WHERE pool_code = %s AND is_enabled = true",
                (int(DistributionPool.L2),),
            )
            assert cursor.fetchone()["count"] == 0

            cursor.execute(
                "SELECT count(*) FROM distribution_members WHERE pool_code = %s AND is_enabled = true",
                (int(DistributionPool.L1),),
            )
            assert cursor.fetchone()["count"] == 0

            # Insert Active Manager configured for both Telegram and Bitrix24
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
        assert card.l1_owner_id is None
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
            assert db_card["l1_owner_id"] is None
            assert db_card["l2_engineer_id"] is None
            assert db_card["unsuccessful_cycle_count"] == 1

            # Assert NO active L1 reminder schedule exists
            cursor.execute(
                """SELECT count(*) FROM reminder_schedules WHERE card_id = %s""",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 0

            # Retrieve card events to get real event IDs for no L2 and no L1 escalations
            cursor.execute(
                """SELECT id, event_type_code, comment
                   FROM card_events
                   WHERE card_id = %s
                   ORDER BY id ASC""",
                (card.id,),
            )
            events = cursor.fetchall()

            mgr_l2_escalation_event = next(
                e for e in events if e["comment"] == "no_available_l2_candidates"
            )
            assert mgr_l2_escalation_event["event_type_code"] == int(
                CardEventType.STATUS_CHANGED
            )

            mgr_l1_escalation_event = next(
                e for e in events if e["comment"] == "no_available_l1_candidates"
            )
            assert mgr_l1_escalation_event["event_type_code"] == int(
                CardEventType.STATUS_CHANGED
            )

            # Assert persisted notification intents in DB
            cursor.execute(
                """SELECT recipient_user_id, channel_code, event_type_code,
                          source_event_id, source_event_type_code, payload, dedupe_key
                   FROM notifications
                   WHERE card_id = %s
                   ORDER BY recipient_user_id, channel_code, id""",
                (card.id,),
            )
            persisted = [dict(row) for row in cursor.fetchall()]
            assert len(persisted) == 4

            # Ensure all intents are for the manager and no L1 followup intents exist
            assert all(n["recipient_user_id"] == manager_id for n in persisted)
            assert all(
                n["event_type_code"] == 2 for n in persisted
            )  # 2 = manager_escalation
            assert not any(
                n["event_type_code"] == 3 for n in persisted
            )  # 3 = l1_followup

            no_l2_intents = [
                n
                for n in persisted
                if n["source_event_id"] == mgr_l2_escalation_event["id"]
            ]
            no_l1_intents = [
                n
                for n in persisted
                if n["source_event_id"] == mgr_l1_escalation_event["id"]
            ]

            assert len(no_l2_intents) == 2
            assert len(no_l1_intents) == 2

            for intent in no_l2_intents:
                assert intent["source_event_type_code"] == int(
                    CardEventType.STATUS_CHANGED
                )
                assert intent["payload"] == {
                    "card_id": card.id,
                    "assignment": "manager_escalation",
                }

            for intent in no_l1_intents:
                assert intent["source_event_type_code"] == int(
                    CardEventType.STATUS_CHANGED
                )
                assert intent["payload"] == {
                    "card_id": card.id,
                    "assignment": "manager_escalation",
                }

            assert {n["channel_code"] for n in no_l2_intents} == {0, 1}
            assert {n["channel_code"] for n in no_l1_intents} == {0, 1}

            # Exact dedupe key cardinality (all 4 notification intents have distinct dedupe_keys)
            dedupe_keys = {n["dedupe_key"] for n in persisted}
            assert len(dedupe_keys) == 4

        # Replay notification delivery attempt for both captured escalation events and verify deduplication
        replay_no_l2 = notifications.notify(
            event="manager_escalation",
            card_id=card.id,
            source_event_id=mgr_l2_escalation_event["id"],
            source_event_type=int(CardEventType.STATUS_CHANGED),
            recipient_user_id=manager_id,
            channel="telegram",
            payload={"card_id": card.id, "assignment": "manager_escalation"},
        )
        assert replay_no_l2 is False

        replay_no_l1 = notifications.notify(
            event="manager_escalation",
            card_id=card.id,
            source_event_id=mgr_l1_escalation_event["id"],
            source_event_type=int(CardEventType.STATUS_CHANGED),
            recipient_user_id=manager_id,
            channel="telegram",
            payload={"card_id": card.id, "assignment": "manager_escalation"},
        )
        assert replay_no_l1 is False

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM notifications WHERE card_id = %s",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 4

    finally:
        connection.rollback()
        connection.close()

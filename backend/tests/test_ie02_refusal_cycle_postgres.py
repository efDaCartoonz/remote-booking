"""IE-02 PostgreSQL integration coverage for repeated full L2 refusal."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import (
    ActorType,
    CardEventType,
    CardStatus,
    DistributionPool,
    RoleId,
)
from app.cards.create_policy import CreateScenario, validate_role_create
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


def test_second_full_l2_refusal_escalates_again_and_raises_criticality(
    database_url: str,
) -> None:
    """Two declined cycles persist separate manager intents and retain creator L1."""
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        suffix = uuid4().hex[:12]
        ticket_number = (
            f"{uuid4().int % 900 + 100:03d}-{uuid4().int % 900000 + 100000:06d}"
        )
        now = datetime.now(UTC).replace(microsecond=0)
        planned_start_at = now + timedelta(days=2)

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM distribution_members WHERE pool_code=%s AND is_enabled=true",
                (int(DistributionPool.L2),),
            )
            assert cursor.fetchone()["count"] == 0

            cursor.execute(
                "INSERT INTO users (username,password_hash,full_name) VALUES (%s,'test',%s) RETURNING id",
                (f"ie02-refusal-manager-{suffix}", f"IE02 Manager {suffix}"),
            )
            manager_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id,role_id) VALUES (%s,%s)",
                (manager_id, int(RoleId.MANAGER)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id,telegram_chat_id,bitrix24_user_id,notify_telegram,notify_bitrix24) VALUES (%s,%s,%s,true,true)",
                (manager_id, f"tg-mgr-{suffix}", f"bx-mgr-{suffix}"),
            )

            cursor.execute(
                "INSERT INTO users (username,password_hash,full_name) VALUES (%s,'test',%s) RETURNING id",
                (f"ie02-refusal-l1-{suffix}", f"IE02 L1 {suffix}"),
            )
            l1_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id,role_id) VALUES (%s,%s)",
                (l1_id, int(RoleId.L1)),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id,telegram_chat_id,bitrix24_user_id,notify_telegram,notify_bitrix24) VALUES (%s,%s,%s,true,true)",
                (l1_id, f"tg-l1-{suffix}", f"bx-l1-{suffix}"),
            )
            cursor.execute(
                "INSERT INTO distribution_members (user_id,pool_code,is_enabled) VALUES (%s,%s,true)",
                (l1_id, int(DistributionPool.L1)),
            )
            for weekday in range(1, 8):
                cursor.execute(
                    "INSERT INTO schedules (user_id,weekday,start_time,end_time,timezone) VALUES (%s,%s,'00:00:00','23:59:59','UTC')",
                    (l1_id, weekday),
                )

            cursor.execute(
                "INSERT INTO users (username,password_hash,full_name) VALUES (%s,'test',%s) RETURNING id",
                (f"ie02-refusal-l2-{suffix}", f"IE02 L2 {suffix}"),
            )
            l2_id = cursor.fetchone()["id"]
            cursor.execute(
                "INSERT INTO user_roles (user_id,role_id) VALUES (%s,%s)",
                (l2_id, int(RoleId.L2)),
            )
            cursor.execute(
                "INSERT INTO distribution_members (user_id,pool_code,is_enabled) VALUES (%s,%s,true)",
                (l2_id, int(DistributionPool.L2)),
            )
            for weekday in range(1, 8):
                cursor.execute(
                    "INSERT INTO schedules (user_id,weekday,start_time,end_time,timezone) VALUES (%s,%s,'00:00:00','23:59:59','UTC')",
                    (l2_id, weekday),
                )

        repository = PostgresCardRepository(connection)
        notifications = PostgresNotificationService(connection)
        service = CardService(repository, notifications=notifications)
        plan = validate_role_create(
            scenario=CreateScenario.L1,
            planned_start_at=planned_start_at,
            planned_duration_minutes=60,
            now=now,
        )
        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=ticket_number,
                planned_start_at=planned_start_at,
                planned_duration_minutes=60,
            ),
            actor_user_id=l1_id,
            actor_type=ActorType.INTERNAL_USER,
            ip_address=None,
            user_agent=None,
            role_create_plan=plan,
        )
        assert card.l2_engineer_id == l2_id

        first = service.reject_card(
            card.public_id,
            actor_user_id=l2_id,
            rejection_reason="first_cycle_unavailable",
            ip_address=None,
            user_agent=None,
        )
        assert CardStatus(first.status_code) == CardStatus.REJECTED
        assert first.l1_owner_id == l1_id

        second_start = planned_start_at + timedelta(days=1)
        reassigned = service.update_rejected_card(
            first.public_id,
            actor_user_id=l1_id,
            planned_start_at=second_start,
            planned_duration_minutes=60,
            description="rescheduled after first refusal",
            ip_address=None,
            user_agent=None,
        )
        assert reassigned.l2_engineer_id == l2_id
        second = service.reject_card(
            reassigned.public_id,
            actor_user_id=l2_id,
            rejection_reason="second_cycle_unavailable",
            ip_address=None,
            user_agent=None,
        )
        assert CardStatus(second.status_code) == CardStatus.REJECTED
        assert second.l1_owner_id == l1_id

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status_code,l1_owner_id,criticality_code,unsuccessful_cycle_count FROM connection_cards WHERE id=%s",
                (card.id,),
            )
            persisted_card = cursor.fetchone()
            assert persisted_card["status_code"] == int(CardStatus.REJECTED)
            assert persisted_card["l1_owner_id"] == l1_id
            assert persisted_card["unsuccessful_cycle_count"] == 2
            assert persisted_card["criticality_code"] > card.criticality_code

            cursor.execute(
                "SELECT cycle_number,status_code FROM assignment_cycles WHERE card_id=%s ORDER BY cycle_number",
                (card.id,),
            )
            assert [dict(row) for row in cursor.fetchall()] == [
                {"cycle_number": 1, "status_code": 2},
                {"cycle_number": 2, "status_code": 2},
            ]
            cursor.execute(
                "SELECT id,comment FROM card_events WHERE card_id=%s AND comment IN ('all_l2_candidates_rejected: first_cycle_unavailable','all_l2_candidates_rejected: second_cycle_unavailable') ORDER BY id",
                (card.id,),
            )
            escalation_events = cursor.fetchall()
            assert len(escalation_events) == 2

            cursor.execute(
                "SELECT recipient_user_id,channel_code,event_type_code,source_event_id,source_event_type_code,payload,dedupe_key FROM notifications WHERE card_id=%s ORDER BY event_type_code,source_event_id,channel_code",
                (card.id,),
            )
            intents = [dict(row) for row in cursor.fetchall()]
            manager_intents = [
                n for n in intents if n["recipient_user_id"] == manager_id
            ]
            l1_intents = [n for n in intents if n["recipient_user_id"] == l1_id]

            assert len(manager_intents) == 4
            assert len(l1_intents) == 4
            assert {n["channel_code"] for n in manager_intents} == {0, 1}
            assert {n["source_event_id"] for n in manager_intents} == {
                event["id"] for event in escalation_events
            }
            for intent in manager_intents:
                assert intent["event_type_code"] == 2
                assert intent["source_event_type_code"] == int(
                    CardEventType.STATUS_CHANGED
                )
                assert intent["payload"] == {
                    "card_id": card.id,
                    "assignment": "manager_escalation",
                }
            assert {n["channel_code"] for n in l1_intents} == {0, 1}
            assert all(n["event_type_code"] == 3 for n in l1_intents)
            assert len({n["dedupe_key"] for n in intents}) == len(intents)

    finally:
        connection.rollback()
        connection.close()

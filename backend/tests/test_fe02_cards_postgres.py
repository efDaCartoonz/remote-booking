from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.repository import PostgresCardRepository
from app.cards.service import CardService

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


def test_mine_notifications_and_manual_l1_interval_are_persisted() -> None:
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        try:
            suffix = uuid4().hex[:10]
            now = datetime.now(UTC).replace(microsecond=0)
            with connection.cursor() as cursor:
                for role in ("l1", "l2", "other"):
                    cursor.execute(
                        "INSERT INTO users (username, password_hash, full_name) "
                        "VALUES (%s, 'test', %s) RETURNING id",
                        (f"fe02-{role}-{suffix}", f"FE02 {role}"),
                    )
                    if role == "l1":
                        l1_id = cursor.fetchone()["id"]
                    elif role == "l2":
                        l2_id = cursor.fetchone()["id"]
                    else:
                        other_id = cursor.fetchone()["id"]
                cursor.execute(
                    "INSERT INTO connection_cards "
                    "(omnidesk_ticket_number, status_code, planned_start_at, "
                    "planned_duration_minutes, l1_owner_id, l2_engineer_id, client_informed) "
                    "VALUES (%s, 4, %s, 60, %s, %s, true) RETURNING id, public_id",
                    (
                        f"998-{uuid4().int % 1_000_000:06d}",
                        now + timedelta(days=1),
                        l1_id,
                        l2_id,
                    ),
                )
                card = cursor.fetchone()
                cursor.execute(
                    "INSERT INTO notifications "
                    "(card_id, recipient_user_id, channel_code, event_type_code, status_code) "
                    "VALUES (%s, %s, 0, 3, 0), (%s, %s, 1, 3, 1)",
                    (card["id"], l1_id, card["id"], other_id),
                )

            # Create an ASSIGNED card for L2 mine list verification
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO connection_cards "
                    "(omnidesk_ticket_number, status_code, planned_start_at, "
                    "planned_duration_minutes, l1_owner_id, l2_engineer_id, client_informed) "
                    "VALUES (%s, 1, %s, 60, %s, %s, false) RETURNING id, public_id",
                    (
                        f"998-{uuid4().int % 1_000_000:06d}",
                        now + timedelta(days=2),
                        l1_id,
                        l2_id,
                    ),
                )
                l2_card = cursor.fetchone()

                # A card the L1 created that has no follow-up owner yet is listed too.
                cursor.execute(
                    "INSERT INTO connection_cards "
                    "(omnidesk_ticket_number, status_code, planned_start_at, "
                    "planned_duration_minutes, created_by_id) "
                    "VALUES (%s, 1, %s, 60, %s) RETURNING id",
                    (
                        f"998-{uuid4().int % 1_000_000:06d}",
                        now + timedelta(days=3),
                        l1_id,
                    ),
                )
                created_card = cursor.fetchone()

            repository = PostgresCardRepository(connection)
            assert [
                item.id
                for item in repository.list_mine_cards(
                    user_id=l1_id, role="l1", limit=100
                )
            ] == [created_card["id"], l2_card["id"], card["id"]]
            assert (
                repository.list_mine_cards(user_id=other_id, role="l1", limit=100) == []
            )
            assert [
                item.id
                for item in repository.list_mine_cards(
                    user_id=l2_id, role="l2", limit=100
                )
            ] == [l2_card["id"]]
            assert (
                len(
                    repository.list_card_notifications(
                        card_id=card["id"], recipient_user_id=l1_id
                    )
                )
                == 1
            )
            assert (
                len(
                    repository.list_card_notifications(
                        card_id=card["id"], recipient_user_id=None
                    )
                )
                == 2
            )

            # Test missing-schedule recreation: when no active schedule exists
            assert repository.get_l1_reminder_interval(card["id"]) is None
            service = CardService(repository, clock=lambda: now + timedelta(minutes=1))
            # GET returns contract default 10 without creating a schedule
            assert (
                service.l1_reminder_interval(
                    card["public_id"], actor_user_id=l1_id, actor_role_ids={1}
                )
                == 10
            )
            assert repository.get_l1_reminder_interval(card["id"]) is None

            # POST 10 must create active schedule
            assert (
                service.l1_reminder_interval(
                    card["public_id"],
                    actor_user_id=l1_id,
                    actor_role_ids={1},
                    interval_minutes=10,
                )
                == 10
            )
            assert repository.get_l1_reminder_interval(card["id"]) == 10

            # POST 30 updates interval to 30
            assert (
                service.l1_reminder_interval(
                    card["public_id"],
                    actor_user_id=l1_id,
                    actor_role_ids={1},
                    interval_minutes=30,
                )
                == 30
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT interval_seconds, next_due_at, anchor_at, settings_snapshot "
                    "FROM reminder_schedules WHERE card_id=%s AND closed_at IS NULL",
                    (card["id"],),
                )
                active = cursor.fetchall()
            assert len(active) == 1
            assert active[0]["interval_seconds"] == 1800
            assert active[0]["next_due_at"] - active[0]["anchor_at"] == timedelta(
                minutes=30
            )
            assert active[0]["settings_snapshot"]["l1_mode"] == "post_informed"

            # 0 switches the reminders off: nothing is active, the choice is remembered
            assert (
                service.l1_reminder_interval(
                    card["public_id"],
                    actor_user_id=l1_id,
                    actor_role_ids={1},
                    interval_minutes=0,
                )
                == 0
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) AS n FROM reminder_schedules "
                    "WHERE card_id=%s AND kind='l1_reminder' AND closed_at IS NULL",
                    (card["id"],),
                )
                assert cursor.fetchone()["n"] == 0
            assert repository.get_l1_reminder_interval(card["id"]) == 0
            assert (
                service.l1_reminder_interval(
                    card["public_id"], actor_user_id=l1_id, actor_role_ids={1}
                )
                == 0
            )

            # and a longer interval can be chosen again afterwards
            assert (
                service.l1_reminder_interval(
                    card["public_id"],
                    actor_user_id=l1_id,
                    actor_role_ids={1},
                    interval_minutes=120,
                )
                == 120
            )
            assert repository.get_l1_reminder_interval(card["id"]) == 120
        finally:
            connection.rollback()

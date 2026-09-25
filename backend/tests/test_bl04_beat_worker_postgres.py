"""Exercise the versioned Beat and worker through their isolated Redis broker."""

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import CardStatus
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.worker import celery_app


pytestmark = pytest.mark.skipif(
    os.getenv("RDM_BL04_BEAT_WORKER") != "1",
    reason="isolated Beat/worker gate only",
)


def test_beat_dispatches_extension_and_worker_processes_it() -> None:
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)
    token = uuid.uuid4().hex
    card_id = None
    user_id = None
    try:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (username, password_hash, full_name) "
                    "VALUES (%s, 'test', 'BL04 beat gate') RETURNING id",
                    (f"bl04-beat-{token}",),
                )
                user_id = cursor.fetchone()["id"]
                cursor.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (%s, 2)",
                    (user_id,),
                )
                cursor.execute(
                    "INSERT INTO distribution_members (user_id, pool_code, is_enabled) "
                    "VALUES (%s, 2, true)",
                    (user_id,),
                )
                for weekday in range(1, 8):
                    cursor.execute(
                        "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) "
                        "VALUES (%s, %s, '00:00', '23:59:59', 'UTC')",
                        (user_id, weekday),
                    )
            repository = PostgresCardRepository(connection)
            service = CardService(repository)
            card = service.create_card(
                CardCreateRequest(
                    omnidesk_ticket_number=f"{uuid.uuid4().int % 900 + 100}-000001",
                    planned_start_at=datetime.now(UTC) - timedelta(hours=2),
                    planned_duration_minutes=60,
                    l2_engineer_id=user_id,
                ),
                actor_user_id=user_id,
                ip_address="127.0.0.1",
                user_agent="bl04-beat-gate",
                manual_assignment=True,
                allow_out_of_hours=True,
            )
            card_id = card.id
            service.confirm_card(
                card.public_id,
                actor_user_id=user_id,
                comment=None,
                ip_address=None,
                user_agent=None,
            )
            service.start_card(
                card.public_id,
                actor_user_id=user_id,
                comment=None,
                ip_address=None,
                user_agent=None,
            )
            connection.commit()

        # The task is never called or enqueued by this test: only live Beat may
        # publish it, and only the versioned worker can change this card.
        deadline = time.monotonic() + 145
        while time.monotonic() < deadline:
            with psycopg.connect(database_url, row_factory=dict_row) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT status_code, extension_count, planned_duration_minutes "
                        "FROM connection_cards WHERE id = %s",
                        (card_id,),
                    )
                    row = cursor.fetchone()
                    cursor.execute(
                        "SELECT count(*) AS n FROM session_extensions WHERE card_id = %s",
                        (card_id,),
                    )
                    extension_rows = cursor.fetchone()["n"]
            if row["extension_count"] == 1 and extension_rows == 1:
                assert row["status_code"] == int(CardStatus.IN_PROGRESS)
                assert row["planned_duration_minutes"] == 75
                break
            time.sleep(2)
        else:
            pytest.fail(
                "Beat did not cause the worker to extend the due card within 145s"
            )

        # The same notifications queue must still be consumed by this worker.
        result = celery_app.send_task("app.worker.deliver_notifications")
        assert result.get(timeout=20) == 0  # delivery disabled in .env.example
    finally:
        if user_id is not None:
            with psycopg.connect(database_url) as connection:
                with connection.cursor() as cursor:
                    if card_id is not None:
                        cursor.execute(
                            "DELETE FROM session_extensions WHERE card_id = %s",
                            (card_id,),
                        )
                        cursor.execute(
                            "DELETE FROM card_events WHERE card_id = %s", (card_id,)
                        )
                        cursor.execute(
                            "DELETE FROM audit_log WHERE entity_type = 'connection_card' AND entity_id = %s",
                            (card_id,),
                        )
                        cursor.execute(
                            "DELETE FROM connection_cards WHERE id = %s", (card_id,)
                        )
                    cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))

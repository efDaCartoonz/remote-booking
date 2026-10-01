from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.auth.store import PostgresAuthStore

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


def test_profile_timezone_update_is_audited_without_moving_card_instant() -> None:
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        try:
            suffix = uuid4().hex[:10]
            user = connection.execute(
                "INSERT INTO users (username, password_hash, full_name) "
                "VALUES (%s, 'test', 'FE03 timezone') RETURNING id",
                (f"fe03-tz-{suffix}",),
            ).fetchone()
            user_id = user["id"]
            instant = datetime(2030, 1, 2, 10, 30, tzinfo=UTC)
            card = connection.execute(
                "INSERT INTO connection_cards "
                "(omnidesk_ticket_number, status_code, planned_start_at, "
                "planned_duration_minutes) VALUES (%s, 0, %s, 60) RETURNING id",
                (f"777-{uuid4().int % 1_000_000:06d}", instant),
            ).fetchone()

            store = PostgresAuthStore(connection)
            assert store.get_user_timezone(user_id) == "Asia/Yekaterinburg"
            assert (
                store.set_user_timezone(
                    actor_user_id=user_id,
                    target_user_id=user_id,
                    timezone="Europe/Moscow",
                )
                == "Europe/Moscow"
            )
            assert store.get_user_timezone(user_id) == "Europe/Moscow"
            assert (
                store.get_user_by_username(f"fe03-tz-{suffix}").timezone
                == "Europe/Moscow"
            )
            saved_card = connection.execute(
                "SELECT planned_start_at FROM connection_cards WHERE id = %s",
                (card["id"],),
            ).fetchone()
            assert saved_card["planned_start_at"] == instant
            audit = connection.execute(
                "SELECT actor_user_id, action_code, entity_type, entity_id, "
                "old_values, new_values FROM audit_log "
                "WHERE entity_type = 'user_settings' AND entity_id = %s ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            assert audit["actor_user_id"] == user_id
            assert audit["action_code"] == 1
            assert audit["entity_id"] == user_id
            assert audit["old_values"] == {"timezone": "Asia/Yekaterinburg"}
            assert audit["new_values"] == {"timezone": "Europe/Moscow"}
        finally:
            connection.rollback()

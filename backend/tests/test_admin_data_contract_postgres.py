import os
from datetime import UTC, date, datetime, time

import psycopg
import pytest
from psycopg.rows import dict_row

from app.admin.repository import (
    AdministrativeRepository,
    ConnectionResult,
    WorkSchedule,
)
from app.cards.constants import CardEventType
from app.cards.repository import CardHistoryRecord
from app.cards.schemas import card_history_response

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture
def database_url() -> str:
    return os.getenv("PSYCOPG_DATABASE_URL") or os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


@pytest.fixture(autouse=True)
def clean_database(database_url: str):
    yield
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM audit_log WHERE entity_type IN ('connection_result', 'production_calendar_day', 'distribution_membership', 'system_setting', 'absence', 'schedule')"
        )
        cursor.execute("DELETE FROM session_extensions")
        cursor.execute(
            "DELETE FROM connection_cards WHERE omnidesk_ticket_number LIKE '920-%'"
        )
        cursor.execute("DELETE FROM absences")
        cursor.execute("DELETE FROM schedules")
        cursor.execute("DELETE FROM production_calendar_days")
        cursor.execute("DELETE FROM distribution_members")
        cursor.execute("DELETE FROM users WHERE id=92000")
        cursor.execute(
            "UPDATE system_settings SET value='900'::jsonb WHERE key='session_extension_interval_seconds'"
        )


def seed_admin(connection) -> None:
    connection.execute(
        "INSERT INTO users (id, username, password_hash, full_name) VALUES (92000, 'db02-admin', 'x', 'DB02 Admin')"
    )


def test_admin_storage_is_audited_and_calendar_is_a_policy_source(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin(connection)
        repository = AdministrativeRepository(connection)
        repository.upsert_result(
            result=ConnectionResult(90, "DB-02 result", True, 90), actor_user_id=92000
        )
        repository.set_calendar_day(
            calendar_date=date(2030, 1, 1),
            day_type_code=1,
            is_manual_override=True,
            comment="holiday",
            actor_user_id=92000,
        )
        repository.replace_schedules(
            user_id=92000,
            schedules=[WorkSchedule(3, time(9), time(18), "UTC")],
            actor_user_id=92000,
        )
        repository.add_absence(
            user_id=92000,
            start_at=datetime(2030, 1, 2, 12, tzinfo=UTC),
            end_at=datetime(2030, 1, 2, 13, tzinfo=UTC),
            actor_user_id=92000,
            reason="training",
        )
        repository.set_distribution_membership(
            user_id=92000,
            pool_code=2,
            enabled=True,
            actor_user_id=92000,
            comment="manager decision",
        )
        with pytest.raises(ValueError, match="whole_minutes"):
            repository.set_extension_interval(interval_seconds=901, actor_user_id=92000)
        repository.set_extension_interval(interval_seconds=900, actor_user_id=92000)
        assert repository.is_out_of_hours(
            user_id=92000,
            start_at=datetime(2030, 1, 1, 10, tzinfo=UTC),
            end_at=datetime(2030, 1, 1, 11, tzinfo=UTC),
        )
        assert not repository.is_out_of_hours(
            user_id=92000,
            start_at=datetime(2030, 1, 2, 10, tzinfo=UTC),
            end_at=datetime(2030, 1, 2, 11, tzinfo=UTC),
        )
        rows = connection.execute(
            "SELECT entity_type FROM audit_log ORDER BY id"
        ).fetchall()
    assert {row["entity_type"] for row in rows} >= {
        "connection_result",
        "production_calendar_day",
        "distribution_membership",
        "system_setting",
        "absence",
        "schedule",
    }


def test_result_deactivation_preserves_card_and_extension_contract(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin(connection)
        card_id = connection.execute(
            """
            INSERT INTO connection_cards (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes, result_code)
            VALUES ('920-000001', 3, '2030-01-03T10:00:00Z', 60, 0) RETURNING id
            """
        ).fetchone()["id"]
        repository = AdministrativeRepository(connection)
        repository.upsert_result(
            result=ConnectionResult(0, "Completed", False, 10), actor_user_id=92000
        )
        extension_id = repository.record_session_extension(
            card_id=card_id,
            previous_planned_end_at=datetime(2030, 1, 3, 11, tzinfo=UTC),
            new_planned_end_at=datetime(2030, 1, 3, 11, 15, tzinfo=UTC),
            interval_seconds=900,
            collision_card_id=None,
        )
        row = connection.execute(
            "SELECT result_code, extension_count FROM connection_cards WHERE id=%s",
            (card_id,),
        ).fetchone()
        extension = connection.execute(
            "SELECT interval_seconds, has_collision, event_id FROM session_extensions WHERE id=%s",
            (extension_id,),
        ).fetchone()
        event = connection.execute(
            "SELECT event_type_code, actor_type_code FROM card_events WHERE id=%s",
            (extension["event_id"],),
        ).fetchone()
        audit = connection.execute(
            "SELECT actor_type_code FROM audit_log WHERE entity_type='connection_card' AND entity_id=%s",
            (card_id,),
        ).fetchone()
    assert row == {"result_code": 0, "extension_count": 1}
    assert extension["interval_seconds"] == 900
    assert extension["has_collision"] is False
    assert event == {"event_type_code": 9, "actor_type_code": 2}
    assert audit == {"actor_type_code": 2}
    history = card_history_response(
        CardHistoryRecord(
            event_type_code=event["event_type_code"],
            actor_type_code=event["actor_type_code"],
            actor_name=None,
            created_at=datetime(2030, 1, 3, 11, 15, tzinfo=UTC),
        )
    )
    assert history.event_label == "Сессия продлена"
    assert CardEventType.SESSION_EXTENDED == 9


def test_extension_and_setting_constraints_reject_invalid_values(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin(connection)
        card_id = connection.execute(
            "INSERT INTO connection_cards (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes) VALUES ('920-000002', 3, '2030-01-04T10:00:00Z', 60) RETURNING id"
        ).fetchone()["id"]
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE system_settings SET value='30'::jsonb WHERE key='session_extension_interval_seconds'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "UPDATE system_settings SET value='901'::jsonb WHERE key='session_extension_interval_seconds'"
            )
        connection.rollback()
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO session_extensions (card_id, previous_planned_end_at, new_planned_end_at, interval_seconds, has_collision) VALUES (%s, now(), now(), 0, false)",
                (card_id,),
            )

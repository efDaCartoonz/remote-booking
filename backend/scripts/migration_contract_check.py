"""PostgreSQL migration contract checks used by the reproducible quality gate."""

from __future__ import annotations

import sys

import psycopg
from app.core.config import settings
from psycopg.errors import ExclusionViolation, UniqueViolation

BASELINE_TICKET = "901-000001"
OVERLAPPING_TICKET = "901-000002"
NON_OVERLAPPING_TICKET = "901-000003"


def connect() -> psycopg.Connection:
    return psycopg.connect(settings.psycopg_database_url)


def assert_baseline_data(connection: psycopg.Connection) -> None:
    row = connection.execute(
        """
        SELECT user_name, card_number, planned_duration_minutes
        FROM (
            SELECT u.username AS user_name, c.number AS card_number,
                   c.planned_duration_minutes
            FROM users u
            JOIN connection_cards c ON c.l2_engineer_id = u.id
            WHERE u.username = 'migration-gate-l2'
              AND c.omnidesk_ticket_number = %s
        ) preserved
        """,
        (BASELINE_TICKET,),
    ).fetchone()
    assert row == ("migration-gate-l2", "RDM-000001", 60), row


def seed_baseline(connection: psycopg.Connection) -> None:
    connection.execute(
        """
        INSERT INTO users (username, password_hash, full_name)
        VALUES ('migration-gate-l2', 'not-a-real-password', 'Migration Gate L2')
        """
    )
    connection.execute(
        """
        INSERT INTO connection_cards (
            omnidesk_ticket_number, status_code, planned_start_at,
            planned_duration_minutes, l2_engineer_id
        )
        SELECT %s, 1, '2030-01-01T10:00:00Z', 60, id
        FROM users
        WHERE username = 'migration-gate-l2'
        """,
        (BASELINE_TICKET,),
    )
    connection.commit()
    assert_baseline_data(connection)


def assert_head_invariants(connection: psycopg.Connection) -> None:
    assert_baseline_data(connection)
    case_index = connection.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE tablename = 'omnidesk_case_index'
          AND indexname = 'ux_omnidesk_case_index_case_number'
        """
    ).fetchone()
    assert case_index == ("ux_omnidesk_case_index_case_number",), case_index
    extension_setting = connection.execute(
        "SELECT value FROM system_settings WHERE key = 'session_extension_interval_seconds'"
    ).fetchone()
    assert extension_setting == (900,), extension_setting
    result_catalog = connection.execute(
        "SELECT count(*) FROM connection_results WHERE is_active"
    ).fetchone()
    assert result_catalog[0] > 0, result_catalog
    extension_table = connection.execute(
        "SELECT to_regclass('session_extensions')"
    ).fetchone()
    assert extension_table == ("session_extensions",), extension_table
    overdue_at = connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'connection_cards' AND column_name = 'overdue_at'
        """
    ).fetchone()
    assert overdue_at == ("overdue_at",), overdue_at
    outbox_table = connection.execute(
        "SELECT to_regclass('omnidesk_internal_note_outbox')"
    ).fetchone()
    assert outbox_table == ("omnidesk_internal_note_outbox",), outbox_table

    reminder_index = connection.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE tablename = 'reminder_schedules'
          AND indexname = 'ux_reminder_schedules_one_active_per_kind'
        """
    ).fetchone()
    assert reminder_index == (
        "ux_reminder_schedules_one_active_per_kind",
    ), reminder_index

    connection.execute(
        """
        INSERT INTO reminder_schedules (
            card_id, kind, anchor_at, interval_seconds, escalation_after_count,
            next_due_at, settings_snapshot
        )
        SELECT id, 'l2_reminder', '2030-01-01T10:00:00Z', 60, 2,
               '2030-01-01T10:01:00Z', '{}'::jsonb
        FROM connection_cards
        WHERE omnidesk_ticket_number = %s
        ON CONFLICT DO NOTHING
        """,
        (BASELINE_TICKET,),
    )

    try:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO reminder_schedules (
                    card_id, kind, anchor_at, interval_seconds,
                    escalation_after_count, next_due_at, settings_snapshot
                )
                SELECT id, 'l2_reminder', '2030-01-01T10:00:00Z', 60, 2,
                       '2030-01-01T10:01:00Z', '{}'::jsonb
                FROM connection_cards
                WHERE omnidesk_ticket_number = %s
                """,
                (BASELINE_TICKET,),
            )
    except UniqueViolation:
        pass
    else:
        raise AssertionError("only one active reminder per card and kind is allowed")

    try:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO connection_cards (
                    omnidesk_ticket_number, status_code, planned_start_at,
                    planned_duration_minutes, l2_engineer_id
                )
                SELECT %s, 1, '2030-01-01T10:30:00Z', 60, id
                FROM users
                WHERE username = 'migration-gate-l2'
                """,
                (OVERLAPPING_TICKET,),
            )
    except ExclusionViolation:
        pass
    else:
        raise AssertionError("active L2 cards may not overlap")

    connection.execute(
        """
        INSERT INTO connection_cards (
            omnidesk_ticket_number, status_code, planned_start_at,
            planned_duration_minutes, l2_engineer_id
        )
        SELECT %s, 1, '2030-01-01T11:00:00Z', 60, id
        FROM users
        WHERE username = 'migration-gate-l2'
          AND NOT EXISTS (
              SELECT 1
              FROM connection_cards
              WHERE omnidesk_ticket_number = %s
          )
        """,
        (NON_OVERLAPPING_TICKET, NON_OVERLAPPING_TICKET),
    )

    try:
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO connection_cards (
                    omnidesk_ticket_number, status_code, planned_start_at,
                    planned_duration_minutes
                )
                VALUES (%s, 0, '2030-01-02T10:00:00Z', 60)
                """,
                (BASELINE_TICKET,),
            )
    except UniqueViolation:
        pass
    else:
        raise AssertionError("only one active card per Omnidesk ticket is allowed")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {
        "seed-baseline",
        "check-baseline",
        "check-head",
    }:
        raise SystemExit(
            "usage: migration_contract_check.py {seed-baseline|check-baseline|check-head}"
        )

    with connect() as connection:
        command = sys.argv[1]
        if command == "seed-baseline":
            seed_baseline(connection)
        elif command == "check-baseline":
            assert_baseline_data(connection)
        else:
            assert_head_invariants(connection)


if __name__ == "__main__":
    main()

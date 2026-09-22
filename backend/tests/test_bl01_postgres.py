import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import (
    AssignmentAttemptStatus,
    AssignmentCycleStatus,
    CardStatus,
    RoleId,
)
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService, InvalidCardTransitionError
from app.integrations.omnidesk_reschedule import ConfirmedOmnideskReschedule
from app.reminders import PostgresReminderRepository, ReminderService

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture
def database_url():
    return os.environ["PSYCOPG_DATABASE_URL"]


@pytest.fixture(autouse=True)
def cleanup(database_url):
    yield
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM connection_cards")
        cursor.execute("DELETE FROM users WHERE id BETWEEN 92000 AND 92010")


def _seed(connection):
    with connection.cursor() as cursor:
        for user_id, role in (
            (92000, RoleId.MANAGER),
            (92001, RoleId.L1),
            (92002, RoleId.L2),
            (92003, RoleId.L2),
        ):
            cursor.execute(
                "INSERT INTO users (id, username, password_hash, full_name) VALUES (%s, %s, 'test', %s)",
                (user_id, f"bl01-{user_id}", f"BL01 {user_id}"),
            )
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (user_id, int(role)),
            )
        for user_id, pool in ((92001, 1), (92002, 2), (92003, 2)):
            cursor.execute(
                "INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (%s, %s, true)",
                (user_id, pool),
            )
            for weekday in range(1, 8):
                cursor.execute(
                    "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (%s, %s, '00:00', '23:59:59', 'UTC')",
                    (user_id, weekday),
                )


def _create_rejected(service):
    return service.create_card(
        CardCreateRequest(
            omnidesk_ticket_number="920-000001",
            planned_start_at=datetime.now(UTC) + timedelta(days=2),
            planned_duration_minutes=60,
        ),
        actor_user_id=92000,
        ip_address=None,
        user_agent=None,
    )


class _NoopNotifications:
    def notify(self, **_kwargs):
        return False


def test_omnidesk_reschedule_persists_l1_closure_and_deduplicates(database_url):
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE distribution_members SET is_enabled=false WHERE user_id IN (92002,92003)"
            )
        service = CardService(PostgresCardRepository(connection))
        rejected = _create_rejected(service)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE distribution_members SET is_enabled=true WHERE user_id IN (92002,92003)"
            )
        command = ConfirmedOmnideskReschedule(
            card_id=rejected.id,
            source_event_id="od-920-1",
            planned_start_at=rejected.planned_start_at + timedelta(hours=1),
            planned_duration_minutes=90,
        )
        service.apply_confirmed_omnidesk_reschedule(command)
        service.apply_confirmed_omnidesk_reschedule(command)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status_code, l1_owner_id, planned_duration_minutes FROM connection_cards WHERE id=%s",
                (rejected.id,),
            )
            assert dict(cursor.fetchone()) == {
                "status_code": 1,
                "l1_owner_id": None,
                "planned_duration_minutes": 90,
            }
            cursor.execute(
                "SELECT count(*) FROM card_events WHERE card_id=%s AND comment='omnidesk_rescheduled:od-920-1'",
                (rejected.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM audit_log WHERE entity_type='omnidesk_reschedule' AND entity_id=%s",
                (rejected.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND kind='l1_reminder' AND closed_at IS NOT NULL",
                (rejected.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND kind='l2_reminder' AND closed_at IS NULL",
                (rejected.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM assignment_cycles WHERE card_id=%s",
                (rejected.id,),
            )
            assert cursor.fetchone()["count"] == 2
            cursor.execute(
                "SELECT count(*) FROM assignment_attempts WHERE card_id=%s AND status_code=0",
                (rejected.id,),
            )
            assert cursor.fetchone()["count"] == 1
        with pytest.raises(InvalidCardTransitionError):
            service.apply_confirmed_omnidesk_reschedule(
                ConfirmedOmnideskReschedule(
                    card_id=rejected.id,
                    source_event_id="od-920-stale",
                    planned_start_at=rejected.planned_start_at,
                    planned_duration_minutes=60,
                )
            )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM card_events WHERE card_id=%s AND comment LIKE 'omnidesk_rescheduled:%%'",
                (rejected.id,),
            )
            assert cursor.fetchone()["count"] == 1


def test_manager_reassignment_persists_cycle_attempt_and_reminder_lifecycle(
    database_url,
):
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        service = CardService(PostgresCardRepository(connection))
        card = _create_rejected(service)
        # The normal initial distribution assigned L2; move it transactionally to
        # the other enabled engineer, then repeat the same request.
        assert card.l2_engineer_id == 92002
        reassigned = service.assign_card(
            card.public_id,
            l2_engineer_id=92003,
            actor_user_id=92000,
            actor_role_ids={int(RoleId.MANAGER)},
            comment="coverage reassignment",
            ip_address=None,
            user_agent=None,
        )
        repeated = service.assign_card(
            card.public_id,
            l2_engineer_id=92003,
            actor_user_id=92000,
            actor_role_ids={int(RoleId.MANAGER)},
            comment="coverage reassignment",
            ip_address=None,
            user_agent=None,
        )
        assert repeated.id == reassigned.id
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT cycle_number, status_code FROM assignment_cycles WHERE card_id=%s ORDER BY cycle_number",
                (card.id,),
            )
            assert [dict(row) for row in cursor.fetchall()] == [
                {"cycle_number": 1, "status_code": 3},
                {"cycle_number": 2, "status_code": 1},
            ]
            cursor.execute(
                "SELECT l2_engineer_id, status_code, actor_user_id FROM assignment_attempts WHERE card_id=%s ORDER BY id",
                (card.id,),
            )
            assert [dict(row) for row in cursor.fetchall()] == [
                {"l2_engineer_id": 92002, "status_code": 3, "actor_user_id": 92000},
                {"l2_engineer_id": 92003, "status_code": 0, "actor_user_id": None},
            ]
            cursor.execute(
                "SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND kind='l2_reminder' AND closed_at IS NOT NULL",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND kind='l2_reminder' AND closed_at IS NULL",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM card_events WHERE card_id=%s AND comment='manager_manual_assignment'",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM audit_log WHERE entity_type='assignment_cycle' AND actor_user_id=92000",
                (),
            )
            assert cursor.fetchone()["count"] == 1


def test_cancel_closes_assignment_lifecycle_and_releases_reservation(database_url):
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        service = CardService(PostgresCardRepository(connection))
        card = _create_rejected(service)

        cancelled = service.cancel_card(
            card.public_id,
            actor_user_id=92000,
            actor_role_ids={int(RoleId.MANAGER)},
            comment="client_requested",
            ip_address=None,
            user_agent=None,
        )

        assert cancelled.status_code == int(CardStatus.CANCELLED)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status_code FROM assignment_cycles WHERE card_id=%s", (card.id,)
            )
            assert cursor.fetchone()["status_code"] == int(
                AssignmentCycleStatus.CANCELLED
            )
            cursor.execute(
                "SELECT status_code, actor_user_id, rejection_reason FROM assignment_attempts WHERE card_id=%s",
                (card.id,),
            )
            assert dict(cursor.fetchone()) == {
                "status_code": int(AssignmentAttemptStatus.SKIPPED),
                "actor_user_id": 92000,
                "rejection_reason": "cancelled",
            }
            cursor.execute(
                "SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND closed_at IS NULL",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 0
            cursor.execute(
                "SELECT count(*) FROM connection_cards WHERE l2_engineer_id=92002 AND status_code IN (1, 2, 3)",
            )
            assert cursor.fetchone()["count"] == 0


def test_manager_cancel_in_progress_persists_closed_lifecycle_and_releases_reservation(
    database_url,
):
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        service = CardService(PostgresCardRepository(connection))
        card = _create_rejected(service)
        assert card.l2_engineer_id == 92002

        service.confirm_card(
            card.public_id,
            actor_user_id=92002,
            comment="confirmed",
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card.public_id,
            actor_user_id=92002,
            comment="started",
            ip_address=None,
            user_agent=None,
        )
        cancelled = service.cancel_card(
            card.public_id,
            actor_user_id=92000,
            actor_role_ids={int(RoleId.MANAGER)},
            comment="client_requested",
            ip_address=None,
            user_agent=None,
        )

        assert cancelled.status_code == int(CardStatus.CANCELLED)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status_code FROM connection_cards WHERE id=%s", (card.id,)
            )
            assert cursor.fetchone()["status_code"] == int(CardStatus.CANCELLED)
            cursor.execute(
                "SELECT status_code FROM assignment_cycles WHERE card_id=%s",
                (card.id,),
            )
            assert cursor.fetchone()["status_code"] == int(
                AssignmentCycleStatus.CANCELLED
            )
            cursor.execute(
                "SELECT status_code, responded_at, actor_user_id FROM assignment_attempts WHERE card_id=%s",
                (card.id,),
            )
            attempt = dict(cursor.fetchone())
            assert attempt["status_code"] == int(AssignmentAttemptStatus.CONFIRMED)
            assert attempt["responded_at"] is not None
            assert attempt["actor_user_id"] == 92002
            cursor.execute(
                "SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND closed_at IS NULL",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 0
            cursor.execute(
                "SELECT count(*) FROM connection_cards WHERE l2_engineer_id=92002 AND status_code IN (1, 2, 3)",
            )
            assert cursor.fetchone()["count"] == 0

        # The engineer's reservation is released for a subsequent assignment.
        replacement = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number="920-000002",
                planned_start_at=card.planned_start_at,
                planned_duration_minutes=60,
            ),
            actor_user_id=92000,
            ip_address=None,
            user_agent=None,
        )
        assert replacement.l2_engineer_id == 92002


def test_l2_overdue_persists_l1_followup_and_deduplicates_escalation(database_url):
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        service = CardService(PostgresCardRepository(connection))
        card = _create_rejected(service)
        assert card.l2_engineer_id == 92002
        now = datetime.now(UTC)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE connection_cards SET planned_start_at=%s WHERE id=%s",
                (now - timedelta(hours=2), card.id),
            )
            cursor.execute(
                "UPDATE reminder_schedules SET next_due_at=%s WHERE card_id=%s AND kind='l2_reminder' AND closed_at IS NULL",
                (now - timedelta(minutes=1), card.id),
            )
        scanner = ReminderService(
            PostgresReminderRepository(connection), _NoopNotifications()
        )
        assert scanner.scan(now=now, batch_size=10) == 0
        assert scanner.scan(now=now, batch_size=10) == 0
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT overdue_at, overdue_flag, l1_owner_id, status_code FROM connection_cards WHERE id=%s",
                (card.id,),
            )
            row = dict(cursor.fetchone())
            assert row["overdue_at"] is not None
            assert row["overdue_flag"] is True
            assert row["l1_owner_id"] == 92001
            assert row["status_code"] == 1
            cursor.execute(
                "SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND kind='l2_reminder' AND closed_at IS NOT NULL",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND kind='l1_reminder' AND closed_at IS NULL",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM card_events WHERE card_id=%s AND comment='l2_overdue'",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 1
            cursor.execute(
                "SELECT count(*) FROM audit_log WHERE entity_type='manager_escalation' AND entity_id=%s",
                (card.id,),
            )
            assert cursor.fetchone()["count"] == 1

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import RoleId
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService, InvalidCardTransitionError
from app.integrations.omnidesk_reschedule import ConfirmedOmnideskReschedule

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
        for user_id, role in ((92000, RoleId.MANAGER), (92001, RoleId.L1), (92002, RoleId.L2), (92003, RoleId.L2)):
            cursor.execute("INSERT INTO users (id, username, password_hash, full_name) VALUES (%s, %s, 'test', %s)", (user_id, f"bl01-{user_id}", f"BL01 {user_id}"))
            cursor.execute("INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)", (user_id, int(role)))
        for user_id, pool in ((92001, 1), (92002, 2), (92003, 2)):
            cursor.execute("INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (%s, %s, true)", (user_id, pool))
            for weekday in range(1, 8):
                cursor.execute("INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (%s, %s, '00:00', '23:59:59', 'UTC')", (user_id, weekday))


def _create_rejected(service):
    return service.create_card(
        CardCreateRequest(omnidesk_ticket_number="920-000001", planned_start_at=datetime.now(UTC) + timedelta(days=2), planned_duration_minutes=60),
        actor_user_id=92000, ip_address=None, user_agent=None,
    )


def test_omnidesk_reschedule_persists_l1_closure_and_deduplicates(database_url):
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        with connection.cursor() as cursor:
            cursor.execute("UPDATE distribution_members SET is_enabled=false WHERE user_id IN (92002,92003)")
        service = CardService(PostgresCardRepository(connection))
        rejected = _create_rejected(service)
        command = ConfirmedOmnideskReschedule(card_id=rejected.id, source_event_id="od-920-1", planned_start_at=rejected.planned_start_at + timedelta(hours=1), planned_duration_minutes=90)
        service.apply_confirmed_omnidesk_reschedule(command)
        service.apply_confirmed_omnidesk_reschedule(command)
        with connection.cursor() as cursor:
            cursor.execute("SELECT status_code, l1_owner_id FROM connection_cards WHERE id=%s", (rejected.id,))
            assert dict(cursor.fetchone()) == {"status_code": 4, "l1_owner_id": 92001}
            cursor.execute("SELECT count(*) FROM card_events WHERE card_id=%s AND comment='omnidesk_rescheduled:od-920-1'", (rejected.id,))
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT count(*) FROM audit_log WHERE entity_type='omnidesk_reschedule' AND entity_id=%s", (rejected.id,))
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT count(*) FROM reminder_schedules WHERE card_id=%s AND kind='l1_reminder' AND closed_at IS NOT NULL", (rejected.id,))
            assert cursor.fetchone()[0] == 1
        with pytest.raises(InvalidCardTransitionError):
            service.apply_confirmed_omnidesk_reschedule(ConfirmedOmnideskReschedule(card_id=rejected.id, source_event_id="od-920-stale", planned_start_at=rejected.planned_start_at, planned_duration_minutes=60))

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import app.api.manager as manager_api
import psycopg
import pytest
from app.auth.store import RoleRecord, UserAuthRecord
from app.frame.omnidesk import OmnideskTicket
from app.main import create_app
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture(scope="module")
def database_url() -> str:
    return os.environ["PSYCOPG_DATABASE_URL"]


@pytest.fixture(autouse=True)
def clean_database(database_url: str):
    yield
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM connection_cards")
        cursor.execute("DELETE FROM users WHERE id IN (91000, 91001, 91002)")


def _seed_users(connection, *ids: int) -> None:
    with connection.cursor() as cursor:
        for user_id in ids:
            cursor.execute(
                "INSERT INTO users (id, username, password_hash, full_name) VALUES (%s, %s, 'test', %s) ON CONFLICT (id) DO NOTHING",
                (user_id, f"pg-{user_id}", f"PG {user_id}"),
            )
        for user_id in ids:
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, 2) ON CONFLICT DO NOTHING",
                (user_id,),
            )


def _insert_card(connection, *, ticket: str, l2: int, start: datetime) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO connection_cards (number, omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes, l2_engineer_id) VALUES (%s, %s, 1, %s, 60, %s)",
            (f"PG-{l2}-{ticket[-1]}", ticket, start, l2),
        )


def test_postgres_metadata_has_expected_constraints(database_url: str) -> None:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename='connection_cards' AND indexname='ux_connection_cards_one_active_per_ticket'"
        )
        assert cursor.fetchone() is not None
        cursor.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid='connection_cards'::regclass AND conname='ex_connection_cards_l2_no_overlap'"
        )
        assert cursor.fetchone() is not None


def test_postgres_sequential_conflicts_are_distinct(database_url: str) -> None:
    start = datetime.now(UTC) + timedelta(days=2)
    with psycopg.connect(database_url) as connection:
        _seed_users(connection, 91000, 91001, 91002)
        _insert_card(connection, ticket="910-000001", l2=91001, start=start)
        connection.commit()
        with pytest.raises(psycopg.errors.ExclusionViolation) as overlap:
            _insert_card(connection, ticket="910-000002", l2=91001, start=start)
        assert overlap.value.diag.constraint_name == "ex_connection_cards_l2_no_overlap"
        connection.rollback()
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation) as duplicate:
            _insert_card(connection, ticket="910-000001", l2=91002, start=start)
        assert (
            duplicate.value.diag.constraint_name
            == "ux_connection_cards_one_active_per_ticket"
        )


def test_postgres_two_connection_race_keeps_one_card(database_url: str) -> None:
    start = datetime.now(UTC) + timedelta(days=3)
    with psycopg.connect(database_url) as connection:
        _seed_users(connection, 91000, 91001, 91002)

    def attempt(l2: int) -> str:
        try:
            with psycopg.connect(database_url) as connection:
                _insert_card(connection, ticket="910-000003", l2=l2, start=start)
            return "success"
        except psycopg.errors.UniqueViolation as exc:
            assert (
                exc.diag.constraint_name == "ux_connection_cards_one_active_per_ticket"
            )
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (91001, 91002)))
    assert sorted(outcomes) == ["conflict", "success"]
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM connection_cards WHERE omnidesk_ticket_number='910-000003'"
        )
        assert cursor.fetchone()[0] == 1


@pytest.mark.parametrize("same_ticket", (False, True))
def test_http_two_connection_race_returns_one_success_and_one_409(
    database_url, monkeypatch, same_ticket
):
    with psycopg.connect(database_url) as connection:
        _seed_users(connection, 91000, 91001, 91002)

    @contextmanager
    def isolated_connection():
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            yield connection

    class OmnideskStub:
        def get_ticket_by_case_id(self, case_id):
            return OmnideskTicket(
                case_id=case_id,
                number="910-000004" if same_ticket else f"910-{case_id}",
                user_id="pg-http-client",
                status="open",
                client_display_name="HTTP Test Client",
            )

        def reopen_ticket(self, case_id):
            raise AssertionError("open ticket must not be reopened")

    app = create_app()
    app.dependency_overrides[manager_api.require_manager_role] = lambda: UserAuthRecord(
        id=91000,
        username="manager",
        password_hash="test",
        full_name="Manager",
        email=None,
        roles=(RoleRecord(id=3, name="Руководитель"),),
    )
    app.dependency_overrides[manager_api.get_omnidesk_ticket_client] = OmnideskStub
    monkeypatch.setattr(manager_api, "db_connection", isolated_connection)
    planned_start = datetime.now(UTC) + timedelta(hours=2, minutes=5)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        for user_id in (91001, 91002):
            for weekday in range(1, 8):
                cursor.execute(
                    "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (%s, %s, '00:00', '23:59:59.999999', 'UTC')",
                    (user_id, weekday),
                )
    payload = {
        "case_id": "000004",
        "case_number": "910-000004",
        "planned_start_at": planned_start.isoformat(),
        "planned_duration_minutes": 60,
        "l2_user_id": 91001,
    }
    payloads = [payload, {**payload, "case_id": "000005", "case_number": "910-000005"}]
    if same_ticket:
        payloads[1] = {**payload, "l2_user_id": 91002}

    def request(index):
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            return client.post("/api/v1/manager/cards", json=payloads[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(request, range(2)))
    assert sorted(response.status_code for response in responses) == [201, 409], [
        response.text for response in responses
    ]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["detail"] == (
        "active_card_exists_for_ticket" if same_ticket else "l2_assignment_conflict"
    )
    assert "connection_cards" not in conflict.text
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM connection_cards WHERE omnidesk_ticket_number LIKE '910-00000%'"
        )
        assert cursor.fetchone()[0] == 1
        cursor.execute(
            "SELECT count(*) FROM assignment_cycles c JOIN connection_cards card ON card.id=c.card_id WHERE card.omnidesk_ticket_number LIKE '910-00000%'"
        )
        assert cursor.fetchone()[0] == 1
        for table in (
            "assignment_attempts",
            "reminder_schedules",
            "card_events",
            "audit_log",
            "notifications",
        ):
            cursor.execute(
                f"SELECT count(*) FROM {table} WHERE card_id IN (SELECT id FROM connection_cards WHERE omnidesk_ticket_number LIKE '910-00000%')"
                if table != "audit_log"
                else "SELECT count(*) FROM audit_log WHERE entity_id IN (SELECT id FROM connection_cards WHERE omnidesk_ticket_number LIKE '910-00000%')"
            )
            assert cursor.fetchone()[0] >= 1

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

import app.api.manager as manager_api
import app.omnidesk_index.resolver as ticket_resolver
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.create_policy import CreateScenario, validate_role_create
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService, InvalidCardTransitionError
from app.frame.omnidesk import OmnideskTicket
from app.main import create_app

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
        cursor.execute(
            "DELETE FROM production_calendar_days WHERE updated_by_id IN (91000, 91001, 91002)"
        )
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


def _seed_manager_and_l2(
    connection,
    *,
    schedule_start: str = "09:00",
    schedule_end: str = "17:00",
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, password_hash, full_name) "
            "VALUES (91000, 'pg-manager', 'test', 'PG Manager'), "
            "(91001, 'pg-scheduled-l2', 'test', 'PG Scheduled L2') "
            "ON CONFLICT (id) DO NOTHING"
        )
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (91000, 3), "
            "(91001, 2) ON CONFLICT DO NOTHING"
        )
        for weekday in range(1, 8):
            cursor.execute(
                "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (91001, %s, %s, %s, 'UTC')",
                (weekday, schedule_start, schedule_end),
            )


def _manager_create(
    service: CardService,
    *,
    ticket: str,
    start: datetime,
    allow_out_of_hours: bool = True,
):
    return service.create_card(
        CardCreateRequest(
            omnidesk_ticket_number=ticket,
            planned_start_at=start,
            planned_duration_minutes=60,
            l2_engineer_id=91001,
        ),
        actor_user_id=91000,
        ip_address="127.0.0.1",
        user_agent="postgres-manager-scheduling-test",
        manual_assignment=True,
        allow_out_of_hours=allow_out_of_hours,
    )


def _persisted_lifecycle(cursor, card_id: int) -> None:
    cursor.execute(
        "SELECT status_code, l2_engineer_id, out_of_hours_flag "
        "FROM connection_cards WHERE id=%s",
        (card_id,),
    )
    assert cursor.fetchone() == {
        "status_code": 1,
        "l2_engineer_id": 91001,
        "out_of_hours_flag": False,
    }
    cursor.execute(
        "SELECT status_code FROM assignment_cycles WHERE card_id=%s", (card_id,)
    )
    assert cursor.fetchone()["status_code"] == 1
    cursor.execute(
        "SELECT status_code, l2_engineer_id FROM assignment_attempts WHERE card_id=%s",
        (card_id,),
    )
    assert cursor.fetchone() == {"status_code": 0, "l2_engineer_id": 91001}
    cursor.execute(
        "SELECT kind, closed_at IS NULL AS is_active "
        "FROM reminder_schedules WHERE card_id=%s",
        (card_id,),
    )
    assert cursor.fetchone() == {"kind": "l2_reminder", "is_active": True}
    cursor.execute(
        "SELECT actor_user_id FROM card_events WHERE card_id=%s AND event_type_code=2",
        (card_id,),
    )
    assert cursor.fetchone()["actor_user_id"] == 91000
    cursor.execute(
        "SELECT actor_user_id FROM audit_log WHERE entity_type='connection_card' "
        "AND entity_id=%s AND action_code=1",
        (card_id,),
    )
    assert cursor.fetchone()["actor_user_id"] == 91000


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


def test_active_intervals_without_exclusion_id_are_queryable(database_url: str) -> None:
    start = datetime.now(UTC) + timedelta(days=2)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed_users(connection, 91001)
        _insert_card(connection, ticket="910-000000", l2=91001, start=start)
        intervals = PostgresCardRepository(connection)._list_active_card_intervals(
            91001,
            planned_start_at=start,
            planned_end_at=start + timedelta(minutes=60),
        )
    assert len(intervals) == 1


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


def test_postgres_manager_scheduling_exceptions_persist_expected_state(
    database_url: str,
) -> None:
    inside = (datetime.now(UTC) + timedelta(days=8)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    outside = inside.replace(hour=20)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed_manager_and_l2(connection)
        service = CardService(PostgresCardRepository(connection))

        inside_card = _manager_create(service, ticket="910-000020", start=inside)
        with connection.cursor() as cursor:
            _persisted_lifecycle(cursor, inside_card.id)

        outside_card = _manager_create(service, ticket="910-000021", start=outside)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT out_of_hours_flag FROM connection_cards WHERE id=%s",
                (outside_card.id,),
            )
            assert cursor.fetchone()["out_of_hours_flag"] is True

        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO production_calendar_days "
                "(date, day_type_code, updated_by_id) VALUES (%s, 1, 91000)",
                (inside.date(),),
            )
        calendar_card = _manager_create(
            service,
            ticket="910-000022",
            start=inside + timedelta(hours=2),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT out_of_hours_flag FROM connection_cards WHERE id=%s",
                (calendar_card.id,),
            )
            assert cursor.fetchone()["out_of_hours_flag"] is True

        absence_start = inside + timedelta(hours=5)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO absences "
                "(user_id, start_at, end_at, created_by_id, reason) "
                "VALUES (91001, %s, %s, 91000, 'test absence')",
                (absence_start, absence_start + timedelta(hours=1)),
            )
        with pytest.raises(InvalidCardTransitionError, match="l2_unavailable"):
            _manager_create(service, ticket="910-000023", start=absence_start)

        collision_start = inside + timedelta(hours=7)
        _insert_card(connection, ticket="910-000024", l2=91001, start=collision_start)
        with pytest.raises(InvalidCardTransitionError, match="l2_unavailable"):
            _manager_create(service, ticket="910-000025", start=collision_start)

        with pytest.raises(
            InvalidCardTransitionError, match="out_of_hours_not_permitted"
        ):
            _manager_create(
                service,
                ticket="910-000026",
                start=outside + timedelta(days=1),
                allow_out_of_hours=False,
            )
        connection.commit()

    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        for ticket in ("910-000023", "910-000025", "910-000026"):
            cursor.execute(
                "SELECT count(*) FROM connection_cards WHERE omnidesk_ticket_number=%s",
                (ticket,),
            )
            assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM assignment_cycles c "
            "JOIN connection_cards card ON card.id=c.card_id "
            "WHERE card.omnidesk_ticket_number "
            "IN ('910-000023', '910-000025', '910-000026')"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM assignment_attempts a "
            "JOIN connection_cards card ON card.id=a.card_id "
            "WHERE card.omnidesk_ticket_number "
            "IN ('910-000023', '910-000025', '910-000026')"
        )
        assert cursor.fetchone()[0] == 0
        cursor.execute(
            "SELECT count(*) FROM reminder_schedules r "
            "JOIN connection_cards card ON card.id=r.card_id "
            "WHERE card.omnidesk_ticket_number "
            "IN ('910-000023', '910-000025', '910-000026')"
        )
        assert cursor.fetchone()[0] == 0


def test_postgres_manager_reschedule_selected_l2_persists_scheduling_lifecycle(
    database_url: str,
) -> None:
    inside = (datetime.now(UTC) + timedelta(days=9)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    outside = inside.replace(hour=20)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed_manager_and_l2(connection)
        service = CardService(PostgresCardRepository(connection))
        card = _manager_create(service, ticket="910-000030", start=inside)

        updated = service.reschedule_card(
            card.public_id,
            actor_user_id=91000,
            actor_role_ids={3},
            planned_start_at=outside,
            planned_duration_minutes=90,
            description="agreed evening window",
            reason="client_requested",
            ip_address="127.0.0.1",
            user_agent="postgres-manager-reschedule-test",
            selected_l2_engineer_id=91001,
        )

        assert updated.status_code == 1
        assert updated.l2_engineer_id == 91001
        assert updated.out_of_hours_flag is True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT planned_start_at, planned_duration_minutes, "
                "l2_engineer_id, out_of_hours_flag FROM connection_cards WHERE id=%s",
                (card.id,),
            )
            assert cursor.fetchone() == {
                "planned_start_at": outside,
                "planned_duration_minutes": 90,
                "l2_engineer_id": 91001,
                "out_of_hours_flag": True,
            }
            cursor.execute(
                "SELECT status_code FROM assignment_cycles "
                "WHERE card_id=%s ORDER BY cycle_number",
                (card.id,),
            )
            assert [row["status_code"] for row in cursor.fetchall()] == [3, 1]
            cursor.execute(
                "SELECT status_code, rejection_reason FROM assignment_attempts "
                "WHERE card_id=%s ORDER BY id",
                (card.id,),
            )
            assert [dict(row) for row in cursor.fetchall()] == [
                {"status_code": 3, "rejection_reason": "rescheduled"},
                {"status_code": 0, "rejection_reason": None},
            ]
            cursor.execute(
                "SELECT closed_at IS NULL AS is_active FROM reminder_schedules "
                "WHERE card_id=%s ORDER BY id",
                (card.id,),
            )
            assert [row["is_active"] for row in cursor.fetchall()] == [False, True]
        connection.commit()


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

    class CaseIndexStub:
        def __init__(self, connection):
            self.connection = connection

        def resolve_case_id(self, case_number):
            return case_number.rsplit("-", maxsplit=1)[1]

    app = create_app()
    manager = UserAuthRecord(
        id=91000,
        username="manager",
        password_hash="test",
        full_name="Manager",
        email=None,
        roles=(RoleRecord(id=3, name="Руководитель"),),
    )
    app.dependency_overrides[get_current_user] = lambda: manager
    app.dependency_overrides[manager_api.get_omnidesk_ticket_client] = OmnideskStub
    monkeypatch.setattr(manager_api, "db_connection", isolated_connection)
    monkeypatch.setattr(ticket_resolver, "CaseIndexRepository", CaseIndexStub)
    planned_start = datetime.now(UTC) + timedelta(hours=2, minutes=5)
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        for user_id in (91001, 91002):
            for weekday in range(1, 8):
                cursor.execute(
                    "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (%s, %s, '00:00', '23:59:59.999999', 'UTC')",
                    (user_id, weekday),
                )
    payload = {
        "case_number": "910-000004",
        "planned_start_at": planned_start.isoformat(),
        "planned_duration_minutes": 60,
        "l2_user_id": 91001,
    }
    payloads = [payload, {**payload, "case_number": "910-000005"}]
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
    expected_details = (
        {"active_card_exists_for_ticket"}
        if same_ticket
        else {"l2_unavailable", "l2_assignment_conflict"}
    )
    assert conflict.json()["detail"] in expected_details
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


def test_postgres_role_create_plans_persist_each_role_contract(
    database_url: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, password_hash, full_name) VALUES (91000, 'pg-l1', 'test', 'PG L1') ON CONFLICT (id) DO NOTHING"
            )
            cursor.execute(
                "INSERT INTO users (id, username, password_hash, full_name) VALUES (91001, 'pg-l2', 'test', 'PG L2') ON CONFLICT (id) DO NOTHING"
            )
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (91000, 1), (91001, 2) ON CONFLICT DO NOTHING"
            )
            cursor.execute(
                "INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (91001, 2, true) ON CONFLICT (user_id, pool_code) DO UPDATE SET is_enabled=true"
            )
            for weekday in range(1, 8):
                cursor.execute(
                    "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (91001, %s, '00:00', '23:59:59.999999', 'UTC')",
                    (weekday,),
                )

        service = CardService(PostgresCardRepository(connection))

        def create(*, ticket: str, scenario: CreateScenario, start: datetime, **kwargs):
            return service.create_card(
                CardCreateRequest(
                    omnidesk_ticket_number=ticket,
                    planned_start_at=start,
                    planned_duration_minutes=60,
                ),
                actor_user_id=91000 if scenario == CreateScenario.L1 else 91001,
                ip_address=None,
                user_agent=None,
                role_create_plan=validate_role_create(
                    scenario=scenario,
                    planned_start_at=start,
                    planned_duration_minutes=60,
                    now=now,
                    **kwargs,
                ),
            )

        l1 = create(
            ticket="910-000010",
            scenario=CreateScenario.L1,
            start=now + timedelta(hours=3),
        )
        l2 = create(
            ticket="910-000011",
            scenario=CreateScenario.L2_SELF,
            start=now + timedelta(hours=5),
        )
        urgent = create(
            ticket="910-000012",
            scenario=CreateScenario.L2_URGENT,
            start=now + timedelta(minutes=5),
            urgent_reason="incident",
        )
        retroactive = create(
            ticket="910-000013",
            scenario=CreateScenario.L2_RETROACTIVE,
            start=now - timedelta(hours=2),
            result_code=0,
            engineer_report="completed retroactively",
        )
        connection.commit()

        assert l1.created_by_id == 91000
        assert l2.status_code == 1 and l2.l2_engineer_id == 91001
        assert urgent.status_code == 1 and urgent.urgent_reason == "incident"
        assert retroactive.status_code == 5
        assert retroactive.actual_end_at == now - timedelta(hours=1)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_user_id FROM audit_log WHERE entity_id=%s AND action_code=0",
                (retroactive.id,),
            )
            assert cursor.fetchone()["actor_user_id"] == 91001

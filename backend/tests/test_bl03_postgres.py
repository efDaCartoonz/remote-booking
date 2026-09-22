import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import CardStatus, RoleId
from app.cards.create_policy import CreateScenario, validate_role_create
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService, InvalidCardTransitionError

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture
def database_url() -> str:
    return os.getenv("PSYCOPG_DATABASE_URL") or os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


@pytest.fixture(autouse=True)
def cleanup(database_url: str):
    yield
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM omnidesk_internal_note_outbox")
        cursor.execute(
            "DELETE FROM connection_cards WHERE omnidesk_ticket_number LIKE '930-%'"
        )
        cursor.execute("DELETE FROM users WHERE id BETWEEN 93000 AND 93010")
        cursor.execute("DELETE FROM connection_results WHERE code >= 9300")


def _seed(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (id, username, password_hash, full_name) VALUES (93000, 'bl03-l2', 'test', 'BL03 L2')"
        )
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (93000, %s)",
            (int(RoleId.L2),),
        )
        cursor.execute(
            "INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (93000, 2, true)"
        )
        for weekday in range(1, 8):
            cursor.execute(
                "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (93000, %s, '00:00', '23:59:59', 'UTC')",
                (weekday,),
            )
        cursor.execute(
            "INSERT INTO connection_results (code, name, is_active, sort_order) VALUES (9301, 'Active BL03 Result', true, 1) ON CONFLICT (code) DO UPDATE SET is_active=true"
        )
        cursor.execute(
            "INSERT INTO connection_results (code, name, is_active, sort_order) VALUES (9302, 'Inactive BL03 Result', false, 2) ON CONFLICT (code) DO UPDATE SET is_active=false"
        )


def test_normal_completion_persists_actual_duration_and_outbox_intent(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number="930-000001",
                planned_start_at=datetime.now(UTC) + timedelta(hours=3),
                planned_duration_minutes=60,
                l2_engineer_id=93000,
            ),
            actor_user_id=93000,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card.public_id,
            actor_user_id=93000,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card.public_id,
            actor_user_id=93000,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        completed = service.complete_card(
            card.public_id,
            result_code=9301,
            engineer_report="Completed with 50 minutes actual duration",
            actual_duration_minutes=50,
            actor_user_id=93000,
            comment="done",
            ip_address="127.0.0.1",
            user_agent="test",
        )
        connection.commit()

        assert completed.status_code == int(CardStatus.COMPLETED)
        assert completed.actual_duration_minutes == 50
        assert completed.result_code == 9301

        # Check PostgreSQL database rows
        persisted_card = connection.execute(
            "SELECT status_code, result_code, actual_duration_minutes FROM connection_cards WHERE id = %s",
            (card.id,),
        ).fetchone()
        assert persisted_card["status_code"] == int(CardStatus.COMPLETED)
        assert persisted_card["result_code"] == 9301
        assert persisted_card["actual_duration_minutes"] == 50

        # Check outbox intent row
        outbox_row = connection.execute(
            "SELECT * FROM omnidesk_internal_note_outbox WHERE card_id = %s",
            (card.id,),
        ).fetchone()
        assert outbox_row is not None
        assert outbox_row["omnidesk_ticket_number"] == "930-000001"
        assert outbox_row["status_code"] == 0
        assert outbox_row["payload"]["card_id"] == card.id
        assert outbox_row["payload"]["ticket_number"] == "930-000001"
        assert outbox_row["payload"]["result_code"] == 9301
        assert outbox_row["payload"]["actual_duration_minutes"] == 50
        assert "case_id" not in outbox_row["payload"]

        duplicate_id = repository.create_omnidesk_internal_note_intent(
            card_id=card.id,
            source_event_id=outbox_row["source_event_id"],
            omnidesk_ticket_number="930-000001",
            payload={"card_id": card.id},
        )
        assert duplicate_id == 0
        assert (
            connection.execute(
                "SELECT count(*) FROM omnidesk_internal_note_outbox WHERE card_id = %s",
                (card.id,),
            ).fetchone()["count"]
            == 1
        )


def test_completed_retroactive_persists_derived_duration_and_rejects_inactive_code(
    database_url: str,
) -> None:
    now = datetime(2026, 9, 21, 10, tzinfo=UTC)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        # Inactive result code must be rejected
        inactive_plan = validate_role_create(
            scenario=CreateScenario.L2_RETROACTIVE,
            planned_start_at=now - timedelta(hours=2),
            planned_duration_minutes=90,
            result_code=9302,
            engineer_report="Should fail",
            now=now,
        )
        with pytest.raises(
            InvalidCardTransitionError, match="result_code_inactive_or_unknown"
        ):
            service.create_card(
                CardCreateRequest(
                    omnidesk_ticket_number="930-000002",
                    planned_start_at=now - timedelta(hours=2),
                    planned_duration_minutes=90,
                ),
                actor_user_id=93000,
                ip_address=None,
                user_agent=None,
                role_create_plan=inactive_plan,
            )
        connection.rollback()

        # Active result code succeeds, derives duration, creates note outbox intent
        active_plan = validate_role_create(
            scenario=CreateScenario.L2_RETROACTIVE,
            planned_start_at=now - timedelta(hours=2),
            planned_duration_minutes=90,
            result_code=9301,
            engineer_report="Retroactive completed",
            now=now,
        )
        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number="930-000002",
                planned_start_at=now - timedelta(hours=2),
                planned_duration_minutes=90,
            ),
            actor_user_id=93000,
            ip_address=None,
            user_agent=None,
            role_create_plan=active_plan,
        )
        connection.commit()

        assert card.status_code == int(CardStatus.COMPLETED)
        assert card.actual_duration_minutes == 90
        assert card.result_code == 9301

        outbox_row = connection.execute(
            "SELECT * FROM omnidesk_internal_note_outbox WHERE card_id = %s",
            (card.id,),
        ).fetchone()
        assert outbox_row is not None
        assert outbox_row["payload"]["actual_duration_minutes"] == 90
        assert outbox_row["payload"]["result_code"] == 9301
        assert "case_id" not in outbox_row["payload"]


def test_in_progress_retroactive_does_not_create_outbox_intent(
    database_url: str,
) -> None:
    now = datetime(2026, 9, 21, 10, tzinfo=UTC)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        in_progress_plan = validate_role_create(
            scenario=CreateScenario.L2_RETROACTIVE,
            planned_start_at=now - timedelta(minutes=30),
            planned_duration_minutes=60,
            now=now,
        )
        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number="930-000003",
                planned_start_at=now - timedelta(minutes=30),
                planned_duration_minutes=60,
            ),
            actor_user_id=93000,
            ip_address=None,
            user_agent=None,
            role_create_plan=in_progress_plan,
        )
        connection.commit()

        assert card.status_code == int(CardStatus.IN_PROGRESS)
        outbox_count = connection.execute(
            "SELECT count(*) AS count FROM omnidesk_internal_note_outbox WHERE card_id = %s",
            (card.id,),
        ).fetchone()["count"]
        assert outbox_count == 0

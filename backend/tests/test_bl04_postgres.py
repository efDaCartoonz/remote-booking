from unittest.mock import patch
import os
import uuid
from datetime import UTC, datetime, timedelta
import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import ActorType, CardEventType, CardStatus, RoleId
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.cards.repository import PostgresCardRepository
from app.cards.extension import process_due_in_progress_sessions
from app.admin.repository import AdministrativeRepository
from app.notifications import PostgresNotificationService


pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)

# These integration tests may run against a shared database. Keep generated
# ticket numbers within the public 3-6 digit contract and capture inserted card
# IDs so cleanup never relies on a potentially colliding ticket number.
_run_token = str(uuid.uuid4().int % 900 + 100)
_created_card_ids: list[int] = []
_l2_user_id = None
_l2_user_2_id = None
_manager_user_id = None


def _ticket(suffix: str) -> str:
    return f"{_run_token}-{suffix}"


@pytest.fixture
def database_url() -> str:
    return os.getenv("PSYCOPG_DATABASE_URL") or os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


@pytest.fixture(autouse=True)
def cleanup(database_url: str, monkeypatch):
    global _run_token
    _run_token = str(uuid.uuid4().int % 900 + 100)
    _created_card_ids.clear()
    original_create_card = PostgresCardRepository.create_card

    def capture_created_card(repository, data):
        card = original_create_card(repository, data)
        _created_card_ids.append(card.id)
        return card

    monkeypatch.setattr(PostgresCardRepository, "create_card", capture_created_card)
    yield
    user_ids = [_l2_user_id, _l2_user_2_id, _manager_user_id]
    user_ids = [user_id for user_id in user_ids if user_id is not None]
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM notifications WHERE card_id = ANY(%s)",
            (_created_card_ids,),
        )
        cursor.execute(
            "DELETE FROM session_extensions WHERE card_id = ANY(%s)",
            (_created_card_ids,),
        )
        cursor.execute(
            "DELETE FROM card_events WHERE card_id = ANY(%s)",
            (_created_card_ids,),
        )
        cursor.execute(
            "DELETE FROM audit_log WHERE entity_type IN ('urgent_collision', 'connection_card') AND entity_id = ANY(%s)",
            (_created_card_ids,),
        )
        cursor.execute(
            "DELETE FROM connection_cards WHERE id = ANY(%s)",
            (_created_card_ids,),
        )
        # IDs are returned by INSERTs performed in _seed for this run only.
        cursor.execute("DELETE FROM users WHERE id = ANY(%s)", (user_ids,))


def _seed(connection: psycopg.Connection) -> None:
    global _l2_user_id, _l2_user_2_id, _manager_user_id
    _l2_user_id = _l2_user_2_id = _manager_user_id = None
    username_token = uuid.uuid4().hex
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', 'BL04 L2') RETURNING id",
            (f"bl04-l2-{username_token}",),
        )
        _l2_user_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
            (_l2_user_id, int(RoleId.L2)),
        )
        cursor.execute(
            "INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (%s, 2, true)",
            (_l2_user_id,),
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', 'BL04 L2 2') RETURNING id",
            (f"bl04-l2-2-{username_token}",),
        )
        _l2_user_2_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
            (_l2_user_2_id, int(RoleId.L2)),
        )
        cursor.execute(
            "INSERT INTO distribution_members (user_id, pool_code, is_enabled) VALUES (%s, 2, true)",
            (_l2_user_2_id,),
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'test', 'BL04 Manager') RETURNING id",
            (f"bl04-manager-{username_token}",),
        )
        _manager_user_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
            (_manager_user_id, int(RoleId.MANAGER)),
        )
        for weekday in range(1, 8):
            cursor.execute(
                "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (%s, %s, '00:00', '23:59:59', 'UTC')",
                (_l2_user_id, weekday),
            )
            cursor.execute(
                "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (%s, %s, '00:00', '23:59:59', 'UTC')",
                (_l2_user_2_id, weekday),
            )
            cursor.execute(
                "INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone) VALUES (%s, %s, '00:00', '23:59:59', 'UTC')",
                (_manager_user_id, weekday),
            )


def test_process_due_in_progress_sessions_extends_card(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        now = datetime.now(UTC)
        past_start = now - timedelta(hours=2)

        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000001"),
                planned_start_at=past_start,
                planned_duration_minutes=60,  # End 1 hour ago
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_type_code FROM card_events WHERE card_id=%s AND event_type_code=%s ORDER BY id DESC LIMIT 1",
                (card.id, int(CardEventType.STATUS_CHANGED)),
            )
            assert cursor.fetchone()["actor_type_code"] == ActorType.INTERNAL_USER

        admin = AdministrativeRepository(connection)
        admin.set_extension_interval(interval_seconds=900, actor_user_id=_l2_user_id)

        # Run process
        count = process_due_in_progress_sessions(connection, now=now)
        assert count == 1

        updated_card = service.get_card(card.public_id)
        assert updated_card.extension_count == 1
        assert updated_card.planned_duration_minutes == 75

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM session_extensions WHERE card_id = %s",
                (updated_card.id,),
            )
            ext = cursor.fetchone()
            assert ext["interval_seconds"] == 900
            assert ext["has_collision"] is False


def test_process_due_rechecks_planned_end_after_lock(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)
        now = datetime.now(UTC)
        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000008"),
                planned_start_at=now - timedelta(minutes=60, seconds=1),
                planned_duration_minutes=60,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address=None,
            user_agent=None,
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        AdministrativeRepository(connection).set_extension_interval(
            interval_seconds=900, actor_user_id=_l2_user_id
        )

        original_get_for_update = repository.get_card_by_id_for_update

        def update_before_lock(card_id: int):
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE connection_cards SET planned_duration_minutes=75 WHERE id=%s",
                    (card_id,),
                )
            return original_get_for_update(card_id)

        with patch(
            "app.cards.extension.PostgresCardRepository.get_card_by_id_for_update",
            side_effect=update_before_lock,
        ):
            assert process_due_in_progress_sessions(connection, now=now) == 0

        updated = service.get_card(card.public_id)
        assert updated.planned_duration_minutes == 75
        assert updated.extension_count == 0
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS n FROM session_extensions WHERE card_id=%s",
                (updated.id,),
            )
            assert cursor.fetchone()["n"] == 0


def test_process_due_in_progress_sessions_with_collision(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        now = datetime.now(UTC)
        past_start = now - timedelta(hours=2)

        card1 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000002"),
                planned_start_at=past_start,
                planned_duration_minutes=60,  # End 1 hour ago
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        # Create collision card: overlapping with extension
        card2 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000003"),
                planned_start_at=past_start
                + timedelta(minutes=65),  # Collides with 60m + 15m extension
                planned_duration_minutes=30,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card2.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        # Run process
        count = process_due_in_progress_sessions(connection, now=now)
        assert count == 1

        updated_card1 = service.get_card(card1.public_id)
        assert updated_card1.extension_count == 1
        assert updated_card1.extension_collision_flag is True

        updated_card2 = service.get_card(card2.public_id)
        assert CardStatus(updated_card2.status_code) == CardStatus.CREATED  # Displaced

        # Validate atomicity - next run should be no-op because it's already extended
        count2 = process_due_in_progress_sessions(
            connection, now=past_start + timedelta(minutes=60, seconds=400)
        )
        assert count2 == 0


def test_process_due_in_progress_sessions_failure_isolation(
    database_url: str, caplog
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        now = datetime.now(UTC)
        past_start = now - timedelta(hours=2)

        card1 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000006"),
                planned_start_at=past_start,
                planned_duration_minutes=60,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        card2 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000007"),
                planned_start_at=past_start - timedelta(hours=3),
                planned_duration_minutes=60,
                l2_engineer_id=_l2_user_2_id,
            ),
            actor_user_id=_l2_user_2_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card2.public_id,
            actor_user_id=_l2_user_2_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card2.public_id,
            actor_user_id=_l2_user_2_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        admin = AdministrativeRepository(connection)
        admin.set_extension_interval(interval_seconds=900, actor_user_id=_l2_user_id)

        original_get_for_update = repository.get_card_by_id_for_update

        def fail_first_card(card_id: int):
            if card_id == card1.id:
                raise RuntimeError("simulated card failure")
            return original_get_for_update(card_id)

        with patch(
            "app.cards.extension.PostgresCardRepository.get_card_by_id_for_update",
            side_effect=fail_first_card,
        ):
            count = process_due_in_progress_sessions(connection, now=now)

        assert count == 1
        assert "session extension failed for card id" in caplog.text
        assert service.get_card(card1.public_id).extension_count == 0
        assert service.get_card(card2.public_id).extension_count == 1


def test_process_due_in_progress_sessions_max_12_hours(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        now = datetime.now(UTC)
        past_start = now - timedelta(hours=12, minutes=1)

        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000008"),
                planned_start_at=past_start,
                planned_duration_minutes=720,  # exactly 12 hours
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        admin = AdministrativeRepository(connection)
        admin.set_extension_interval(interval_seconds=900, actor_user_id=_l2_user_id)

        # Before processing, status is IN_PROGRESS
        assert (
            CardStatus(service.get_card(card.public_id).status_code)
            == CardStatus.IN_PROGRESS
        )

        count = process_due_in_progress_sessions(connection, now=now)
        # Should not count as an extension, but as a completion
        assert count == 0

        updated_card = service.get_card(card.public_id)
        assert (
            CardStatus(updated_card.status_code) == CardStatus.COMPLETED_PENDING_RESULT
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT comment FROM card_events WHERE card_id=%s AND event_type_code=%s ORDER BY id DESC LIMIT 1",
                (updated_card.id, 1),
            )
            row = cursor.fetchone()
            assert row["comment"] == "завершено автоматически"


def test_l2_cannot_have_multiple_in_progress_cards(database_url: str) -> None:
    from app.cards.service import InvalidCardTransitionError
    import pytest

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        now = datetime.now(UTC)

        card1 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000111"),
                planned_start_at=now - timedelta(hours=1),
                planned_duration_minutes=60,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        card2 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000222"),
                planned_start_at=now + timedelta(hours=1),
                planned_duration_minutes=60,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card2.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        with pytest.raises(InvalidCardTransitionError) as excinfo:
            service.start_card(
                card2.public_id,
                actor_user_id=_l2_user_id,
                comment=None,
                ip_address=None,
                user_agent=None,
            )

        assert "engineer_already_has_in_progress_card" in str(excinfo.value)


def test_urgent_attempts_cannot_overlap_in_progress(database_url: str) -> None:
    from app.cards.service import InvalidCardTransitionError
    import pytest

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        now = datetime.now(UTC)

        card1 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000333"),
                planned_start_at=now - timedelta(minutes=30),
                planned_duration_minutes=120,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        with pytest.raises(
            InvalidCardTransitionError, match="urgent_collision_in_progress"
        ):
            service.create_card(
                CardCreateRequest(
                    omnidesk_ticket_number=_ticket("000444"),
                    planned_start_at=now,
                    planned_duration_minutes=30,
                    l2_engineer_id=_l2_user_id,
                    urgency_code=1,
                ),
                actor_user_id=_l2_user_id,
                ip_address="127.0.0.1",
                user_agent="test",
                manual_assignment=True,
                allow_out_of_hours=True,
            )


def test_extension_only_displaces_assigned_confirmed(database_url: str) -> None:
    from app.admin.repository import AdministrativeRepository
    from app.cards.extension import process_due_in_progress_sessions

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(repository)

        now = datetime.now(UTC)

        # Card 1 is the one that will be extended (assigned to L2 _l2_user_id)
        card1 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000555"),
                planned_start_at=now - timedelta(minutes=60),
                planned_duration_minutes=60,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card1.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        # Card 2 is an IN_PROGRESS card assigned to a DIFFERENT L2 (_l2_user_2_id) in the extension window
        card2 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000666"),
                planned_start_at=now + timedelta(minutes=5),
                planned_duration_minutes=30,
                l2_engineer_id=_l2_user_2_id,
            ),
            actor_user_id=_l2_user_2_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card2.public_id,
            actor_user_id=_l2_user_2_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card2.public_id,
            actor_user_id=_l2_user_2_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        # Card 3 is a CONFIRMED card for L2 _l2_user_id in the extension window (should be displaced)
        card3 = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000777"),
                planned_start_at=now + timedelta(minutes=5),
                planned_duration_minutes=30,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card3.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        admin = AdministrativeRepository(connection)
        admin.set_extension_interval(interval_seconds=900, actor_user_id=_l2_user_id)

        count = process_due_in_progress_sessions(connection, now=now)
        assert count == 1

        updated_card1 = service.get_card(card1.public_id)
        assert updated_card1.extension_count == 1

        updated_card2 = service.get_card(card2.public_id)
        assert updated_card2.status_code == int(CardStatus.IN_PROGRESS)  # Not displaced
        assert updated_card2.l2_engineer_id == _l2_user_2_id

        updated_card3 = service.get_card(card3.public_id)
        # CONFIRMED card displaced back to CREATED
        assert updated_card3.l2_engineer_id is None
        assert updated_card3.status_code == int(CardStatus.CREATED)


def test_process_due_in_progress_boundary_710m_autoends_and_allows_direct_completion(
    database_url: str,
) -> None:
    """Regression test for BL-04 P1: 710m session with +15m interval must auto-end without CheckViolation deadlock."""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(
            repository=repository,
            notifications=PostgresNotificationService(connection),
        )

        now = datetime.now(UTC)
        past_start = now - timedelta(minutes=710)

        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000710"),
                planned_start_at=past_start,
                planned_duration_minutes=710,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        admin = AdministrativeRepository(connection)
        admin.set_extension_interval(interval_seconds=900, actor_user_id=_l2_user_id)

        # Before processing, status is IN_PROGRESS
        assert service.get_card(card.public_id).status_code == int(
            CardStatus.IN_PROGRESS
        )

        # 1st run: 710 + 15 = 725 >= 720 -> must auto-end without DB CheckViolation
        count = process_due_in_progress_sessions(connection, now=now)
        assert count == 0

        updated_card = service.get_card(card.public_id)
        assert updated_card.status_code == int(CardStatus.COMPLETED_PENDING_RESULT)
        assert updated_card.planned_duration_minutes == 710  # Preserved at 710, not 725
        assert updated_card.extension_count == 0

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT comment, actor_type_code FROM card_events WHERE card_id=%s AND event_type_code=%s ORDER BY id DESC LIMIT 1",
                (updated_card.id, int(CardEventType.STATUS_CHANGED)),
            )
            row = cursor.fetchone()
            assert row["comment"] == "завершено автоматически"

            cursor.execute(
                "SELECT recipient_user_id, channel_code FROM notifications WHERE card_id=%s AND event_type_code=7",
                (updated_card.id,),
            )
            notifs = cursor.fetchall()
            recipients_and_channels = {
                (n["recipient_user_id"], n["channel_code"]) for n in notifs
            }
            assert (_l2_user_id, 0) in recipients_and_channels
            assert (_l2_user_id, 1) in recipients_and_channels
            assert (_manager_user_id, 0) in recipients_and_channels
            assert (_manager_user_id, 1) in recipients_and_channels

        # 2nd run (subsequent tick): prove no repeated failure or duplicate autoend
        count2 = process_due_in_progress_sessions(
            connection, now=now + timedelta(minutes=5)
        )
        assert count2 == 0
        assert service.get_card(card.public_id).status_code == int(
            CardStatus.COMPLETED_PENDING_RESULT
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) as n FROM card_events WHERE card_id=%s AND comment='завершено автоматически'",
                (updated_card.id,),
            )
            assert cursor.fetchone()["n"] == 1

        # Direct result completion by assigned L2
        completed_card = service.complete_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            actor_role_ids=[int(RoleId.L2)],
            result_code=0,
            engineer_report="Completed after auto-end",
            actual_duration_minutes=710,
            comment="Result submitted",
            ip_address="127.0.0.1",
            user_agent="test",
        )
        assert completed_card.status_code == int(CardStatus.COMPLETED)
        assert completed_card.result_code == 0
        assert completed_card.engineer_report == "Completed after auto-end"


def test_process_due_in_progress_boundary_705m_reaches_exact_12h_boundary(
    database_url: str,
) -> None:
    """Regression test for BL-04 boundary: 705m session + 15m interval reaches exactly 720m (12h)."""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(
            repository=repository,
            notifications=PostgresNotificationService(connection),
        )

        now = datetime.now(UTC)
        past_start = now - timedelta(minutes=705)

        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000705"),
                planned_start_at=past_start,
                planned_duration_minutes=705,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        admin = AdministrativeRepository(connection)
        admin.set_extension_interval(interval_seconds=900, actor_user_id=_l2_user_id)

        # 1st run: 705 + 15 = 720 -> extends successfully to exactly 12 hours
        count = process_due_in_progress_sessions(connection, now=now)
        assert count == 1

        updated_card = service.get_card(card.public_id)
        assert updated_card.status_code == int(CardStatus.IN_PROGRESS)
        assert updated_card.planned_duration_minutes == 720
        assert updated_card.extension_count == 1

        # 2nd run (next tick): 720 + 15 = 735 > 720 -> must auto-end
        count2 = process_due_in_progress_sessions(
            connection, now=now + timedelta(minutes=15)
        )
        assert count2 == 0

        final_card = service.get_card(card.public_id)
        assert final_card.status_code == int(CardStatus.COMPLETED_PENDING_RESULT)
        assert final_card.planned_duration_minutes == 720
        assert final_card.extension_count == 1

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT comment FROM card_events WHERE card_id=%s AND event_type_code=%s ORDER BY id DESC LIMIT 1",
                (final_card.id, int(CardEventType.STATUS_CHANGED)),
            )
            assert cursor.fetchone()["comment"] == "завершено автоматически"

        # Subsequent tick: no repeat autoend
        count3 = process_due_in_progress_sessions(
            connection, now=now + timedelta(minutes=30)
        )
        assert count3 == 0

        # Direct result completion succeeds
        completed_card = service.complete_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            actor_role_ids=[int(RoleId.L2)],
            result_code=1,
            engineer_report="Reconnection needed",
            actual_duration_minutes=705,
            comment="Result submitted",
            ip_address="127.0.0.1",
            user_agent="test",
        )
        assert completed_card.status_code == int(CardStatus.COMPLETED)
        assert completed_card.result_code == 1


def test_process_due_in_progress_700m_extends_to_715_then_autoends_on_next_tick(
    database_url: str,
) -> None:
    """Test 700m session: +15m extends to 715m (<720m), then next tick (715+15=730>=720m) auto-ends."""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        _seed(connection)
        repository = PostgresCardRepository(connection)
        service = CardService(
            repository=repository,
            notifications=PostgresNotificationService(connection),
        )

        now = datetime.now(UTC)
        past_start = now - timedelta(minutes=700)

        card = service.create_card(
            CardCreateRequest(
                omnidesk_ticket_number=_ticket("000700"),
                planned_start_at=past_start,
                planned_duration_minutes=700,
                l2_engineer_id=_l2_user_id,
            ),
            actor_user_id=_l2_user_id,
            ip_address="127.0.0.1",
            user_agent="test",
            manual_assignment=True,
            allow_out_of_hours=True,
        )
        service.confirm_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )
        service.start_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

        admin = AdministrativeRepository(connection)
        admin.set_extension_interval(interval_seconds=900, actor_user_id=_l2_user_id)

        # 1st run: 700 + 15 = 715 < 720 -> extends successfully
        count1 = process_due_in_progress_sessions(connection, now=now)
        assert count1 == 1

        card_after_ext = service.get_card(card.public_id)
        assert card_after_ext.status_code == int(CardStatus.IN_PROGRESS)
        assert card_after_ext.planned_duration_minutes == 715
        assert card_after_ext.extension_count == 1

        # 2nd run at 715m (15m later): 715 + 15 = 730 >= 720 -> auto-ends
        count2 = process_due_in_progress_sessions(
            connection, now=now + timedelta(minutes=15)
        )
        assert count2 == 0

        card_after_autoend = service.get_card(card.public_id)
        assert card_after_autoend.status_code == int(
            CardStatus.COMPLETED_PENDING_RESULT
        )
        assert card_after_autoend.planned_duration_minutes == 715  # Preserved at 715
        assert card_after_autoend.extension_count == 1

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT actor_type_code, comment FROM card_events WHERE card_id=%s AND event_type_code=%s ORDER BY id DESC LIMIT 1",
                (card_after_autoend.id, int(CardEventType.STATUS_CHANGED)),
            )
            event = cursor.fetchone()
            assert event["comment"] == "завершено автоматически"
            assert event["actor_type_code"] == ActorType.SYSTEM
            cursor.execute(
                "SELECT actor_type_code FROM audit_log WHERE entity_type='connection_card' AND entity_id=%s ORDER BY id DESC LIMIT 1",
                (card_after_autoend.id,),
            )
            assert cursor.fetchone()["actor_type_code"] == ActorType.SYSTEM

        # 3rd run: no repeat autoend
        count3 = process_due_in_progress_sessions(
            connection, now=now + timedelta(minutes=30)
        )
        assert count3 == 0

        # Direct result completion succeeds
        completed_card = service.complete_card(
            card.public_id,
            actor_user_id=_l2_user_id,
            actor_role_ids=[int(RoleId.L2)],
            result_code=0,
            engineer_report="Completed successfully",
            actual_duration_minutes=715,
            comment="Done",
            ip_address="127.0.0.1",
            user_agent="test",
        )
        assert completed_card.status_code == int(CardStatus.COMPLETED)

import os
import uuid
from datetime import UTC, datetime, timedelta
import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.constants import ActorType, CardEventType
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.cards.repository import PostgresCardRepository
from unittest.mock import patch
from app.integrations.omnidesk_outbox import (
    PostgresOmnideskOutboxRepository,
    deliver_pending_omnidesk_outbox,
)
from app.frame.omnidesk import OmnideskTicketClient, OmnideskTicket, OmnideskCaseList

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)

_run_token = str(uuid.uuid4().int % 900 + 100)
_run_idx = 0


def next_ticket():
    global _run_idx
    _run_idx += 1
    return f"{_run_token}-90000{_run_idx}"


class FakeOmnideskClient(OmnideskTicketClient):
    def __init__(self):
        self.assignments = []
        self.internal_notes = []
        self.public_messages = []

    def get_ticket_by_case_id(self, case_id: str) -> OmnideskTicket | None:
        return OmnideskTicket(
            case_id=case_id,
            number=case_id,  # Simplified for test
            user_id="1",
            status="open",
        )

    def reopen_ticket(self, case_id: str) -> OmnideskTicket:
        return self.get_ticket_by_case_id(case_id)

    def list_cases(self, **kwargs) -> OmnideskCaseList:
        return OmnideskCaseList(items=[], total_count=0)

    def assign_staff(self, case_id: str, staff_id: int) -> None:
        self.assignments.append((case_id, staff_id))

    def add_internal_note(
        self, case_id: str, content: str, staff_id: int | None = None
    ) -> None:
        self.internal_notes.append((case_id, content, staff_id))

    def send_public_message(
        self, case_id: str, content: str, staff_id: int | None = None
    ) -> None:
        self.public_messages.append((case_id, content, staff_id))


@pytest.fixture
def database_url() -> str:
    return (
        os.getenv("PSYCOPG_DATABASE_URL")
        or os.environ.get("DATABASE_URL", "").replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        or "postgresql://nimda:nimda@postgres:5432/rdm"
    )


@pytest.fixture
def connection(database_url: str):
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        yield conn
        conn.rollback()


def test_ie01_omnidesk_outbox_integration(connection: psycopg.Connection):
    card_repo = PostgresCardRepository(connection)
    service = CardService(card_repo)
    outbox_repo = PostgresOmnideskOutboxRepository(connection)

    # 1. Enable public notifications in system settings
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO system_settings (key, value) VALUES ('omnidesk_public_notification_enabled', 'true'), ('omnidesk_public_notification_template', '\"Заявка подтверждена, ожидайте специалиста.\"') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
        # Also need a user with omnidesk_staff_id to test staff_id assignment
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, omnidesk_staff_id) VALUES ('staff1', 'x', 'Staff One', '12345') RETURNING id"
        )
        staff_user_id = cursor.fetchone()["id"]

    ticket = next_ticket()

    # 2. Add ticket to case index so it resolves
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO omnidesk_case_index (case_id, case_number, status, omnidesk_created_at, omnidesk_updated_at) VALUES (%s, %s, %s, now(), now())",
            ("c" + ticket, ticket, "open"),
        )

    # 3. Create card
    card = service.create_card(
        CardCreateRequest(
            omnidesk_ticket_number=ticket,
            client_contact_value="client@test.com",
            planned_start_at=datetime.now(UTC) + timedelta(hours=1),
            planned_duration_minutes=60,
        ),
        actor_user_id=staff_user_id,
        actor_type=ActorType.SYSTEM,
        ip_address=None,
        user_agent=None,
    )

    # 4. Assign L1 / L2 and confirm in database
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE connection_cards SET status_code = 1, l1_owner_id = %s, l2_engineer_id = %s WHERE id = %s",
            (staff_user_id, staff_user_id, card.id),
        )

    # Trigger L2 assign outbox
    card_repo.add_card_event(
        card_id=card.id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_user_id,
        actor_type=ActorType.SYSTEM,
        old_values={"l2_engineer_id": None},
        new_values={"l2_engineer_id": staff_user_id},
        comment="L2 assign test",
    )

    # Trigger L1 assign outbox
    card_repo.add_card_event(
        card_id=card.id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_user_id,
        actor_type=ActorType.SYSTEM,
        old_values={"l1_owner_id": None},
        new_values={"l1_owner_id": staff_user_id},
        comment="L1 assign test",
    )

    # Trigger confirm
    card_repo.add_card_event(
        card_id=card.id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_user_id,
        actor_type=ActorType.SYSTEM,
        old_values={"status_code": 0},
        new_values={
            "status_code": 1,
            "planned_start_at": card.planned_start_at.isoformat(),
        },
        comment="Confirm test",
    )

    # Process
    client = FakeOmnideskClient()
    # Mock datetime to future to process the 15-min delayed notification

    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE omnidesk_outbox SET next_attempt_at = %s",
            (datetime.now(UTC) - timedelta(minutes=5),),
        )

    with patch(
        "app.integrations.omnidesk_outbox.resolve_ticket_by_case_number"
    ) as mock_resolve:
        mock_resolve.return_value = OmnideskTicket(
            case_id="c" + ticket, number=ticket, user_id="u1", status="open"
        )
        processed = deliver_pending_omnidesk_outbox(outbox_repo, client)
    assert processed >= 3

    assert len(client.internal_notes) >= 1
    assert any("создана" in content for _, content, _ in client.internal_notes)

    assert len(client.assignments) >= 1
    assert client.assignments[0] == ("c" + ticket, 12345)

    assert len(client.public_messages) >= 1
    assert "подтверждена" in client.public_messages[0][1]


def test_ie01_idempotent_l1_l2_assignments(connection: psycopg.Connection):
    card_repo = PostgresCardRepository(connection)
    ticket = next_ticket()

    # Enable public notifications in system settings
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO system_settings (key, value) VALUES ('omnidesk_public_notification_enabled', 'true'), ('omnidesk_public_notification_template', '\"Заявка подтверждена, ожидайте специалиста.\"') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, omnidesk_staff_id) VALUES ('staff_idempotent', 'x', 'Staff Idem', '54321') RETURNING id"
        )
        staff_id = cursor.fetchone()["id"]

        cursor.execute(
            """
            INSERT INTO connection_cards (omnidesk_ticket_number, status_code, criticality_code, urgency_code, planned_start_at, planned_duration_minutes, assignment_method_code, out_of_hours_flag, retroactive_flag, created_source_code)
            VALUES (%s, 0, 0, 0, now(), 60, 0, false, false, 0)
            RETURNING id
            """,
            (ticket,),
        )
        card_id = cursor.fetchone()["id"]

    # Same event triggers both L1 and L2 assignment
    event_id = card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_id,
        actor_type=ActorType.SYSTEM,
        old_values={"l1_owner_id": None, "l2_engineer_id": None},
        new_values={"l1_owner_id": staff_id, "l2_engineer_id": staff_id},
        comment="test double assignment idempotency",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT action_type FROM omnidesk_outbox WHERE source_event_id = %s",
            (event_id,),
        )
        rows = cursor.fetchall()

    actions = {r["action_type"] for r in rows}
    assert "l1_assignment" in actions
    assert "l2_assignment" in actions
    assert len(actions) == 2


def test_ie01_safe_staff_id_error(connection: psycopg.Connection):
    # Test that when a staff ID is missing, the error identifies the user without external data
    # and notifies admin + manager
    repo = PostgresOmnideskOutboxRepository(connection)
    client = FakeOmnideskClient()
    ticket = next_ticket()

    # Insert a user without omnidesk_staff_id
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, omnidesk_staff_id) VALUES ('no_staff', 'x', 'No Staff', NULL) RETURNING id"
        )
        user_id = cursor.fetchone()["id"]

        expected_recipient_ids = set()
        for username, role_id, channel_value in (
            ("ie01_admin_alert", 4, "tg-admin-ie01"),
            ("ie01_manager_alert", 3, "tg-manager-ie01"),
        ):
            cursor.execute(
                "INSERT INTO users (username, password_hash, full_name) VALUES (%s, 'x', %s) RETURNING id",
                (username, username),
            )
            recipient_id = cursor.fetchone()["id"]
            expected_recipient_ids.add(recipient_id)
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
                (recipient_id, role_id),
            )
            cursor.execute(
                "INSERT INTO user_settings (user_id, telegram_chat_id) VALUES (%s, %s)",
                (recipient_id, channel_value),
            )

        cursor.execute(
            """
            INSERT INTO connection_cards (omnidesk_ticket_number, status_code, criticality_code, urgency_code, planned_start_at, planned_duration_minutes, assignment_method_code, out_of_hours_flag, retroactive_flag, created_source_code)
            VALUES (%s, 0, 0, 0, now(), 60, 0, false, false, 0)
            RETURNING id
            """,
            (ticket,),
        )
        card_id = cursor.fetchone()["id"]

        cursor.execute(
            """
            INSERT INTO card_events (card_id, event_type_code, actor_type_code)
            VALUES (%s, 0, 0)
            RETURNING id
            """,
            (card_id,),
        )
        event_id = cursor.fetchone()["id"]

        # Insert a pending outbox intent
        cursor.execute(
            """
            INSERT INTO omnidesk_outbox (card_id, source_event_id, omnidesk_ticket_number, action_type, payload, status_code, next_attempt_at)
            VALUES (%s, %s, %s, 'l1_assignment', %s, 0, now())
            RETURNING id
            """,
            (
                card_id,
                event_id,
                ticket,
                __import__("psycopg").types.json.Jsonb(
                    {"user_id": user_id, "target": "l1"}
                ),
            ),
        )
        intent_id = cursor.fetchone()["id"]

    with patch(
        "app.integrations.omnidesk_outbox.resolve_ticket_by_case_number"
    ) as mock_resolve:
        mock_resolve.return_value = OmnideskTicket(
            case_id="c_dummy", number=ticket, user_id="u1", status="open"
        )
        deliver_pending_omnidesk_outbox(repo, client)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT recipient_user_id, event_type_code FROM notifications WHERE source_event_id = %s AND event_type_code = 8",
            (event_id,),
        )
        notices = cursor.fetchall()
        cursor.execute(
            "SELECT status_code, error_message FROM omnidesk_outbox WHERE id = %s",
            (intent_id,),
        )
        failed_intent = cursor.fetchone()
    assert {notice["recipient_user_id"] for notice in notices} == expected_recipient_ids
    assert len(notices) == 2
    assert failed_intent["status_code"] == 2
    assert f"user_id={user_id}" in failed_intent["error_message"]


def test_ie01_disabling_public_notification_after_queue_suppresses_delivery(
    connection: psycopg.Connection,
):
    card_repo = PostgresCardRepository(connection)
    repo = PostgresOmnideskOutboxRepository(connection)
    ticket = next_ticket()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO system_settings (key, value) VALUES ('omnidesk_public_notification_enabled', 'true'), ('omnidesk_public_notification_template', '\"Client warning\"') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES ('ie01_disabled_warning', 'x', 'IE01 Disabled Warning') RETURNING id"
        )
        actor_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO connection_cards (omnidesk_ticket_number, status_code, criticality_code, urgency_code, planned_start_at, planned_duration_minutes, assignment_method_code, out_of_hours_flag, retroactive_flag, created_source_code) VALUES (%s, 1, 0, 0, now(), 60, 0, false, false, 0) RETURNING id",
            (ticket,),
        )
        card_id = cursor.fetchone()["id"]
    event_id = card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=actor_id,
        actor_type=ActorType.SYSTEM,
        old_values={"status_code": 0},
        new_values={"status_code": 1},
        comment="queue public warning",
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE omnidesk_outbox SET next_attempt_at = now() - interval '1 minute' WHERE source_event_id = %s AND action_type = 'public_notification'",
            (event_id,),
        )
        cursor.execute(
            "UPDATE system_settings SET value = 'false'::jsonb WHERE key = 'omnidesk_public_notification_enabled'"
        )
    client = FakeOmnideskClient()
    with patch(
        "app.integrations.omnidesk_outbox.resolve_ticket_by_case_number"
    ) as mock_resolve:
        mock_resolve.return_value = OmnideskTicket(
            case_id="c_disabled", number=ticket, user_id="u1", status="open"
        )
        deliver_pending_omnidesk_outbox(repo, client)
    assert client.public_messages == []
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status_code, error_message FROM omnidesk_outbox WHERE source_event_id = %s AND action_type = 'public_notification'",
            (event_id,),
        )
        intent = cursor.fetchone()
    assert intent["status_code"] == 2
    assert intent["error_message"] == "public_notification_disabled"


def test_ie01_initial_assignment_evaluation(connection: psycopg.Connection):
    card_repo = PostgresCardRepository(connection)
    ticket = next_ticket()

    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, omnidesk_staff_id) VALUES ('staff_initial', 'x', 'Staff Init', '65432') RETURNING id"
        )
        staff_id = cursor.fetchone()["id"]

        cursor.execute(
            """
            INSERT INTO connection_cards (omnidesk_ticket_number, status_code, criticality_code, urgency_code, planned_start_at, planned_duration_minutes, assignment_method_code, out_of_hours_flag, retroactive_flag, created_source_code)
            VALUES (%s, 0, 0, 0, now(), 60, 0, false, false, 0)
            RETURNING id
            """,
            (ticket,),
        )
        card_id = cursor.fetchone()["id"]

    # Simulate card creation (old_values=None) with initial L1/L2 assignment
    event_id = card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.CREATED,
        actor_user_id=staff_id,
        actor_type=ActorType.SYSTEM,
        old_values=None,
        new_values={
            "l1_owner_id": staff_id,
            "l2_engineer_id": staff_id,
            "planned_start_at": datetime.now(UTC).isoformat(),
        },
        comment="test initial assignment evaluation",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT action_type FROM omnidesk_outbox WHERE source_event_id = %s",
            (event_id,),
        )
        rows = cursor.fetchall()

    actions = {r["action_type"] for r in rows}
    assert "l1_assignment" in actions
    assert "l2_assignment" in actions
    assert len(actions) == 2


def test_ie01_public_notification_revocation_and_timing(
    connection: psycopg.Connection,
):
    card_repo = PostgresCardRepository(connection)
    repo = PostgresOmnideskOutboxRepository(connection)
    ticket = next_ticket()

    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO system_settings (key, value) VALUES ('omnidesk_public_notification_enabled', 'true'), ('omnidesk_public_notification_template', '\"Пользовательский шаблон уведомления.\"') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, omnidesk_staff_id) VALUES ('staff_pub', 'x', 'Staff Pub', '99999') RETURNING id"
        )
        staff_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO connection_cards (omnidesk_ticket_number, status_code, criticality_code, urgency_code, planned_start_at, planned_duration_minutes, assignment_method_code, out_of_hours_flag, retroactive_flag, created_source_code)
            VALUES (%s, 1, 0, 0, now(), 60, 0, false, false, 0)
            RETURNING id
            """,
            (ticket,),
        )
        card_id = cursor.fetchone()["id"]

    planned_start_at = datetime.now(UTC) + timedelta(hours=2)
    # Trigger confirm
    event_id = card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_id,
        actor_type=ActorType.SYSTEM,
        old_values={"status_code": 0},
        new_values={
            "status_code": 1,
            "planned_start_at": planned_start_at.isoformat(),
        },
        comment="test public notification timing",
    )

    # Check timing
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT next_attempt_at, payload FROM omnidesk_outbox WHERE source_event_id = %s AND action_type = 'public_notification'",
            (event_id,),
        )
        row = cursor.fetchone()

    assert row is not None
    assert row["payload"]["content"] == "Пользовательский шаблон уведомления."
    # Verify next_attempt_at is approx planned_start_at - 15 minutes
    expected_time = planned_start_at - timedelta(minutes=15)
    diff = abs((row["next_attempt_at"] - expected_time).total_seconds())
    assert diff < 60

    # Check revocation (when card status changes to not 1)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE connection_cards SET status_code = 4 WHERE id = %s",
            (card_id,),
        )  # REJECTED = 4

    client = FakeOmnideskClient()
    # Mock datetime so it fetches the intent
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE omnidesk_outbox SET next_attempt_at = now() - interval '5 minutes'"
        )

    with patch(
        "app.integrations.omnidesk_outbox.resolve_ticket_by_case_number"
    ) as mock_resolve:
        mock_resolve.return_value = OmnideskTicket(
            case_id="c_pub", number=ticket, user_id="u1", status="open"
        )
        deliver_pending_omnidesk_outbox(repo, client)

    # Notification shouldn't be sent because the card is no longer CONFIRMED (1)
    assert len(client.public_messages) == 0


def test_ie01_reschedule_and_cancellation_revocation(connection: psycopg.Connection):
    from app.cards.repository import PostgresCardRepository
    from app.integrations.omnidesk_outbox import (
        PostgresOmnideskOutboxRepository,
        deliver_pending_omnidesk_outbox,
    )

    card_repo = PostgresCardRepository(connection)
    repo = PostgresOmnideskOutboxRepository(connection)
    ticket = next_ticket()

    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO system_settings (key, value) VALUES ('omnidesk_public_notification_enabled', 'true'), ('omnidesk_public_notification_template', '\"Перенесенное уведомление.\"') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name, omnidesk_staff_id) VALUES ('staff_resched', 'x', 'Staff Resched', '88888') RETURNING id"
        )
        staff_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO connection_cards (omnidesk_ticket_number, status_code, criticality_code, urgency_code, planned_start_at, planned_duration_minutes, assignment_method_code, out_of_hours_flag, retroactive_flag, created_source_code)
            VALUES (%s, 1, 0, 0, now(), 60, 0, false, false, 0)
            RETURNING id
            """,
            (ticket,),
        )
        card_id = cursor.fetchone()["id"]

    t1 = datetime.now(UTC) + timedelta(hours=2)
    t2 = datetime.now(UTC) + timedelta(hours=4)

    # 1. Initial confirmation
    card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_id,
        actor_type=ActorType.SYSTEM,
        old_values={"status_code": 0},
        new_values={"status_code": 1, "planned_start_at": t1.isoformat()},
        comment="confirm",
    )

    # 2. Reschedule while confirmed
    card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_id,
        actor_type=ActorType.SYSTEM,
        old_values={"status_code": 1, "planned_start_at": t1.isoformat()},
        new_values={"status_code": 1, "planned_start_at": t2.isoformat()},
        comment="reschedule",
    )

    # The test records lifecycle events directly; mirror the state transition
    # that CardService persists before writing its event.
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE connection_cards SET status_code = 1, planned_start_at = %s WHERE id = %s",
            (t2, card_id),
        )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, next_attempt_at, status_code FROM omnidesk_outbox WHERE card_id = %s AND action_type = 'public_notification' ORDER BY id",
            (card_id,),
        )
        rows = cursor.fetchall()

    # Keep history, but only the current schedule's intent remains deliverable.
    assert len(rows) == 2
    assert [row["status_code"] for row in rows] == [2, 0]

    # Fast forward time to process them
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE omnidesk_outbox SET next_attempt_at = now() - interval '5 minutes' WHERE status_code = 0"
        )

    client = FakeOmnideskClient()
    with patch(
        "app.integrations.omnidesk_outbox.resolve_ticket_by_case_number"
    ) as mock_resolve:
        mock_resolve.return_value = OmnideskTicket(
            case_id="c_resched", number=ticket, user_id="u1", status="open"
        )
        deliver_pending_omnidesk_outbox(repo, client)

    # Only ONE public message should be sent (the valid t2 one).
    assert len(client.public_messages) == 1

    assert client.public_messages[0][1] == "Перенесенное уведомление."


def test_ie01_cancel_reconfirm_replaces_pending_public_notification(
    connection: psycopg.Connection,
):
    card_repo = PostgresCardRepository(connection)
    ticket = next_ticket()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO system_settings (key, value) VALUES ('omnidesk_public_notification_enabled', 'true'), ('omnidesk_public_notification_template', '\"Персональный текст.\"') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, full_name) VALUES ('staff_reconfirm', 'x', 'Staff Reconfirm') RETURNING id"
        )
        staff_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO connection_cards (omnidesk_ticket_number, status_code, criticality_code, urgency_code, planned_start_at, planned_duration_minutes, assignment_method_code, out_of_hours_flag, retroactive_flag, created_source_code) VALUES (%s, 0, 0, 0, now(), 60, 0, false, false, 0) RETURNING id",
            (ticket,),
        )
        card_id = cursor.fetchone()["id"]

    first_start = datetime.now(UTC) + timedelta(hours=2)
    second_start = datetime.now(UTC) + timedelta(hours=3)
    card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_id,
        actor_type=ActorType.SYSTEM,
        old_values={"status_code": 0},
        new_values={"status_code": 1, "planned_start_at": first_start.isoformat()},
        comment="first confirmation",
    )
    card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_id,
        actor_type=ActorType.SYSTEM,
        old_values={"status_code": 1},
        new_values={"status_code": 4},
        comment="cancel",
    )
    card_repo.add_card_event(
        card_id=card_id,
        event_type=CardEventType.STATUS_CHANGED,
        actor_user_id=staff_id,
        actor_type=ActorType.SYSTEM,
        old_values={"status_code": 4},
        new_values={"status_code": 1, "planned_start_at": second_start.isoformat()},
        comment="reconfirm",
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status_code, payload FROM omnidesk_outbox WHERE card_id = %s AND action_type = 'public_notification' ORDER BY id",
            (card_id,),
        )
        rows = cursor.fetchall()
    assert [row["status_code"] for row in rows] == [2, 0]
    assert rows[1]["payload"]["content"] == "Персональный текст."
    assert rows[1]["payload"]["planned_start_at"] == second_start.isoformat()

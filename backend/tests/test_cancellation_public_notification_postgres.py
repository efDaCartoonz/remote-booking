from __future__ import annotations

import os
from typing import Any
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
import pytest
from fastapi.testclient import TestClient

from app.admin.repository import AdministrativeRepository
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import CardStatus, RoleId
from app.cards.repository import PostgresCardRepository
from app.cards.service import CardService
from app.frame.omnidesk import OmnideskCaseList, OmnideskTicket, OmnideskTicketClient
from app.integrations.omnidesk_outbox import (
    PostgresOmnideskOutboxRepository,
    deliver_pending_omnidesk_outbox,
)
from app.main import app

pytestmark = pytest.mark.skipif(
    not os.getenv("RDM_PG_INTEGRATION"),
    reason="RDM_PG_INTEGRATION environment variable not set",
)


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


class MockOmnideskClient(OmnideskTicketClient):
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.public_messages: list[tuple[str, str, int | None]] = []
        self.internal_notes: list[tuple[str, str, int | None]] = []
        self.assignments: list[tuple[str, int]] = []

    def get_ticket_by_case_id(self, case_id: str) -> OmnideskTicket | None:
        if self.should_fail:
            raise RuntimeError("Omnidesk connection error")
        ticket_number = case_id.removeprefix("case_")
        return OmnideskTicket(
            case_id=case_id,
            number=ticket_number,
            user_id="1",
            status="open",
        )

    def reopen_ticket(self, case_id: str) -> OmnideskTicket:
        return self.get_ticket_by_case_id(case_id)

    def list_cases(self, **kwargs) -> OmnideskCaseList:
        return OmnideskCaseList(items=[], total_count=0)

    def assign_staff(self, case_id: str, staff_id: int) -> None:
        if self.should_fail:
            raise RuntimeError("Omnidesk connection error")
        self.assignments.append((case_id, staff_id))

    def send_public_message(
        self, case_id: str, content: str, staff_id: int | None = None
    ) -> None:
        if self.should_fail:
            raise RuntimeError("Omnidesk connection error")
        self.public_messages.append((case_id, content, staff_id))

    def add_internal_note(
        self, case_id: str, content: str, staff_id: int | None = None
    ) -> None:
        if self.should_fail:
            raise RuntimeError("Omnidesk connection error")
        self.internal_notes.append((case_id, content, staff_id))


def next_unique_str(prefix: str = "user") -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def next_valid_ticket() -> str:
    return f"{uuid4().int % 900 + 100}-{(uuid4().int % 900000) + 100000}"


def create_test_user(cursor: psycopg.Cursor, role_ids: list[int]) -> int:
    username = next_unique_str("user")
    cursor.execute(
        "INSERT INTO users (username, password_hash, full_name, is_active, omnidesk_staff_id) VALUES (%s, 'hash', 'Test User', true, '101') RETURNING id",
        (username,),
    )
    user_id = cursor.fetchone()["id"]
    for role_id in role_ids:
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (%s, %s)",
            (user_id, role_id),
        )
    return user_id


def create_test_card(cursor: psycopg.Cursor, ticket_num: str) -> tuple[int, Any]:
    case_id = f"case_{ticket_num}"
    cursor.execute(
        "INSERT INTO omnidesk_case_index (case_id, case_number, status, omnidesk_created_at, omnidesk_updated_at) VALUES (%s, %s, %s, now(), now()) ON CONFLICT DO NOTHING",
        (case_id, ticket_num, "open"),
    )
    cursor.execute(
        """
        INSERT INTO connection_cards (
            omnidesk_ticket_number, status_code, criticality_code, urgency_code,
            planned_start_at, planned_duration_minutes, assignment_method_code,
            out_of_hours_flag, retroactive_flag, created_source_code
        )
        VALUES (%s, 1, 0, 0, now() + interval '2 hours', 60, 0, false, false, 0)
        RETURNING id, public_id
        """,
        (ticket_num,),
    )
    row = cursor.fetchone()
    return row["id"], row["public_id"]


def test_cancellation_public_notification_settings_repository(
    connection: psycopg.Connection,
) -> None:
    admin_repo = AdministrativeRepository(connection)
    with connection.cursor() as cursor:
        admin_id = create_test_user(cursor, [int(RoleId.ADMIN)])

    # Initial settings
    settings = admin_repo.get_cancellation_public_notification_settings()
    assert settings["enabled"] is True
    assert isinstance(settings["template"], str)
    assert len(settings["template"]) > 0

    # Update settings
    admin_repo.set_cancellation_public_notification_settings(
        enabled=False,
        template="Заказ отменен по просьбе клиента.",
        actor_user_id=admin_id,
    )

    updated = admin_repo.get_cancellation_public_notification_settings()
    assert updated["enabled"] is False
    assert updated["template"] == "Заказ отменен по просьбе клиента."

    # 15-minute public notification setting remains independent
    pub_15min = admin_repo.get_public_notification_settings()
    assert pub_15min["enabled"] is True


def test_card_cancellation_outbox_intent_creation_and_survives_delivery_error(
    connection: psycopg.Connection,
) -> None:
    card_repo = PostgresCardRepository(connection)
    card_service = CardService(repository=card_repo)
    ticket_num = next_valid_ticket()

    with connection.cursor() as cursor:
        actor_id = create_test_user(cursor, [int(RoleId.MANAGER)])
        card_id, public_id = create_test_card(cursor, ticket_num)

    # Cancel card
    cancelled_card = card_service.cancel_card(
        public_id,
        actor_user_id=actor_id,
        comment="Клиент попросил отменить встречу по личным причинам",
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert cancelled_card.status_code == int(CardStatus.CANCELLED)

    # Check omnidesk_outbox intents created
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT action_type, payload, source_event_id FROM omnidesk_outbox WHERE card_id = %s ORDER BY id",
            (card_id,),
        )
        intents = cursor.fetchall()

    action_types = [i["action_type"] for i in intents]
    assert "internal_note" in action_types
    assert "cancellation_public_notification" in action_types

    pub_intent = [
        i for i in intents if i["action_type"] == "cancellation_public_notification"
    ][0]
    # Verify public text contains template text and NO internal reason / secrets
    assert "content" in pub_intent["payload"]
    assert "Клиент попросил отменить" not in pub_intent["payload"]["content"]

    # Test delivery with failing mock: business transaction survives delivery failure
    outbox_repo = PostgresOmnideskOutboxRepository(connection)
    failing_client = MockOmnideskClient(should_fail=True)
    deliver_pending_omnidesk_outbox(outbox_repo, failing_client)

    # Card remains CANCELLED in DB
    rechecked = card_repo.get_card_by_public_id(public_id)
    assert rechecked is not None
    assert rechecked.status_code == int(CardStatus.CANCELLED)


def test_cancellation_public_notification_delivery_respects_toggle(
    connection: psycopg.Connection,
) -> None:
    card_repo = PostgresCardRepository(connection)
    card_service = CardService(repository=card_repo)
    admin_repo = AdministrativeRepository(connection)
    ticket_num = next_valid_ticket()

    with connection.cursor() as cursor:
        actor_id = create_test_user(cursor, [int(RoleId.MANAGER)])
        card_id, public_id = create_test_card(cursor, ticket_num)

    # 1. Enable cancellation notification in settings
    admin_repo.set_cancellation_public_notification_settings(
        enabled=True,
        template="Заявка на удаленное подключение отменена.",
        actor_user_id=actor_id,
    )

    # 2. Cancel card (outbox intent created)
    card_service.cancel_card(
        public_id,
        actor_user_id=actor_id,
        comment="Отмена",
        ip_address=None,
        user_agent=None,
    )

    # 3. Disable setting BEFORE delivery
    admin_repo.set_cancellation_public_notification_settings(
        enabled=False,
        template="Заявка на удаленное подключение отменена.",
        actor_user_id=actor_id,
    )

    # 4. Run outbox worker
    outbox_repo = PostgresOmnideskOutboxRepository(connection)
    mock_client = MockOmnideskClient()
    deliver_pending_omnidesk_outbox(outbox_repo, mock_client)

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT action_type, status_code, error_message FROM omnidesk_outbox WHERE card_id = %s ORDER BY id",
            (card_id,),
        )
        rows = cursor.fetchall()

    cancelled_intents = [
        r for r in rows if r["action_type"] == "cancellation_public_notification"
    ]
    assert len(cancelled_intents) == 1
    assert (
        cancelled_intents[0]["error_message"]
        == "cancellation_public_notification_disabled"
    )

    # Public cancellation message WAS NOT delivered (suppressed because toggle is False at delivery)
    assert len(mock_client.public_messages) == 0

    # Internal note WAS delivered to Omnidesk
    assert len(mock_client.internal_notes) == 1
    assert "Карточка RDM отменена" in mock_client.internal_notes[0][1]


def test_cancellation_public_notification_manager_api_endpoints(
    connection: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    with connection.cursor() as cursor:
        admin_id = create_test_user(cursor, [int(RoleId.ADMIN)])

    user_record = UserAuthRecord(
        id=admin_id,
        username="admin_test",
        password_hash="hash",
        full_name="Admin Test",
        email=None,
        roles=(RoleRecord(id=int(RoleId.ADMIN), name="Administrator"),),
    )

    current_user = [user_record]

    def mock_get_current_user():
        return current_user[0]

    class ConnContext:
        def __enter__(self):
            return connection

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    monkeypatch.setattr("app.api.manager.db_connection", ConnContext)
    app.dependency_overrides[
        __import__(
            "app.auth.dependencies", fromlist=["get_current_user"]
        ).get_current_user
    ] = mock_get_current_user

    try:
        client = TestClient(app)

        # GET settings
        response = client.get(
            "/api/v1/manager/settings/cancellation-public-notification"
        )
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "template" in data

        # PUT settings
        response = client.put(
            "/api/v1/manager/settings/cancellation-public-notification",
            json={"enabled": False, "template": "Сессия отменена."},
        )
        assert response.status_code == 200
        updated = response.json()
        assert updated["enabled"] is False
        assert updated["template"] == "Сессия отменена."

        # GET settings again
        response = client.get(
            "/api/v1/manager/settings/cancellation-public-notification"
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        assert response.json()["template"] == "Сессия отменена."

        blank = client.put(
            "/api/v1/manager/settings/cancellation-public-notification",
            json={"enabled": True, "template": "   "},
        )
        assert blank.status_code == 422

        current_user[0] = UserAuthRecord(
            id=admin_id,
            username="l1_test",
            password_hash="hash",
            full_name="L1 Test",
            email=None,
            roles=(RoleRecord(id=int(RoleId.L1), name="L1"),),
        )
        assert (
            client.get(
                "/api/v1/manager/settings/cancellation-public-notification"
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/api/v1/manager/settings/cancellation-public-notification",
                json={"enabled": True, "template": "Сообщение об отмене"},
            ).status_code
            == 403
        )

    finally:
        app.dependency_overrides.clear()

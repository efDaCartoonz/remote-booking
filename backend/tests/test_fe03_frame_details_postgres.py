from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cards.repository import PostgresCardRepository
from app.frame.omnidesk import OmnideskTicket
from app.frame.schemas import FrameCardCreateRequest
from app.frame.service import FrameService
from app.frame.sessions import CreatedFrameSession, FrameSession


pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


class InMemorySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, FrameSession] = {}
        self.counter = 1

    def create_session(
        self,
        *,
        omnidesk_case_id: str,
        omnidesk_ticket_number: str,
        omnidesk_user_id: str,
        omnidesk_company_id: str | None,
        origin: str | None,
        client_display_name: str | None = None,
        client_company_name: str | None = None,
        client_contact_value: str | None = None,
    ) -> CreatedFrameSession:
        token = f"test-token-{self.counter}"
        self.counter += 1
        now = datetime.now(UTC)
        session = FrameSession(
            omnidesk_case_id=omnidesk_case_id,
            omnidesk_ticket_number=omnidesk_ticket_number,
            omnidesk_user_id=omnidesk_user_id,
            omnidesk_company_id=omnidesk_company_id,
            created_at=now,
            expires_at=now + timedelta(minutes=30),
            origin=origin,
            permissions=("cards:read", "cards:create"),
            client_display_name=client_display_name,
            client_company_name=client_company_name,
            client_contact_value=client_contact_value,
        )
        self.sessions[token] = session
        return CreatedFrameSession(token=token, session=session)

    def get_session(self, token: str) -> FrameSession | None:
        return self.sessions.get(token)


class StubOmnideskClient:
    def __init__(self, tickets: dict[str, OmnideskTicket]) -> None:
        self.tickets = tickets

    def get_ticket_by_case_id(self, case_id: str) -> OmnideskTicket | None:
        return self.tickets.get(case_id)

    def reopen_ticket(self, case_id: str) -> OmnideskTicket:
        ticket = self.tickets[case_id]
        return ticket


def get_pg_connection() -> psycopg.Connection:
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(database_url, row_factory=dict_row)


def test_postgres_card_details_snapshot_and_ownership_isolation() -> None:
    with get_pg_connection() as connection:
        try:
            ticket_suffix = f"{uuid4().int % 1_000_000:06d}"
            ticket_number = f"888-{ticket_suffix}"
            case_id = f"{uuid4().int % 100_000_000:08d}"

            repo = PostgresCardRepository(connection)
            store = InMemorySessionStore()

            # Client 1 in Omnidesk
            ticket_user_1 = f"pg-client-1-{ticket_suffix}"
            ticket_1 = OmnideskTicket(
                case_id=case_id,
                number=ticket_number,
                user_id=ticket_user_1,
                company_id="comp-1",
                client_display_name="Оригинальный Клиент",
                client_company_name="ООО Исходная",
                client_contact_value="client1@example.test",
                status="open",
            )
            omnidesk_client = StubOmnideskClient({case_id: ticket_1})

            frame_service = FrameService(
                repository=repo,
                session_store=store,
                omnidesk_client=omnidesk_client,
            )

            # Create session for Client 1
            created_session_1 = frame_service.create_session(
                omnidesk_case_id=case_id,
                omnidesk_ticket_number=ticket_number,
                origin=None,
            )
            assert (
                created_session_1.session.client_display_name == "Оригинальный Клиент"
            )
            assert created_session_1.session.client_company_name == "ООО Исходная"

            # Client 1 submits card with custom name and company snapshot
            now = datetime.now(UTC)
            card_1 = frame_service.create_card(
                session=created_session_1.session,
                payload=FrameCardCreateRequest(
                    planned_start_at=now + timedelta(hours=3),
                    planned_duration_minutes=60,
                    client_name="Иван Скорректированный",
                    client_company_name="ПАО Новая Фирма",
                    client_contact_type_code=0,
                    client_contact_value="custom1@example.test",
                    description="Тестовая запись PG",
                ),
                ip_address="127.0.0.1",
                user_agent="test-agent",
            )

            # Verify snapshot columns directly in DB
            db_card = connection.execute(
                "SELECT client_name, client_company_name, client_contact_value FROM connection_cards WHERE id = %s",
                (card_1.id,),
            ).fetchone()
            assert db_card["client_name"] == "Иван Скорректированный"
            assert db_card["client_company_name"] == "ПАО Новая Фирма"
            assert db_card["client_contact_value"] == "custom1@example.test"

            # Verify clients row display_name is untouched
            db_client = connection.execute(
                "SELECT display_name, omnidesk_user_id FROM clients WHERE id = %s",
                (card_1.client_id,),
            ).fetchone()
            assert db_client["display_name"] == "Оригинальный Клиент"
            assert db_client["omnidesk_user_id"] == ticket_user_1

            # Client 1 lists cards: sees card_1
            cards_for_1 = frame_service.list_cards(created_session_1.session)
            assert len(cards_for_1) == 1
            assert getattr(cards_for_1, "can_create", None) is False

            # Ticket reassigned in Omnidesk to Client 2
            ticket_user_2 = f"pg-client-2-{ticket_suffix}"
            ticket_2 = OmnideskTicket(
                case_id=case_id,
                number=ticket_number,
                user_id=ticket_user_2,
                company_id="comp-2",
                client_display_name="Второй Клиент",
                client_company_name="ООО Вторая",
                client_contact_value="client2@example.test",
                status="open",
            )
            omnidesk_client.tickets[case_id] = ticket_2

            created_session_2 = frame_service.create_session(
                omnidesk_case_id=case_id,
                omnidesk_ticket_number=ticket_number,
                origin=None,
            )

            # Client 2 lists cards: Client 1's card is HIDDEN, but can_create is False (active card exists)
            cards_for_2 = frame_service.list_cards(created_session_2.session)
            assert len(cards_for_2) == 0
            assert getattr(cards_for_2, "can_create", None) is False

        finally:
            connection.rollback()

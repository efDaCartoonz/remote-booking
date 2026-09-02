from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.api.frame import get_frame_card_repository
from app.cards.constants import ActorType, CreatedSource
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.core.config import settings
from app.frame.omnidesk import (
    OmnideskTicket,
    OmnideskTicketClient,
    OmnideskTicketMismatchError,
    get_omnidesk_ticket_client,
)
from app.frame.sessions import (
    FRAME_TOKEN_HEADER,
    CreatedFrameSession,
    FrameSession,
    FrameSessionStore,
    RedisFrameSessionStore,
    get_frame_session_store,
)
from app.main import create_app
from fastapi.testclient import TestClient
from test_cards import FakeCardRepository


class FakeFrameSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, FrameSession] = {}
        self.next_token = 1

    def create_session(
        self,
        *,
        omnidesk_case_id: str,
        omnidesk_ticket_number: str,
        omnidesk_user_id: str,
        omnidesk_company_id: str | None,
        origin: str | None,
    ) -> CreatedFrameSession:
        token = f"frame-token-{self.next_token}"
        self.next_token += 1
        now = datetime.now(UTC)
        session = FrameSession(
            omnidesk_case_id=omnidesk_case_id,
            omnidesk_ticket_number=omnidesk_ticket_number,
            omnidesk_user_id=omnidesk_user_id,
            omnidesk_company_id=omnidesk_company_id,
            created_at=now,
            expires_at=now + timedelta(minutes=settings.frame_session_ttl_minutes),
            origin=origin,
            permissions=("cards:read", "cards:create"),
        )
        self.sessions[token] = session
        return CreatedFrameSession(token=token, session=session)

    def get_session(self, token: str) -> FrameSession | None:
        return self.sessions.get(token)


class FakeOmnideskTicketClient:
    def __init__(self) -> None:
        self.tickets: dict[str, OmnideskTicket] = {}
        self.get_calls: list[str] = []
        self.reopen_calls: list[str] = []
        self.reopen_status = "open"

    def get_ticket_by_id(
        self, case_id: str, ticket_number: str
    ) -> OmnideskTicket | None:
        self.get_calls.append(f"{case_id}:{ticket_number}")
        ticket = self.tickets.get(ticket_number)
        if ticket is not None and ticket.case_id != case_id:
            raise OmnideskTicketMismatchError
        return self.tickets.get(ticket_number)

    def reopen_ticket(self, case_id: str, ticket_number: str) -> OmnideskTicket:
        self.reopen_calls.append(f"{case_id}:{ticket_number}")
        ticket = self.tickets[ticket_number]
        reopened = replace(ticket, status=self.reopen_status)
        self.tickets[ticket_number] = reopened
        return reopened


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl_seconds

    def get(self, key: str) -> str | None:
        return self.values.get(key)


def make_client(
    *,
    repository: FakeCardRepository,
    session_store: FrameSessionStore,
    omnidesk_client: OmnideskTicketClient,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_frame_card_repository] = lambda: repository
    app.dependency_overrides[get_frame_session_store] = lambda: session_store
    app.dependency_overrides[get_omnidesk_ticket_client] = lambda: omnidesk_client
    return TestClient(app, base_url="https://testserver")


def future_start() -> str:
    return (datetime.now(UTC) + timedelta(hours=3)).isoformat()


def create_frame_session(
    client: TestClient, ticket_number: str = "123-456789", case_id: str = "2000"
) -> str:
    response = client.post(
        "/api/v1/frame/sessions",
        json={
            "omnidesk_case_id": case_id,
            "omnidesk_ticket_number": ticket_number,
        },
        headers={"origin": "https://iridi.omnidesk.ru"},
    )
    assert response.status_code == 201
    return response.json()["token"]


def create_frame_session_without_origin(
    client: TestClient, ticket_number: str = "123-456789", case_id: str = "2000"
) -> str:
    response = client.post(
        "/api/v1/frame/sessions",
        json={
            "omnidesk_case_id": case_id,
            "omnidesk_ticket_number": ticket_number,
        },
    )
    assert response.status_code == 201
    return response.json()["token"]


def seed_ticket(
    omnidesk_client: FakeOmnideskTicketClient,
    *,
    case_id: str = "2000",
    number: str = "123-456789",
    user_id: str | None = "client-1",
    status: str = "open",
    deleted: bool = False,
    spam: bool = False,
) -> None:
    omnidesk_client.tickets[number] = OmnideskTicket(
        number=number,
        case_id=case_id,
        user_id=user_id,
        company_id="company-1",
        client_display_name="Клиент",
        client_contact_value="client@example.test",
        status=status,
        deleted=deleted,
        spam=spam,
    )


def test_redis_frame_session_uses_hashed_key_and_short_ttl() -> None:
    redis_client = FakeRedis()
    store = RedisFrameSessionStore(redis_client)  # type: ignore[arg-type]

    created = store.create_session(
        omnidesk_case_id="2000",
        omnidesk_ticket_number="123-456789",
        omnidesk_user_id="client-1",
        omnidesk_company_id=None,
        origin="https://iridi.omnidesk.ru",
    )

    assert len(redis_client.values) == 1
    stored_key = next(iter(redis_client.ttls))
    assert stored_key.startswith("frame_session:")
    assert created.token not in stored_key
    assert redis_client.ttls[stored_key] == settings.frame_session_ttl_minutes * 60
    assert 15 * 60 <= redis_client.ttls[stored_key] <= 30 * 60
    assert (
        json.loads(redis_client.values[stored_key])["omnidesk_ticket_number"]
        == "123-456789"
    )
    assert json.loads(redis_client.values[stored_key])["omnidesk_case_id"] == "2000"
    stored_session = store.get_session(created.token)
    assert stored_session is not None
    assert stored_session.omnidesk_case_id == "2000"
    assert stored_session.omnidesk_user_id == "client-1"


def test_frame_api_reads_only_current_ticket_with_minimal_card_fields() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client)
    seed_ticket(omnidesk_client, number="555-000001", user_id="client-2")
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    service = CardService(repository)
    service.create_card(
        CardCreateRequest(
            omnidesk_ticket_number="123-456789",
            planned_start_at=datetime.now(UTC) + timedelta(hours=3),
            l2_engineer_id=20,
            description="Текущий тикет",
        ),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    service.create_card(
        CardCreateRequest(
            omnidesk_ticket_number="555-000001",
            planned_start_at=datetime.now(UTC) + timedelta(hours=4),
            description="Другой тикет",
        ),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    token = create_frame_session(client)

    response = client.get("/api/v1/frame/cards", headers={FRAME_TOKEN_HEADER: token})

    assert response.status_code == 200
    body = response.json()
    assert body["omnidesk_ticket_number"] == "123-456789"
    assert body["can_create"] is False
    assert len(body["cards"]) == 1
    card = body["cards"][0]
    assert card["description"] == "Текущий тикет"
    assert card["available_actions"] == ["read"]
    assert "l2_engineer_id" not in card
    assert "created_by_id" not in card
    assert "engineer_report" not in card


def test_frame_api_creates_card_only_for_session_ticket() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client, status="closed")
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session(client)

    response = client.post(
        "/api/v1/frame/cards",
        json={
            "planned_start_at": future_start(),
            "planned_duration_minutes": 60,
            "client_timezone_at_creation": "Asia/Yekaterinburg",
            "client_contact_type_code": 0,
            "client_contact_value": "new-client@example.test",
            "description": "Нужна диагностика доступа",
        },
        headers={FRAME_TOKEN_HEADER: token},
    )

    assert response.status_code == 201
    assert omnidesk_client.reopen_calls == ["2000:123-456789"]
    body = response.json()
    assert body["status"] == "created"
    assert body["client_timezone_at_creation"] == "Asia/Yekaterinburg"
    stored = next(iter(repository.cards.values()))
    assert stored.omnidesk_ticket_number == "123-456789"
    assert stored.created_by_id is None
    assert stored.created_source_code == int(CreatedSource.FRAME)
    assert stored.client_id == 1
    assert repository.events[-1]["actor_type"] == ActorType.FRAME_CLIENT
    assert repository.audit[-1]["actor_type"] == ActorType.FRAME_CLIENT


def test_frame_api_rejects_card_create_when_closed_ticket_is_not_reopened() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    omnidesk_client.reopen_status = "closed"
    seed_ticket(omnidesk_client, status="closed")
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session(client)

    response = client.post(
        "/api/v1/frame/cards",
        json={"planned_start_at": future_start()},
        headers={FRAME_TOKEN_HEADER: token},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "omnidesk_ticket_not_open_after_reopen"
    assert omnidesk_client.reopen_calls == ["2000:123-456789"]
    assert repository.cards == {}


def test_frame_api_rereads_ticket_after_successful_reopen_before_create() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client, status="closed")
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session(client)
    omnidesk_client.get_calls.clear()

    response = client.post(
        "/api/v1/frame/cards",
        json={"planned_start_at": future_start()},
        headers={FRAME_TOKEN_HEADER: token},
    )

    assert response.status_code == 201
    assert omnidesk_client.reopen_calls == ["2000:123-456789"]
    assert omnidesk_client.get_calls == ["2000:123-456789", "2000:123-456789"]
    assert len(repository.cards) == 1


def test_frame_api_accepts_matching_origin_for_existing_frame_session() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client)
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session(client)

    response = client.get(
        "/api/v1/frame/cards",
        headers={
            FRAME_TOKEN_HEADER: token,
            "origin": "https://iridi.omnidesk.ru",
        },
    )

    assert response.status_code == 200


def test_frame_api_accepts_matching_referer_for_existing_frame_session() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client)
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session(client)

    response = client.get(
        "/api/v1/frame/cards",
        headers={
            FRAME_TOKEN_HEADER: token,
            "referer": "https://iridi.omnidesk.ru/l_rus/user/cases/record/123-456789/",
        },
    )

    assert response.status_code == 200


def test_frame_api_rejects_mismatched_origin_for_existing_frame_session() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client)
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session(client)

    response = client.get(
        "/api/v1/frame/cards",
        headers={
            FRAME_TOKEN_HEADER: token,
            "origin": "https://example.invalid",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "frame_session_origin_mismatch"


def test_frame_api_allows_missing_origin_or_referer_for_existing_frame_session() -> (
    None
):
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client)
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session(client)

    response = client.get("/api/v1/frame/cards", headers={FRAME_TOKEN_HEADER: token})

    assert response.status_code == 200


def test_frame_api_allows_session_created_without_origin_or_referer() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client)
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session_without_origin(client)

    response = client.get(
        "/api/v1/frame/cards",
        headers={
            FRAME_TOKEN_HEADER: token,
            "origin": "https://iridi.omnidesk.ru",
        },
    )

    assert response.status_code == 200


def test_frame_api_rejects_create_when_active_card_exists_for_ticket() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client)
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    CardService(repository).create_card(
        CardCreateRequest(
            omnidesk_ticket_number="123-456789",
            planned_start_at=datetime.now(UTC) + timedelta(hours=3),
            description="Уже есть активная",
        ),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    token = create_frame_session(client)

    response = client.post(
        "/api/v1/frame/cards",
        json={"planned_start_at": future_start()},
        headers={FRAME_TOKEN_HEADER: token},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "active_card_exists_for_ticket"


def test_frame_api_rejects_unavailable_or_client_mismatched_ticket() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client, deleted=True)
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )

    unavailable_response = client.post(
        "/api/v1/frame/sessions",
        json={
            "omnidesk_case_id": "2000",
            "omnidesk_ticket_number": "123-456789",
        },
    )
    assert unavailable_response.status_code == 403
    assert unavailable_response.json()["detail"] == "ticket_not_available"

    omnidesk_client.tickets.clear()
    seed_ticket(omnidesk_client, user_id="client-1")
    token = create_frame_session(client)
    seed_ticket(omnidesk_client, user_id="client-2")

    mismatch_response = client.get(
        "/api/v1/frame/cards", headers={FRAME_TOKEN_HEADER: token}
    )
    assert mismatch_response.status_code == 403
    assert mismatch_response.json()["detail"] == "ticket_client_mismatch"


def test_frame_api_rejects_case_id_and_ticket_number_mismatch() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client, case_id="2000")
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )

    response = client.post(
        "/api/v1/frame/sessions",
        json={
            "omnidesk_case_id": "9999",
            "omnidesk_ticket_number": "123-456789",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "omnidesk_ticket_id_number_mismatch"


def test_frame_api_does_not_accept_existing_card_changes_or_internal_fields() -> None:
    repository = FakeCardRepository()
    session_store = FakeFrameSessionStore()
    omnidesk_client = FakeOmnideskTicketClient()
    seed_ticket(omnidesk_client)
    client = make_client(
        repository=repository,
        session_store=session_store,
        omnidesk_client=omnidesk_client,
    )
    token = create_frame_session(client)

    internal_field_response = client.post(
        "/api/v1/frame/cards",
        json={
            "planned_start_at": future_start(),
            "l2_engineer_id": 20,
        },
        headers={FRAME_TOKEN_HEADER: token},
    )
    assert internal_field_response.status_code == 422

    unknown_card_id = "00000000-0000-0000-0000-000000000001"
    cancel_response = client.post(
        f"/api/v1/frame/cards/{unknown_card_id}/cancel",
        json={},
        headers={FRAME_TOKEN_HEADER: token},
    )
    patch_response = client.patch(
        f"/api/v1/frame/cards/{unknown_card_id}",
        json={"planned_duration_minutes": 120},
        headers={FRAME_TOKEN_HEADER: token},
    )

    assert cancel_response.status_code == 404
    assert patch_response.status_code == 404

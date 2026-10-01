from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.cancellation import (
    get_cancellation_card_repository,
    get_cancellation_notification_service,
)
from app.api.frame import (
    get_frame_card_repository,
    get_frame_session_store,
    get_omnidesk_ticket_client,
)
from app.cancellation.service import (
    CancellationConfigurationError,
    derive_cancellation_token,
    get_trusted_cancellation_base_url,
)
from app.cards.constants import (
    ActorType,
    CardStatus,
)
from app.cards.repository import (
    CancellationRecord,
    CardRecord,
    ClientRecord,
    ClientSyncData,
    CreateCardData,
)
from app.core.config import settings
from app.frame.omnidesk import OmnideskTicket
from app.frame.sessions import FRAME_TOKEN_HEADER, CreatedFrameSession, FrameSession
from app.integrations.omnidesk_outbox import (
    OutboxIntent,
    SuppressedIntent,
    _process_intent,
)
from app.main import create_app
from app.notifications import RecordingNotificationService
from test_cards import FakeCardRepository


class EnhancedFakeCardRepository(FakeCardRepository):
    def create_cancellation_token(
        self,
        *,
        token_hash: str,
        card_id: int,
        omnidesk_ticket_number: str,
        omnidesk_user_id: str | None,
        expires_at: datetime,
        nonce: str = "",
        created_at: datetime | None = None,
    ) -> int:
        now = created_at or datetime.now(UTC)
        record = CancellationRecord(
            id=self.next_token_id,
            token_hash=token_hash,
            card_id=card_id,
            omnidesk_ticket_number=omnidesk_ticket_number,
            omnidesk_user_id=omnidesk_user_id,
            action="cancel",
            created_at=now,
            expires_at=expires_at,
            nonce=nonce,
            consumed_at=None,
        )
        self.cancellation_tokens[token_hash] = record
        token_id = self.next_token_id
        self.next_token_id += 1
        return token_id

    def get_cancellation_token_by_id(self, token_id: int) -> CancellationRecord | None:
        for rec in self.cancellation_tokens.values():
            if rec.id == token_id:
                return rec
        return None


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
        client_display_name: str | None = None,
        client_company_name: str | None = None,
        client_contact_value: str | None = None,
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
            expires_at=now + timedelta(minutes=15),
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


class FakeOmnideskTicketClient:
    def __init__(self) -> None:
        self.tickets: dict[str, OmnideskTicket] = {}
        self.sent_public_messages: list[dict[str, str | None]] = []

    def get_ticket_by_case_id(self, case_id: str) -> OmnideskTicket | None:
        return self.tickets.get(case_id)

    def send_public_message(
        self, case_id: str, content: str, staff_id: int | None = None
    ) -> None:
        self.sent_public_messages.append(
            {"case_id": case_id, "content": content, "staff_id": staff_id}
        )


def make_test_client(
    repository: EnhancedFakeCardRepository,
    session_store: FakeFrameSessionStore,
    omnidesk_client: FakeOmnideskTicketClient,
    notifications: RecordingNotificationService | None = None,
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_frame_card_repository] = lambda: repository
    app.dependency_overrides[get_frame_session_store] = lambda: session_store
    app.dependency_overrides[get_omnidesk_ticket_client] = lambda: omnidesk_client
    app.dependency_overrides[get_cancellation_card_repository] = lambda: repository
    app.dependency_overrides[get_cancellation_notification_service] = (
        lambda: notifications
    )
    return TestClient(app, base_url="https://testserver")


def seed_ticket(
    omnidesk_client: FakeOmnideskTicketClient,
    *,
    case_id: str = "2000",
    number: str = "123-456789",
    user_id: str | None = "client-1",
) -> None:
    omnidesk_client.tickets[case_id] = OmnideskTicket(
        number=number,
        case_id=case_id,
        user_id=user_id,
        company_id="company-1",
        client_display_name="Клиент",
        client_contact_value="client@example.test",
        status="open",
        deleted=False,
        spam=False,
    )


def create_frame_session(
    client: TestClient,
    ticket_number: str = "123-456789",
    case_id: str = "2000",
    origin: str = "https://iridi.omnidesk.ru",
) -> str:
    response = client.post(
        "/api/v1/frame/sessions",
        json={
            "case_id": case_id,
            "omnidesk_ticket_number": ticket_number,
        },
        headers={"origin": origin},
    )
    assert response.status_code == 201
    return response.json()["token"]


@pytest.fixture(autouse=True)
def configure_trusted_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://rdm.trusted.example"
    )


def seed_client_and_card(
    repository: EnhancedFakeCardRepository,
    *,
    status: CardStatus = CardStatus.CONFIRMED,
    ticket_number: str = "123-456789",
    user_id: str = "client-1",
    l1_owner_id: int | None = 10,
    l2_engineer_id: int | None = 20,
) -> tuple[ClientRecord, CardRecord]:
    client_rec = repository.get_or_create_client(
        ClientSyncData(omnidesk_user_id=user_id, display_name="Клиент")
    )
    card = repository.create_card(
        CreateCardData(
            omnidesk_ticket_number=ticket_number,
            planned_start_at=datetime.now(UTC) + timedelta(hours=3),
            planned_duration_minutes=60,
            client_id=client_rec.id,
            created_by_id=1,
            status=status,
            l1_owner_id=l1_owner_id,
            l2_engineer_id=l2_engineer_id,
        )
    )
    return client_rec, card


def issue_test_cancellation_token(
    repository: EnhancedFakeCardRepository,
    card: CardRecord,
    *,
    user_id: str = "client-1",
    expires_at: datetime | None = None,
) -> tuple[str, str, int]:
    nonce = secrets.token_urlsafe(16)
    raw_token = derive_cancellation_token(nonce)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    exp = expires_at or (datetime.now(UTC) + timedelta(minutes=5))
    token_id = repository.create_cancellation_token(
        token_hash=token_hash,
        card_id=card.id,
        omnidesk_ticket_number=card.omnidesk_ticket_number,
        omnidesk_user_id=user_id,
        expires_at=exp,
        nonce=nonce,
    )
    return nonce, raw_token, token_id


# =============================================================================
# (1) Base URL Validation & Anti-Tampering Tests
# =============================================================================


def test_base_url_strict_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Missing or empty
    monkeypatch.setattr(settings, "cancellation_public_base_url", "")
    monkeypatch.setattr(settings, "notification_card_base_url", "")
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_not_configured"

    # 2. Non-loopback HTTP rejected
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "http://insecure.example.com"
    )
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_must_be_https"

    # 3. Userinfo in URL rejected
    monkeypatch.setattr(
        settings,
        "cancellation_public_base_url",
        "https://user:pass@trusted.example.com",
    )
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_invalid"

    # 4. Query in URL rejected
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://trusted.example.com?query=1"
    )
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_invalid"

    # 5. Fragment in URL rejected
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://trusted.example.com#fragment"
    )
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_invalid"

    # 6. Unexpected path rejected
    monkeypatch.setattr(
        settings,
        "cancellation_public_base_url",
        "https://trusted.example.com/some/path",
    )
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_invalid"

    # 7. Whitespace rejected
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://trusted.example.com "
    )
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_invalid"

    # 8. Control characters rejected
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://trusted\x01.example.com"
    )
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_invalid"

    # 9. Malformed host rejected
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://-malformed-.com"
    )
    with pytest.raises(CancellationConfigurationError) as exc_info:
        get_trusted_cancellation_base_url()
    assert str(exc_info.value) == "cancellation_public_base_url_invalid"

    # 10. Valid loopback HTTP dev/test allowed
    for loopback in (
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://testserver",
    ):
        monkeypatch.setattr(settings, "cancellation_public_base_url", loopback)
        assert get_trusted_cancellation_base_url() == loopback

    # 11. Valid HTTPS allowed and normalized (trailing slash stripped)
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://rdm.trusted.example/"
    )
    assert get_trusted_cancellation_base_url() == "https://rdm.trusted.example"


def test_frame_request_cancellation_link_ignores_origin_and_referer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://rdm.trusted.example"
    )
    repo = EnhancedFakeCardRepository()
    store = FakeFrameSessionStore()
    omni = FakeOmnideskTicketClient()
    seed_ticket(omni, case_id="2000", number="123-456789", user_id="client-1")
    client_rec, card = seed_client_and_card(
        repo, status=CardStatus.CONFIRMED, ticket_number="123-456789"
    )
    client = make_test_client(repo, store, omni)
    token = create_frame_session(client, "123-456789", "2000")

    # Attacker supplies evil origin and referer header in frame request
    response = client.post(
        f"/api/v1/frame/cards/{card.public_id}/cancellation-link",
        headers={
            FRAME_TOKEN_HEADER: token,
            "origin": "https://iridi.omnidesk.ru",
            "referer": "https://evil-attacker.com/steal",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "link_queued", "expires_in_seconds": 300}
    # Security: raw token and cancel URL are never exposed in response
    assert "token" not in body
    assert "url" not in body

    # Outbox intent has token ID only - no plaintext raw cancellation link in DB outbox!
    assert len(repo.omnidesk_note_intents) == 1
    intent = repo.omnidesk_note_intents[0]
    payload = intent["payload"]
    assert "content" not in payload
    assert "raw_token" not in payload
    assert "token_hash" not in payload
    assert "token_id" in payload
    assert payload == {"token_id": 1}


# =============================================================================
# (2) Recoverable Nonce + HMAC Token Outbox & Worker Tests
# =============================================================================


class FakeOutboxRepo:
    def __init__(
        self,
        card_info: dict | None = None,
        token_info: dict | None = None,
    ) -> None:
        self.card_info = card_info
        self.token_info = token_info
        self.staff_id = None

    def get_user_omnidesk_staff_id(self, user_id: int) -> int | None:
        return self.staff_id

    def resolve_ticket(self, client: Any, ticket_number: str) -> Any:
        return OmnideskTicket(
            number=ticket_number,
            case_id="case-100",
            user_id="user-1",
            company_id="company-1",
            client_display_name="Клиент",
            client_contact_value="client@example.test",
            status="open",
            deleted=False,
            spam=False,
        )

    def get_card_info(self, card_id: int) -> dict | None:
        return self.card_info

    def get_cancellation_token_info(
        self, token_id: int | None = None, token_hash: str | None = None
    ) -> dict | None:
        return self.token_info


def test_outbox_delivers_cancellation_link_by_recovering_token_from_nonce() -> None:
    now = datetime.now(UTC)
    nonce = secrets.token_urlsafe(16)
    expected_token = derive_cancellation_token(nonce)
    token_hash = hashlib.sha256(expected_token.encode("utf-8")).hexdigest()

    card_info = {
        "id": 10,
        "status_code": int(CardStatus.CONFIRMED),
        "omnidesk_ticket_number": "123-456789",
        "client_id": 5,
        "omnidesk_user_id": "user-1",
    }
    token_info = {
        "id": 1,
        "card_id": 10,
        "nonce": nonce,
        "token_hash": token_hash,
        "omnidesk_ticket_number": "123-456789",
        "omnidesk_user_id": "user-1",
        "expires_at": now + timedelta(minutes=4),
        "consumed_at": None,
    }
    outbox_repo = FakeOutboxRepo(card_info=card_info, token_info=token_info)
    omni = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456789",
        action_type="cancellation_link_public_message",
        payload={"token_id": 1},
        attempts=0,
    )

    _process_intent(outbox_repo, omni, intent)

    assert len(omni.sent_public_messages) == 1
    assert omni.sent_public_messages[0]["case_id"] == "case-100"
    content = omni.sent_public_messages[0]["content"]
    assert f"https://rdm.trusted.example/cancel#token={expected_token}" in content


def test_outbox_retries_derive_exact_same_token() -> None:
    now = datetime.now(UTC)
    nonce = secrets.token_urlsafe(16)
    expected_token = derive_cancellation_token(nonce)
    token_hash = hashlib.sha256(expected_token.encode("utf-8")).hexdigest()

    card_info = {
        "id": 10,
        "status_code": int(CardStatus.CONFIRMED),
        "omnidesk_ticket_number": "123-456789",
        "client_id": 5,
        "omnidesk_user_id": "user-1",
    }
    token_info = {
        "id": 1,
        "card_id": 10,
        "nonce": nonce,
        "token_hash": token_hash,
        "omnidesk_ticket_number": "123-456789",
        "omnidesk_user_id": "user-1",
        "expires_at": now + timedelta(minutes=4),
        "consumed_at": None,
    }
    outbox_repo = FakeOutboxRepo(card_info=card_info, token_info=token_info)
    omni = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456789",
        action_type="cancellation_link_public_message",
        payload={"token_id": 1},
        attempts=1,
    )

    _process_intent(outbox_repo, omni, intent)
    content1 = omni.sent_public_messages[0]["content"]

    # Retry again (attempt 2)
    _process_intent(outbox_repo, omni, intent)
    content2 = omni.sent_public_messages[1]["content"]

    assert content1 == content2
    assert f"#token={expected_token}" in content1


def test_outbox_suppresses_expired_cancellation_link_delayed_dispatch_over_5m() -> None:
    now = datetime.now(UTC)
    nonce = secrets.token_urlsafe(16)
    expected_token = derive_cancellation_token(nonce)
    token_hash = hashlib.sha256(expected_token.encode("utf-8")).hexdigest()

    card_info = {
        "id": 10,
        "status_code": int(CardStatus.CONFIRMED),
        "omnidesk_ticket_number": "123-456789",
        "client_id": 5,
        "omnidesk_user_id": "user-1",
    }
    # Delayed dispatch > 5m: token expired 30s ago
    token_info = {
        "id": 1,
        "card_id": 10,
        "nonce": nonce,
        "token_hash": token_hash,
        "omnidesk_ticket_number": "123-456789",
        "omnidesk_user_id": "user-1",
        "expires_at": now - timedelta(seconds=30),
        "consumed_at": None,
    }
    outbox_repo = FakeOutboxRepo(card_info=card_info, token_info=token_info)
    omni = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456789",
        action_type="cancellation_link_public_message",
        payload={"token_id": 1},
        attempts=0,
    )

    with pytest.raises(SuppressedIntent) as exc_info:
        _process_intent(outbox_repo, omni, intent)
    assert str(exc_info.value) == "cancellation_link_expired"
    assert len(omni.sent_public_messages) == 0


def test_outbox_suppresses_already_consumed_token() -> None:
    now = datetime.now(UTC)
    nonce = secrets.token_urlsafe(16)
    token_hash = hashlib.sha256(
        derive_cancellation_token(nonce).encode("utf-8")
    ).hexdigest()

    card_info = {
        "id": 10,
        "status_code": int(CardStatus.CONFIRMED),
        "omnidesk_ticket_number": "123-456789",
        "client_id": 5,
        "omnidesk_user_id": "user-1",
    }
    token_info = {
        "id": 1,
        "card_id": 10,
        "nonce": nonce,
        "token_hash": token_hash,
        "omnidesk_ticket_number": "123-456789",
        "omnidesk_user_id": "user-1",
        "expires_at": now + timedelta(minutes=4),
        "consumed_at": now - timedelta(minutes=1),
    }
    outbox_repo = FakeOutboxRepo(card_info=card_info, token_info=token_info)
    omni = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456789",
        action_type="cancellation_link_public_message",
        payload={"token_id": 1},
        attempts=0,
    )

    with pytest.raises(SuppressedIntent) as exc_info:
        _process_intent(outbox_repo, omni, intent)
    assert str(exc_info.value) == "cancellation_link_already_consumed"
    assert len(omni.sent_public_messages) == 0


# =============================================================================
# (3) Fail Closed Ownership Checks at Issuance, Confirm, Verify, Worker
# =============================================================================


def test_issuance_fails_closed_on_missing_or_mismatched_client() -> None:
    repo = EnhancedFakeCardRepository()
    store = FakeFrameSessionStore()
    omni = FakeOmnideskTicketClient()
    seed_ticket(omni, case_id="2000", number="123-456789", user_id="client-1")
    client = make_test_client(repo, store, omni)
    token = create_frame_session(client, "123-456789", "2000")

    # 1. Card missing client_id
    card_no_client = repo.create_card(
        CreateCardData(
            omnidesk_ticket_number="123-456789",
            planned_start_at=datetime.now(UTC) + timedelta(hours=3),
            planned_duration_minutes=60,
            client_id=None,
            created_by_id=1,
            status=CardStatus.CONFIRMED,
        )
    )
    resp = client.post(
        f"/api/v1/frame/cards/{card_no_client.public_id}/cancellation-link",
        headers={FRAME_TOKEN_HEADER: token, "origin": "https://iridi.omnidesk.ru"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "ticket_client_missing"

    # 2. Card client_id mismatch with session client
    other_client = repo.get_or_create_client(
        ClientSyncData(omnidesk_user_id="other-user", display_name="Другой")
    )
    card_mismatch = repo.create_card(
        CreateCardData(
            omnidesk_ticket_number="123-456789",
            planned_start_at=datetime.now(UTC) + timedelta(hours=3),
            planned_duration_minutes=60,
            client_id=other_client.id,
            created_by_id=1,
            status=CardStatus.CONFIRMED,
        )
    )
    resp_mismatch = client.post(
        f"/api/v1/frame/cards/{card_mismatch.public_id}/cancellation-link",
        headers={FRAME_TOKEN_HEADER: token, "origin": "https://iridi.omnidesk.ru"},
    )
    assert resp_mismatch.status_code == 403
    assert resp_mismatch.json()["detail"] == "ticket_client_mismatch"


def test_verify_fails_closed_and_never_claims_cancellable_on_ownership_mismatch() -> (
    None
):
    repo = EnhancedFakeCardRepository()
    store = FakeFrameSessionStore()
    omni = FakeOmnideskTicketClient()
    client = make_test_client(repo, store, omni)

    # Legitimate client and card
    client_rec, card = seed_client_and_card(repo, status=CardStatus.CONFIRMED)

    # Token issued for a different omnidesk user id
    nonce, raw_token, _ = issue_test_cancellation_token(
        repo, card, user_id="attacker-user"
    )

    resp = client.post(
        "/api/v1/cancellation/verify",
        json={"token": raw_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert body["can_cancel"] is False
    assert body["status"] == "ownership_mismatch"
    assert body["card_public_id"] is None


def test_confirm_fails_closed_on_client_mismatch() -> None:
    repo = EnhancedFakeCardRepository()
    store = FakeFrameSessionStore()
    omni = FakeOmnideskTicketClient()
    client = make_test_client(repo, store, omni)

    client_rec, card = seed_client_and_card(repo, status=CardStatus.CONFIRMED)
    nonce, raw_token, _ = issue_test_cancellation_token(
        repo, card, user_id="attacker-user"
    )

    resp = client.post(
        "/api/v1/cancellation/confirm",
        json={"token": raw_token},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "token_client_mismatch"


def test_worker_fails_closed_on_missing_or_mismatched_ownership() -> None:
    now = datetime.now(UTC)
    nonce = secrets.token_urlsafe(16)
    raw_token = derive_cancellation_token(nonce)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    # 1. Card missing client_id
    card_info_no_client = {
        "id": 10,
        "status_code": int(CardStatus.CONFIRMED),
        "omnidesk_ticket_number": "123-456789",
        "client_id": None,
        "omnidesk_user_id": None,
    }
    token_info = {
        "id": 1,
        "card_id": 10,
        "nonce": nonce,
        "token_hash": token_hash,
        "omnidesk_ticket_number": "123-456789",
        "omnidesk_user_id": "user-1",
        "expires_at": now + timedelta(minutes=4),
        "consumed_at": None,
    }
    outbox_repo = FakeOutboxRepo(card_info=card_info_no_client, token_info=token_info)
    omni = FakeOmnideskTicketClient()
    intent = OutboxIntent(
        id=1,
        card_id=10,
        omnidesk_ticket_number="123-456789",
        action_type="cancellation_link_public_message",
        payload={"token_id": 1},
        attempts=0,
    )

    with pytest.raises(SuppressedIntent) as exc_info:
        _process_intent(outbox_repo, omni, intent)
    assert str(exc_info.value) == "cancellation_link_client_missing"

    # 2. Token client mismatch with card client
    card_info_user2 = {
        "id": 10,
        "status_code": int(CardStatus.CONFIRMED),
        "omnidesk_ticket_number": "123-456789",
        "client_id": 5,
        "omnidesk_user_id": "user-2",
    }
    outbox_repo_mismatch = FakeOutboxRepo(
        card_info=card_info_user2, token_info=token_info
    )
    with pytest.raises(SuppressedIntent) as exc_info:
        _process_intent(outbox_repo_mismatch, omni, intent)
    assert str(exc_info.value) == "cancellation_link_client_mismatch"


# =============================================================================
# (4) Removal of Legacy Token-in-Path Routes and Aliases
# =============================================================================


def test_legacy_token_in_path_routes_removed() -> None:
    repo = EnhancedFakeCardRepository()
    store = FakeFrameSessionStore()
    omni = FakeOmnideskTicketClient()
    client = make_test_client(repo, store, omni)
    client_rec, card = seed_client_and_card(repo, status=CardStatus.CONFIRMED)
    nonce, raw_token, _ = issue_test_cancellation_token(repo, card)

    # 1. GET /api/v1/cancellation/tokens/{token} must return 404
    resp1 = client.get(f"/api/v1/cancellation/tokens/{raw_token}")
    assert resp1.status_code == 404

    # 2. POST /api/v1/cancellation/tokens/{token}/confirm must return 404
    resp2 = client.post(f"/api/v1/cancellation/tokens/{raw_token}/confirm")
    assert resp2.status_code == 404

    # 3. GET /api/v1/cancellation/{token} alias must return 404
    resp3 = client.get(f"/api/v1/cancellation/{raw_token}")
    assert resp3.status_code == 404

    # 4. POST /api/v1/cancellation/{token}/confirm alias must return 404
    resp4 = client.post(f"/api/v1/cancellation/{raw_token}/confirm")
    assert resp4.status_code == 404


# =============================================================================
# (5) Body-based Verification, Confirmation, and Idempotency Guarantees
# =============================================================================


def test_body_based_verification_and_confirmation_flow() -> None:
    repo = EnhancedFakeCardRepository()
    store = FakeFrameSessionStore()
    omni = FakeOmnideskTicketClient()
    notifications = RecordingNotificationService()
    client = make_test_client(repo, store, omni, notifications=notifications)
    client_rec, card = seed_client_and_card(
        repo, status=CardStatus.CONFIRMED, l1_owner_id=10, l2_engineer_id=20
    )
    nonce, raw_token, _ = issue_test_cancellation_token(repo, card)

    # 1. Verify via POST body
    verify_resp = client.post(
        "/api/v1/cancellation/verify",
        json={"token": raw_token},
    )
    assert verify_resp.status_code == 200
    v_body = verify_resp.json()
    assert v_body["valid"] is True
    assert v_body["can_cancel"] is True
    assert v_body["status"] == "cancellable"
    assert v_body["card_public_id"] == str(card.public_id)

    # 2. Confirm via POST body
    confirm_resp = client.post(
        "/api/v1/cancellation/confirm",
        json={"token": raw_token},
    )
    assert confirm_resp.status_code == 200
    c_body = confirm_resp.json()
    assert c_body["status"] == "cancelled"
    assert c_body["card_public_id"] == str(card.public_id)
    assert c_body["idempotent"] is False

    # 3. Side effects
    updated = repo.get_card_by_public_id(card.public_id)
    assert updated.status_code == int(CardStatus.CANCELLED)
    assert repo.events[-1]["actor_type"] == ActorType.FRAME_CLIENT
    assert repo.audit[-1]["actor_type"] == ActorType.FRAME_CLIENT

    # 4. Notifications sent to L1 and L2
    recipients = {n.recipient_user_id for n in notifications.notifications}
    assert 10 in recipients
    assert 20 in recipients


def test_confirm_cancellation_retry_idempotent_no_duplicate_events() -> None:
    repo = EnhancedFakeCardRepository()
    store = FakeFrameSessionStore()
    omni = FakeOmnideskTicketClient()
    notifications = RecordingNotificationService()
    client = make_test_client(repo, store, omni, notifications=notifications)
    client_rec, card = seed_client_and_card(repo, status=CardStatus.CONFIRMED)

    nonce1, raw_token1, _ = issue_test_cancellation_token(repo, card)
    nonce2, raw_token2, _ = issue_test_cancellation_token(repo, card)

    # Confirm using token 1
    resp1 = client.post(
        "/api/v1/cancellation/confirm",
        json={"token": raw_token1},
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "cancelled"
    assert resp1.json()["idempotent"] is False

    event_count_before = len(repo.events)
    notif_count_before = len(notifications.notifications)

    # Retry using same token 1 -> idempotent success
    retry_resp = client.post(
        "/api/v1/cancellation/confirm",
        json={"token": raw_token1},
    )
    assert retry_resp.status_code == 200
    assert retry_resp.json()["status"] == "already_cancelled"
    assert retry_resp.json()["idempotent"] is True

    # No duplicate events or notifications
    assert len(repo.events) == event_count_before
    assert len(notifications.notifications) == notif_count_before

    # Attempting to use unconsumed token 2 reports conflict
    resp2 = client.post(
        "/api/v1/cancellation/confirm",
        json={"token": raw_token2},
    )
    assert resp2.status_code == 409
    assert resp2.json()["detail"] == "card_already_cancelled_by_other"


def test_confirm_cancellation_expired_token_returns_410() -> None:
    repo = EnhancedFakeCardRepository()
    store = FakeFrameSessionStore()
    omni = FakeOmnideskTicketClient()
    client = make_test_client(repo, store, omni)
    client_rec, card = seed_client_and_card(repo, status=CardStatus.CONFIRMED)
    nonce, raw_token, _ = issue_test_cancellation_token(
        repo, card, expires_at=datetime.now(UTC) - timedelta(seconds=10)
    )

    resp = client.post(
        "/api/v1/cancellation/confirm",
        json={"token": raw_token},
    )
    assert resp.status_code == 410
    assert resp.json()["detail"] == "cancellation_token_expired"

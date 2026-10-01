from __future__ import annotations

import concurrent.futures
import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from app.cancellation.service import (
    CancellationAccessError,
    CancellationService,
    derive_cancellation_token,
)
from app.cards.constants import ActorType, CardEventType, CardStatus
from app.cards.repository import PostgresCardRepository
from app.cards.service import CardService, InvalidCardTransitionError
from app.core.config import settings
from app.frame.omnidesk import OmnideskTicket
from app.frame.sessions import FrameSession
from app.integrations.omnidesk_outbox import (
    OutboxIntent,
    PostgresOmnideskOutboxRepository,
    SuppressedIntent,
    _process_intent,
)
from app.notifications import PostgresNotificationService

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture(autouse=True)
def configure_trusted_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings, "cancellation_public_base_url", "https://rdm.trusted.example"
    )


def _get_connection() -> psycopg.Connection:
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(database_url, row_factory=dict_row)


class FakeOmnideskTicketClient:
    def __init__(self) -> None:
        self.sent_public_messages: list[dict[str, str | None]] = []

    def send_public_message(
        self, case_id: str, content: str, staff_id: int | None = None
    ) -> None:
        self.sent_public_messages.append(
            {"case_id": case_id, "content": content, "staff_id": staff_id}
        )

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


def test_client_cancellation_postgres_issuance_and_consumption() -> None:
    with _get_connection() as connection:
        try:
            suffix = uuid4().hex[:8]
            ticket_num = f"888-{uuid4().int % 1_000_000:06d}"

            # Insert client
            client = connection.execute(
                """
                INSERT INTO clients (omnidesk_user_id, display_name)
                VALUES (%s, 'Клиент Тест')
                RETURNING id
                """,
                (f"user-{suffix}",),
            ).fetchone()
            client_id = client["id"]

            # Insert users
            l1_user = connection.execute(
                """
                INSERT INTO users (username, password_hash, full_name)
                VALUES (%s, 'test', 'Специалист Л1')
                RETURNING id
                """,
                (f"l1-{suffix}",),
            ).fetchone()

            l2_user = connection.execute(
                """
                INSERT INTO users (username, password_hash, full_name)
                VALUES (%s, 'test', 'Инженер Л2')
                RETURNING id
                """,
                (f"l2-{suffix}",),
            ).fetchone()

            # Insert connection card in CONFIRMED status
            instant = datetime.now(UTC) + timedelta(hours=3)
            card = connection.execute(
                """
                INSERT INTO connection_cards (
                    omnidesk_ticket_number,
                    client_id,
                    status_code,
                    planned_start_at,
                    planned_duration_minutes,
                    l1_owner_id,
                    l2_engineer_id
                )
                VALUES (%s, %s, %s, %s, 60, %s, %s)
                RETURNING id, public_id, status_code
                """,
                (
                    ticket_num,
                    client_id,
                    int(CardStatus.CONFIRMED),
                    instant,
                    l1_user["id"],
                    l2_user["id"],
                ),
            ).fetchone()
            card_id = card["id"]
            public_id = card["public_id"]

            repo = PostgresCardRepository(connection)
            notif = PostgresNotificationService(connection)
            service = CardService(repo, notif)

            # Issue cancellation token with recoverable nonce + HMAC
            nonce = secrets.token_urlsafe(16)
            raw_token = derive_cancellation_token(nonce)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
            now = datetime.now(UTC)
            expires_at = now + timedelta(minutes=5)

            token_id = repo.create_cancellation_token(
                nonce=nonce,
                token_hash=token_hash,
                card_id=card_id,
                omnidesk_ticket_number=ticket_num,
                omnidesk_user_id=f"user-{suffix}",
                expires_at=expires_at,
                created_at=now,
            )
            assert token_id > 0

            # Retrieve token record from DB
            token_record = repo.get_cancellation_token(token_hash)
            assert token_record is not None
            assert token_record.nonce == nonce
            assert token_record.card_id == card_id
            assert token_record.omnidesk_ticket_number == ticket_num
            assert token_record.consumed_at is None

            # Check active token query (for throttling)
            latest = repo.get_latest_active_cancellation_token(card_id, now=now)
            assert latest is not None
            assert latest.id == token_id
            assert latest.nonce == nonce

            # Consume token
            consumed = repo.consume_cancellation_token(
                token_id=token_id,
                consumed_at=now,
                consumed_by_ip="127.0.0.1",
                consumed_by_user_agent="TestAgent/1.0",
            )
            assert consumed is True

            # Double consume returns False (atomic guard)
            double_consumed = repo.consume_cancellation_token(
                token_id=token_id,
                consumed_at=now,
            )
            assert double_consumed is False

            # Cancel card as client
            cancelled_card = service.cancel_card_by_client(
                public_id,
                comment="client_self_cancellation",
                ip_address="127.0.0.1",
                user_agent="TestAgent/1.0",
            )
            assert cancelled_card.status_code == int(CardStatus.CANCELLED)

            # Verify card in DB
            db_card = connection.execute(
                "SELECT status_code FROM connection_cards WHERE id = %s", (card_id,)
            ).fetchone()
            assert db_card["status_code"] == int(CardStatus.CANCELLED)

            # Verify card_events
            event = connection.execute(
                """
                SELECT event_type_code, actor_type_code, comment
                FROM card_events
                WHERE card_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (card_id,),
            ).fetchone()
            assert event["event_type_code"] == int(CardEventType.STATUS_CHANGED)
            assert event["actor_type_code"] == int(ActorType.FRAME_CLIENT)
            assert event["comment"] == "client_self_cancellation"

            # Verify audit_log
            audit = connection.execute(
                """
                SELECT entity_type, entity_id, action_code, actor_type_code, ip_address::text AS ip
                FROM audit_log
                WHERE entity_type = 'connection_card' AND entity_id = %s
                ORDER BY id DESC
                LIMIT 1
                """,
                (card_id,),
            ).fetchone()
            assert audit["actor_type_code"] == int(ActorType.FRAME_CLIENT)
            assert audit["ip"] in ("127.0.0.1", "127.0.0.1/32")

        finally:
            connection.rollback()


def test_client_cancellation_postgres_disallowed_status_rejected() -> None:
    with _get_connection() as connection:
        try:
            ticket_num = f"888-{uuid4().int % 1_000_000:06d}"
            instant = datetime.now(UTC) + timedelta(hours=3)

            # Insert card in IN_PROGRESS status (3)
            card = connection.execute(
                """
                INSERT INTO connection_cards (
                    omnidesk_ticket_number,
                    status_code,
                    planned_start_at,
                    planned_duration_minutes
                )
                VALUES (%s, %s, %s, 60)
                RETURNING id, public_id
                """,
                (ticket_num, int(CardStatus.IN_PROGRESS), instant),
            ).fetchone()

            repo = PostgresCardRepository(connection)
            service = CardService(repo)

            with pytest.raises(InvalidCardTransitionError):
                service.cancel_card_by_client(card["public_id"])

        finally:
            connection.rollback()


def test_client_cancellation_postgres_service_flow_and_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _get_connection() as connection:
        try:
            suffix = uuid4().hex[:8]
            ticket_num = f"888-{uuid4().int % 1_000_000:06d}"

            # Insert client
            client = connection.execute(
                """
                INSERT INTO clients (omnidesk_user_id, display_name)
                VALUES (%s, 'Клиент 1')
                RETURNING id
                """,
                (f"user-{suffix}",),
            ).fetchone()
            client_id = client["id"]

            instant = datetime.now(UTC) + timedelta(hours=3)
            card = connection.execute(
                """
                INSERT INTO connection_cards (
                    omnidesk_ticket_number,
                    client_id,
                    status_code,
                    planned_start_at,
                    planned_duration_minutes
                )
                VALUES (%s, %s, %s, %s, 60)
                RETURNING id, public_id
                """,
                (ticket_num, client_id, int(CardStatus.CONFIRMED), instant),
            ).fetchone()
            card_id = card["id"]
            public_id = card["public_id"]

            repo = PostgresCardRepository(connection)
            service = CancellationService(repo)

            # 1. Mismatched session client ID fails closed
            fake_session_attacker = FrameSession(
                omnidesk_case_id="100",
                omnidesk_ticket_number=ticket_num,
                omnidesk_user_id="evil-attacker",
                omnidesk_company_id=None,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                origin="https://iridi.omnidesk.ru",
                permissions=("cards:read", "cards:create"),
            )
            with pytest.raises(CancellationAccessError):
                service.request_cancellation_link(
                    session=fake_session_attacker,
                    card_id=public_id,
                )

            # 2. Correct session issues link
            fake_session_legit = FrameSession(
                omnidesk_case_id="100",
                omnidesk_ticket_number=ticket_num,
                omnidesk_user_id=f"user-{suffix}",
                omnidesk_company_id=None,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
                origin="https://iridi.omnidesk.ru",
                permissions=("cards:read", "cards:create"),
            )
            link_resp = service.request_cancellation_link(
                session=fake_session_legit,
                card_id=public_id,
            )
            assert link_resp.status == "link_queued"

            # Check outbox intent in postgres: token_id only, no plaintext token at rest
            outbox_intent = connection.execute(
                """
                SELECT id, action_type, payload
                FROM omnidesk_outbox
                WHERE card_id = %s AND action_type = 'cancellation_link_public_message'
                """,
                (card_id,),
            ).fetchone()
            assert outbox_intent is not None
            payload = outbox_intent["payload"]
            assert "token_id" in payload
            assert "content" not in payload
            assert "raw_token" not in payload
            assert "token_hash" not in payload

            # Verify token record in database has nonce and token_hash
            db_token = connection.execute(
                "SELECT nonce, token_hash FROM client_cancellation_tokens WHERE id = %s",
                (payload["token_id"],),
            ).fetchone()
            assert db_token is not None
            assert len(db_token["nonce"]) > 0

            # Derive raw token from stored nonce and verify via service
            raw_token = derive_cancellation_token(db_token["nonce"])
            v_res = service.verify_token(raw_token)
            assert v_res.valid is True
            assert v_res.can_cancel is True
            assert v_res.status == "cancellable"

            # Confirm cancellation
            c_res = service.confirm_cancellation(raw_token, ip_address="127.0.0.1")
            assert c_res.status == "cancelled"
            assert c_res.idempotent is False

            # Retry same token: idempotent success
            c_retry = service.confirm_cancellation(raw_token, ip_address="127.0.0.1")
            assert c_retry.status == "already_cancelled"
            assert c_retry.idempotent is True

        finally:
            connection.rollback()


def test_client_cancellation_postgres_worker_delivery_and_expiry() -> None:
    with _get_connection() as connection:
        try:
            suffix = uuid4().hex[:8]
            ticket_num = f"888-{uuid4().int % 1_000_000:06d}"

            # Insert client
            client = connection.execute(
                """
                INSERT INTO clients (omnidesk_user_id, display_name)
                VALUES (%s, 'Клиент 1')
                RETURNING id
                """,
                (f"user-{suffix}",),
            ).fetchone()
            client_id = client["id"]

            instant = datetime.now(UTC) + timedelta(hours=3)
            card = connection.execute(
                """
                INSERT INTO connection_cards (
                    omnidesk_ticket_number,
                    client_id,
                    status_code,
                    planned_start_at,
                    planned_duration_minutes
                )
                VALUES (%s, %s, %s, %s, 60)
                RETURNING id, public_id
                """,
                (ticket_num, client_id, int(CardStatus.CONFIRMED), instant),
            ).fetchone()
            card_id = card["id"]

            now = datetime.now(UTC)
            nonce = secrets.token_urlsafe(16)
            raw_token = derive_cancellation_token(nonce)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

            token = connection.execute(
                """
                INSERT INTO client_cancellation_tokens (
                    nonce, token_hash, card_id, omnidesk_ticket_number, omnidesk_user_id,
                    action, expires_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, 'cancel', %s, %s)
                RETURNING id
                """,
                (
                    nonce,
                    token_hash,
                    card_id,
                    ticket_num,
                    f"user-{suffix}",
                    now + timedelta(minutes=5),
                    now,
                ),
            ).fetchone()
            token_id = token["id"]

            # Outbox intent
            outbox_repo = PostgresOmnideskOutboxRepository(connection)
            # Custom resolve_ticket returning matching user
            outbox_repo.resolve_ticket = lambda cl, t_num: OmnideskTicket(
                number=ticket_num,
                case_id="case-200",
                user_id=f"user-{suffix}",
                company_id="company-1",
                client_display_name="Клиент",
                client_contact_value="client@example.test",
                status="open",
                deleted=False,
                spam=False,
            )

            omni = FakeOmnideskTicketClient()
            intent = OutboxIntent(
                id=1,
                card_id=card_id,
                omnidesk_ticket_number=ticket_num,
                action_type="cancellation_link_public_message",
                payload={"token_id": token_id},
                attempts=0,
            )

            # Process intent: worker derives token and sends public message in memory
            _process_intent(outbox_repo, omni, intent)
            assert len(omni.sent_public_messages) == 1
            sent = omni.sent_public_messages[0]
            assert sent["case_id"] == "case-200"
            assert (
                f"https://rdm.trusted.example/cancel#token={raw_token}"
                in sent["content"]
            )

            # Delayed dispatch > 5m: expire token in DB
            connection.execute(
                "UPDATE client_cancellation_tokens SET expires_at = %s WHERE id = %s",
                (now - timedelta(seconds=10), token_id),
            )
            with pytest.raises(SuppressedIntent) as exc_info:
                _process_intent(outbox_repo, omni, intent)
            assert str(exc_info.value) == "cancellation_link_expired"

        finally:
            connection.rollback()


def test_client_cancellation_postgres_concurrent_confirms() -> None:
    # Test concurrency and idempotency using two database connections
    conn1 = _get_connection()
    conn2 = _get_connection()
    try:
        suffix = uuid4().hex[:8]
        ticket_num = f"888-{uuid4().int % 1_000_000:06d}"

        # Insert client and card in conn1 and commit so conn2 sees it
        with conn1.transaction():
            client = conn1.execute(
                """
                INSERT INTO clients (omnidesk_user_id, display_name)
                VALUES (%s, 'Клиент Конкурентный')
                RETURNING id
                """,
                (f"user-{suffix}",),
            ).fetchone()
            client_id = client["id"]

            instant = datetime.now(UTC) + timedelta(hours=3)
            card = conn1.execute(
                """
                INSERT INTO connection_cards (
                    omnidesk_ticket_number,
                    client_id,
                    status_code,
                    planned_start_at,
                    planned_duration_minutes
                )
                VALUES (%s, %s, %s, %s, 60)
                RETURNING id, public_id
                """,
                (ticket_num, client_id, int(CardStatus.CONFIRMED), instant),
            ).fetchone()
            card_id = card["id"]

            now = datetime.now(UTC)
            nonce = secrets.token_urlsafe(16)
            raw_token = derive_cancellation_token(nonce)
            token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

            conn1.execute(
                """
                INSERT INTO client_cancellation_tokens (
                    nonce, token_hash, card_id, omnidesk_ticket_number, omnidesk_user_id,
                    action, expires_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, 'cancel', %s, %s)
                RETURNING id
                """,
                (
                    nonce,
                    token_hash,
                    card_id,
                    ticket_num,
                    f"user-{suffix}",
                    now + timedelta(minutes=5),
                    now,
                ),
            ).fetchone()

        # Run two concurrent confirm calls across separate connections/transactions
        def run_confirm(conn: psycopg.Connection) -> dict:
            repo = PostgresCardRepository(conn)
            service = CancellationService(repo)
            with conn.transaction():
                res = service.confirm_cancellation(raw_token, ip_address="127.0.0.1")
                return {"status": res.status, "idempotent": res.idempotent}

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future1 = executor.submit(run_confirm, conn1)
            future2 = executor.submit(run_confirm, conn2)
            res1 = future1.result()
            res2 = future2.result()

        results = [res1, res2]
        statuses = {r["status"] for r in results}
        idempotents = [r["idempotent"] for r in results]

        # One should be initial cancel (cancelled, False), the other idempotent (already_cancelled, True)
        assert statuses == {"cancelled", "already_cancelled"}
        assert sorted(idempotents) == [False, True]

        # Verify exactly one cancellation status change event in DB
        with conn1.cursor() as cur:
            cur.execute(
                "SELECT count(*) as cnt FROM card_events WHERE card_id = %s AND event_type_code = %s",
                (card_id, int(CardEventType.STATUS_CHANGED)),
            )
            count = cur.fetchone()["cnt"]
            assert count == 1

            # Card is in CANCELLED status
            cur.execute(
                "SELECT status_code FROM connection_cards WHERE id = %s", (card_id,)
            )
            status_code = cur.fetchone()["status_code"]
            assert status_code == int(CardStatus.CANCELLED)

    finally:
        # Cleanup
        try:
            with conn1.transaction():
                conn1.execute("DELETE FROM connection_cards WHERE id = %s", (card_id,))
                conn1.execute("DELETE FROM clients WHERE id = %s", (client_id,))
        except Exception:
            pass
        conn1.close()
        conn2.close()

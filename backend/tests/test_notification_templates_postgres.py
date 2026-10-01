from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import RoleId
from app.db import get_db
from app.main import create_app
from app.notifications import (
    NOTIFICATION_CHANNEL_CODES,
    NOTIFICATION_EVENT_CODES,
    PostgresNotificationRuntimeRepository,
    deliver_pending_notifications,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


@pytest.fixture
def database_url() -> str:
    return os.getenv("PSYCOPG_DATABASE_URL") or os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


@pytest.fixture(autouse=True)
def clean_database(database_url: str):
    yield
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM notifications WHERE dedupe_key LIKE 'test_tpl_ntf:%'"
        )
        cursor.execute("DELETE FROM connection_cards WHERE number LIKE 'RDM-TPL-%'")
        cursor.execute("DELETE FROM user_settings WHERE user_id IN (95001, 95002)")
        cursor.execute("DELETE FROM users WHERE id IN (95001, 95002)")
        cursor.execute(
            "DELETE FROM audit_log WHERE entity_type IN ('notification_template', 'notification') AND entity_id IN (SELECT id FROM notification_templates WHERE code LIKE 'test_%')"
        )
        cursor.execute("DELETE FROM notification_templates WHERE code LIKE 'test_%'")


def seed_test_users(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO users (id, username, password_hash, full_name)
            VALUES
                (95001, 'tpl-admin', 'hash', 'Template Admin'),
                (95002, 'tpl-recipient', 'hash', 'Template Recipient')
            ON CONFLICT (id) DO NOTHING
            """
        )
        cursor.execute(
            """
            INSERT INTO user_settings (user_id, telegram_chat_id, bitrix24_user_id, timezone, notify_telegram, notify_bitrix24)
            VALUES (95002, 'tg_tpl_95002', 'bx_tpl_95002', 'UTC', true, true)
            ON CONFLICT (user_id) DO UPDATE SET
                telegram_chat_id = EXCLUDED.telegram_chat_id,
                bitrix24_user_id = EXCLUDED.bitrix24_user_id
            """
        )


def make_auth_admin(user_id: int = 95001) -> UserAuthRecord:
    return UserAuthRecord(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="unused",
        full_name=f"Admin {user_id}",
        email=None,
        roles=(RoleRecord(id=int(RoleId.ADMIN), name=RoleId.ADMIN.name),),
    )


class RecordingStubAdapter:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, str, str]] = []

    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        self.sent_messages.append((recipient, text, idempotency_key))


def test_seeded_notification_templates_count_and_codes_postgres(
    database_url: str,
) -> None:
    expected_codes = {
        f"{event}.{channel}"
        for event in NOTIFICATION_EVENT_CODES
        for channel in NOTIFICATION_CHANNEL_CODES
    }
    assert len(expected_codes) == 16

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT code, channel_code, visible, subject_template, body_template
                FROM notification_templates
                """
            )
            rows = cursor.fetchall()
            db_codes = {r["code"] for r in rows}

            # All 16 seeded codes must be present in database after migration
            for code in expected_codes:
                assert code in db_codes, f"Missing seeded template code: {code}"

            for r in rows:
                if r["code"] in expected_codes:
                    assert r["visible"] is True
                    assert r["subject_template"] is None
                    assert "{card_number}" in r["body_template"]
                    assert "{url}" in r["body_template"]


def test_admin_template_update_applies_to_runtime_delivery_postgres(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.notifications.settings.notification_card_base_url",
        "https://rdm.example.test",
    )

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_test_users(connection)
        app = create_app()
        app.dependency_overrides[get_db] = lambda: connection

        admin = make_auth_admin(95001)
        app.dependency_overrides[get_current_user] = lambda: admin
        client = TestClient(app)

        target_code = "card_cancelled.telegram"

        # 1. Update template via admin API
        custom_body = "ВНИМАНИЕ! Карточка {card_number}{client_suffix} отменена! Тикет: {ticket}. Ссылка: {url}"
        resp = client.put(
            f"/api/v1/admin/notification-templates/{target_code}",
            json={
                "subject_template": None,
                "body_template": custom_body,
                "visible": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["body_template"] == custom_body

        # 2. Insert card and pending notification intent
        card_public_id = str(uuid4())
        card_number = "RDM-TPL-001"
        ticket_number = "555-000001"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO connection_cards (
                    public_id, number, omnidesk_ticket_number, status_code,
                    planned_start_at, planned_duration_minutes
                )
                VALUES (%s, %s, %s, 5, now(), 30)
                RETURNING id
                """,
                (card_public_id, card_number, ticket_number),
            )
            card_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO card_events (card_id, event_type_code, actor_type_code) "
                "VALUES (%s, 1, 2) RETURNING id",
                (card_id,),
            )
            source_event_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                INSERT INTO notifications (
                    card_id, recipient_user_id, channel_code, event_type_code,
                    source_event_id, source_event_type_code, payload, dedupe_key
                )
                VALUES (%s, 95002, 0, %s, %s, 1, '{}'::jsonb, 'test_tpl_ntf:1')
                RETURNING id
                """,
                (card_id, NOTIFICATION_EVENT_CODES["card_cancelled"], source_event_id),
            )
        connection.commit()

        # 3. Deliver pending notifications with Postgres runtime repo
        repo = PostgresNotificationRuntimeRepository(connection)
        tg_adapter = RecordingStubAdapter()
        adapters = {NOTIFICATION_CHANNEL_CODES["telegram"]: tg_adapter}

        delivered = deliver_pending_notifications(repo, adapters, limit=10)
        assert delivered == 1
        assert len(tg_adapter.sent_messages) == 1

        recipient, delivered_text, _ = tg_adapter.sent_messages[0]
        assert recipient == "tg_tpl_95002"
        assert delivered_text.startswith(
            "ВНИМАНИЕ! Карточка RDM-TPL-001 отменена! Тикет: 555-000001."
        )
        assert f"https://rdm.example.test/cards/{card_public_id}" in delivered_text


def test_corrupted_or_hidden_template_falls_back_safely_postgres(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.notifications.settings.notification_card_base_url",
        "https://rdm.example.test",
    )

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_test_users(connection)
        target_code = "l1_followup.telegram"

        # Hide template directly in DB
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE notification_templates
                SET visible = false
                WHERE code = %s
                """,
                (target_code,),
            )

            # Insert card and notification
            card_public_id = str(uuid4())
            cursor.execute(
                """
                INSERT INTO connection_cards (
                    public_id, number, omnidesk_ticket_number, status_code,
                    planned_start_at, planned_duration_minutes
                )
                VALUES (%s, 'RDM-TPL-002', '555-000002', 1, now(), 60)
                RETURNING id
                """,
                (card_public_id,),
            )
            card_id = cursor.fetchone()["id"]

            cursor.execute(
                "INSERT INTO card_events (card_id, event_type_code, actor_type_code) "
                "VALUES (%s, 1, 2) RETURNING id",
                (card_id,),
            )
            source_event_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                INSERT INTO notifications (
                    card_id, recipient_user_id, channel_code, event_type_code,
                    source_event_id, source_event_type_code, payload, dedupe_key
                )
                VALUES (%s, 95002, 0, %s, %s, 1, '{}'::jsonb, 'test_tpl_ntf:2')
                """,
                (card_id, NOTIFICATION_EVENT_CODES["l1_followup"], source_event_id),
            )
        connection.commit()

        # Deliver
        repo = PostgresNotificationRuntimeRepository(connection)
        tg_adapter = RecordingStubAdapter()
        adapters = {NOTIFICATION_CHANNEL_CODES["telegram"]: tg_adapter}

        delivered = deliver_pending_notifications(repo, adapters, limit=10)
        assert delivered == 1
        assert len(tg_adapter.sent_messages) == 1

        _, delivered_text, _ = tg_adapter.sent_messages[0]
        # Should fall back to default template
        assert delivered_text.startswith("Карточка RDM-TPL-002; тикет 555-000002;")

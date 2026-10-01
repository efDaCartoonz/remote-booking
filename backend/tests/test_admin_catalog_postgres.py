from __future__ import annotations

import os

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import RoleId
from app.db import get_db
from app.main import create_app

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
            "DELETE FROM audit_log WHERE entity_type IN ('connection_result', 'notification_template', 'system_setting')"
        )
        cursor.execute("DELETE FROM notification_templates WHERE code LIKE 'test_%'")
        cursor.execute("DELETE FROM connection_results WHERE code >= 900")
        cursor.execute("DELETE FROM users WHERE id IN (94001, 94002, 94003)")


def seed_users(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        for user_id, username, full_name in (
            (94001, "adm-catalog-admin", "Admin User"),
            (94002, "adm-catalog-mgr", "Manager User"),
            (94003, "adm-catalog-l2", "L2 Engineer"),
        ):
            cursor.execute(
                "INSERT INTO users (id, username, password_hash, full_name) VALUES (%s, %s, 'hash', %s) ON CONFLICT (id) DO NOTHING",
                (user_id, username, full_name),
            )


def make_auth_user(user_id: int, role_id: RoleId) -> UserAuthRecord:
    return UserAuthRecord(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="unused",
        full_name=f"User {user_id}",
        email=None,
        roles=(RoleRecord(id=int(role_id), name=role_id.name),),
    )


def test_results_crud_audit_and_last_active_constraint_postgres(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection

        admin = make_auth_user(94001, RoleId.ADMIN)
        manager = make_auth_user(94002, RoleId.MANAGER)

        # 1. Manager is forbidden (403)
        app.dependency_overrides[get_current_user] = lambda: manager
        client = TestClient(app)
        resp = client.get("/api/v1/admin/results")
        assert resp.status_code == 403

        # 2. Admin creates results
        app.dependency_overrides[get_current_user] = lambda: admin
        client = TestClient(app)

        # Create two test results
        resp1 = client.post(
            "/api/v1/admin/results",
            json={
                "code": 901,
                "name": "Результат 901",
                "is_active": True,
                "sort_order": 50,
            },
        )
        assert resp1.status_code == 201
        assert resp1.json()["code"] == 901

        resp2 = client.post(
            "/api/v1/admin/results",
            json={
                "code": 902,
                "name": "Результат 902",
                "is_active": True,
                "sort_order": 20,
            },
        )
        assert resp2.status_code == 201

        # Duplicate code conflict (409)
        resp_dup = client.post(
            "/api/v1/admin/results",
            json={"code": 901, "name": "Дубликат", "is_active": True, "sort_order": 10},
        )
        assert resp_dup.status_code == 409

        # List all results - check sorting
        resp_list = client.get("/api/v1/admin/results")
        assert resp_list.status_code == 200
        items = resp_list.json()
        codes = [r["code"] for r in items]
        assert 901 in codes and 902 in codes
        # 902 has sort_order 20, 901 has 50, so 902 appears before 901
        idx_902 = codes.index(902)
        idx_901 = codes.index(901)
        assert idx_902 < idx_901

        # Update 901
        resp_update = client.put(
            "/api/v1/admin/results/901",
            json={
                "name": "Обновленный результат 901",
                "is_active": False,
                "sort_order": 55,
            },
        )
        assert resp_update.status_code == 200
        assert resp_update.json()["is_active"] is False

        # Verify audit log in DB
        cursor = connection.cursor()
        cursor.execute(
            "SELECT action_code, entity_id, new_values FROM audit_log WHERE entity_type='connection_result' AND entity_id IN (901, 902) ORDER BY id"
        )
        audit_rows = cursor.fetchall()
        assert len(audit_rows) >= 3  # CREATE 901, CREATE 902, UPDATE 901


def test_last_active_result_deactivation_conflict_postgres(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection
        admin = make_auth_user(94001, RoleId.ADMIN)
        app.dependency_overrides[get_current_user] = lambda: admin
        client = TestClient(app)

        # Deactivate all active results except code 905
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE connection_results SET is_active = false WHERE code != 905"
        )
        cursor.execute(
            "INSERT INTO connection_results (code, name, is_active, sort_order) VALUES (905, 'Последний активный', true, 10) ON CONFLICT (code) DO UPDATE SET is_active=true"
        )
        connection.commit()

        # Attempt to deactivate 905 (the only active result) -> 409
        resp = client.put(
            "/api/v1/admin/results/905",
            json={"name": "Последний", "is_active": False, "sort_order": 10},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "cannot_deactivate_last_active_result"


def test_notification_templates_crud_validation_and_audit_postgres(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection

        admin = make_auth_user(94001, RoleId.ADMIN)
        l2 = make_auth_user(94003, RoleId.L2)

        # 1. Non-admin is forbidden (403)
        app.dependency_overrides[get_current_user] = lambda: l2
        client = TestClient(app)
        assert client.get("/api/v1/admin/notification-templates").status_code == 403

        # 2. Seed test template in database
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO notification_templates (code, channel_code, visible, subject_template, body_template)
            VALUES ('test_l1_followup', 0, true, 'Тема {card_number}', 'Тело {card_number} {ticket}')
            ON CONFLICT (code) DO UPDATE SET body_template = EXCLUDED.body_template
            RETURNING id
            """
        )
        template_id = cursor.fetchone()["id"]
        connection.commit()

        # 3. Admin reads template
        app.dependency_overrides[get_current_user] = lambda: admin
        client = TestClient(app)
        resp_list = client.get("/api/v1/admin/notification-templates")
        assert resp_list.status_code == 200
        codes = [t["code"] for t in resp_list.json()]
        assert "test_l1_followup" in codes

        # 4. Admin updates template with valid placeholders
        resp_up = client.put(
            "/api/v1/admin/notification-templates/test_l1_followup",
            json={
                "subject_template": "Заявка {card_number}",
                "body_template": "Карточка {card_number}{client_suffix}; начало {timestamp}; длительность {duration} мин. Ссылка: {url}",
                "visible": False,
            },
        )
        assert resp_up.status_code == 200
        assert resp_up.json()["visible"] is False
        assert resp_up.json()["subject_template"] == "Заявка {card_number}"

        # 5. Admin updates template with unknown placeholder -> 422
        resp_invalid = client.put(
            "/api/v1/admin/notification-templates/test_l1_followup",
            json={
                "subject_template": "Заявка {card_number}",
                "body_template": "Текст {unknown_placeholder_variable}",
                "visible": True,
            },
        )
        assert resp_invalid.status_code == 422

        # 6. Admin updates non-existent template -> 404
        resp_404 = client.put(
            "/api/v1/admin/notification-templates/test_non_existent",
            json={
                "body_template": "Текст {card_number}",
            },
        )
        assert resp_404.status_code == 404

        # 7. Verify audit log entry
        cursor.execute(
            "SELECT action_code, entity_id, old_values, new_values FROM audit_log WHERE entity_type='notification_template' AND entity_id=%s",
            (template_id,),
        )
        audit_row = cursor.fetchone()
        assert audit_row is not None
        assert audit_row["new_values"]["visible"] is False


def test_integrations_status_postgres(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection

        admin = make_auth_user(94001, RoleId.ADMIN)
        app.dependency_overrides[get_current_user] = lambda: admin
        client = TestClient(app)

        resp = client.get("/api/v1/admin/integrations/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "omnidesk" in data
        assert "telegram" in data
        assert "bitrix24" in data
        assert isinstance(data["omnidesk"]["configured"], bool)
        assert isinstance(data["telegram"]["configured"], bool)
        assert isinstance(data["bitrix24"]["configured"], bool)

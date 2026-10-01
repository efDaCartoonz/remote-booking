from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.admin.users import PostgresAdminUserRepository
from app.api.admin_users import get_admin_user_repository, router as admin_users_router
from app.auth.dependencies import get_current_user
from app.auth.security import hash_session_token, verify_password
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import RoleId

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
            "DELETE FROM audit_log WHERE entity_type IN ('user', 'user_roles')"
        )
        cursor.execute("DELETE FROM auth_sessions WHERE user_id >= 93000")
        cursor.execute("DELETE FROM user_roles WHERE user_id >= 93000")
        cursor.execute("DELETE FROM user_settings WHERE user_id >= 93000")
        cursor.execute("DELETE FROM users WHERE id >= 93000")


def seed_admin_user(
    connection: psycopg.Connection, user_id: int = 93001, username: str = "adm-93001"
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO users (id, username, password_hash, full_name, email, is_active)
            VALUES (%s, %s, 'hash', 'Test Admin', 'admin@test.local', true)
            ON CONFLICT (id) DO NOTHING
            """,
            (user_id, username),
        )
        cursor.execute(
            """
            INSERT INTO user_roles (user_id, role_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (user_id, int(RoleId.ADMIN)),
        )


def make_client(
    connection: psycopg.Connection, actor_user: UserAuthRecord
) -> TestClient:
    app = FastAPI()
    app.include_router(admin_users_router)
    app.dependency_overrides[get_admin_user_repository] = (
        lambda: PostgresAdminUserRepository(connection)
    )
    app.dependency_overrides[get_current_user] = lambda: actor_user
    return TestClient(app)


def test_create_user_hashes_password_and_hides_it_in_response(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin_user(connection, 93001, "adm-93001")
        admin_auth = UserAuthRecord(
            id=93001,
            username="adm-93001",
            password_hash="hash",
            full_name="Test Admin",
            email="admin@test.local",
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client = make_client(connection, admin_auth)

        response = client.post(
            "/api/v1/admin/users",
            json={
                "username": "spec-93002",
                "password": "SecurePassword#2026",
                "full_name": "Специалист 93002",
                "email": "spec93002@test.local",
                "phone": "+79001112233",
                "omnidesk_staff_id": "93002",
                "roles": [int(RoleId.L1), int(RoleId.L2)],
                "is_active": True,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "spec-93002"
        assert data["omnidesk_staff_id"] == "93002"
        assert "password" not in response.text
        assert "password_hash" not in response.text
        assert "SecurePassword#2026" not in response.text

        created_user_id = data["id"]
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT password_hash FROM users WHERE id = %s", (created_user_id,)
            )
            row = cursor.fetchone()
            assert row is not None
            assert verify_password("SecurePassword#2026", row["password_hash"])

            # Verify audit log
            cursor.execute(
                "SELECT action_code, entity_type, entity_id, new_values FROM audit_log WHERE entity_type='user' AND entity_id=%s",
                (created_user_id,),
            )
            audit = cursor.fetchone()
            assert audit is not None
            assert audit["action_code"] == 0
            assert "password" not in str(audit["new_values"])


def test_put_user_roles_updates_roles_in_postgres(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin_user(connection, 93001, "adm-93001")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, username, password_hash, full_name, is_active)
                VALUES (93010, 'user-93010', 'hash', 'User 93010', true)
                """
            )
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (93010, 1)"
            )

        admin_auth = UserAuthRecord(
            id=93001,
            username="adm-93001",
            password_hash="hash",
            full_name="Test Admin",
            email=None,
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client = make_client(connection, admin_auth)

        response = client.put(
            "/api/v1/admin/users/93010/roles",
            json={"roles": [int(RoleId.L2), int(RoleId.MANAGER)]},
        )
        assert response.status_code == 200
        data = response.json()
        assert {r["id"] for r in data["roles"]} == {int(RoleId.L2), int(RoleId.MANAGER)}

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT role_id FROM user_roles WHERE user_id = 93010 ORDER BY role_id"
            )
            db_roles = [r["role_id"] for r in cursor.fetchall()]
            assert db_roles == [int(RoleId.L2), int(RoleId.MANAGER)]

            cursor.execute(
                "SELECT action_code, entity_type, old_values, new_values FROM audit_log WHERE entity_type='user_roles' AND entity_id=93010"
            )
            audit = cursor.fetchone()
            assert audit is not None
            assert audit["old_values"]["roles"] == [1]
            assert audit["new_values"]["roles"] == [2, 3]


def test_deactivation_revokes_active_sessions(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin_user(connection, 93001, "adm-93001")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, username, password_hash, full_name, is_active)
                VALUES (93020, 'target-93020', 'hash', 'Target User', true)
                """
            )
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (93020, 1)"
            )
            cursor.execute(
                """
                INSERT INTO auth_sessions (user_id, session_hash, expires_at)
                VALUES (93020, %s, %s), (93020, %s, %s)
                """,
                (
                    hash_session_token("token-1"),
                    datetime.now(UTC) + timedelta(hours=1),
                    hash_session_token("token-2"),
                    datetime.now(UTC) + timedelta(hours=2),
                ),
            )

        admin_auth = UserAuthRecord(
            id=93001,
            username="adm-93001",
            password_hash="hash",
            full_name="Test Admin",
            email=None,
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client = make_client(connection, admin_auth)

        response = client.patch(
            "/api/v1/admin/users/93020",
            json={"is_active": False},
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is False

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS active_count FROM auth_sessions WHERE user_id = 93020 AND revoked_at IS NULL"
            )
            active_count = cursor.fetchone()["active_count"]
            assert active_count == 0

            cursor.execute("SELECT is_active FROM users WHERE id = 93020")
            assert cursor.fetchone()["is_active"] is False


def test_self_deactivation_prevented_409(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin_user(connection, 93001, "adm-93001")
        admin_auth = UserAuthRecord(
            id=93001,
            username="adm-93001",
            password_hash="hash",
            full_name="Test Admin",
            email=None,
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client = make_client(connection, admin_auth)

        response = client.patch(
            "/api/v1/admin/users/93001",
            json={"is_active": False},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "cannot_deactivate_self"


def test_last_admin_deactivation_prevented_409(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin_user(connection, 93001, "adm-93001")
        # Admin 93001 is the only active admin. Create another non-admin actor or test deactivation.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, username, password_hash, full_name, is_active)
                VALUES (93030, 'adm-93030', 'hash', 'Second Admin', true)
                """
            )
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (93030, 4)"
            )

        admin_auth = UserAuthRecord(
            id=93001,
            username="adm-93001",
            password_hash="hash",
            full_name="Test Admin",
            email=None,
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client = make_client(connection, admin_auth)

        # Deactivating second admin when 2 admins exist is allowed
        resp1 = client.patch("/api/v1/admin/users/93030", json={"is_active": False})
        assert resp1.status_code == 200

        # Now only 93001 is active admin. If actor 93030 (if simulated) tried to deactivate 93001 -> 409
        actor_93030 = UserAuthRecord(
            id=93030,
            username="adm-93030",
            password_hash="hash",
            full_name="Second Admin",
            email=None,
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client_93030 = make_client(connection, actor_93030)
        resp2 = client_93030.patch(
            "/api/v1/admin/users/93001", json={"is_active": False}
        )
        assert resp2.status_code == 409
        assert resp2.json()["detail"] == "cannot_deactivate_last_admin"


def test_non_admin_forbidden_403(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        manager_auth = UserAuthRecord(
            id=93050,
            username="manager-93050",
            password_hash="hash",
            full_name="Manager",
            email=None,
            roles=(RoleRecord(id=int(RoleId.MANAGER), name="Руководитель"),),
        )
        client = make_client(connection, manager_auth)

        assert client.get("/api/v1/admin/users").status_code == 403
        assert (
            client.post(
                "/api/v1/admin/users",
                json={"username": "u", "password": "password-123", "full_name": "f"},
            ).status_code
            == 403
        )
        assert (
            client.patch(
                "/api/v1/admin/users/93001", json={"full_name": "f"}
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/api/v1/admin/users/93001/roles", json={"roles": [1]}
            ).status_code
            == 403
        )


def test_patch_user_settings_upsert_on_first_patch(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin_user(connection, 93001, "adm-93001")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, username, password_hash, full_name, is_active)
                VALUES (93100, 'user-93100', 'hash', 'User 93100', true)
                """
            )
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (93100, 1)"
            )

        admin_auth = UserAuthRecord(
            id=93001,
            username="adm-93001",
            password_hash="hash",
            full_name="Test Admin",
            email=None,
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client = make_client(connection, admin_auth)

        response = client.patch(
            "/api/v1/admin/users/93100",
            json={
                "telegram_chat_id": "-100123456",
                "bitrix24_user_id": "501",
                "notify_telegram": False,
                "notify_bitrix24": True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["telegram_chat_id"] == "-100123456"
        assert data["bitrix24_user_id"] == "501"
        assert data["notify_telegram"] is False
        assert data["notify_bitrix24"] is True
        assert data["timezone"] == "Asia/Yekaterinburg"

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT timezone, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24
                FROM user_settings
                WHERE user_id = 93100
                """
            )
            settings_row = cursor.fetchone()
            assert settings_row is not None
            assert settings_row["timezone"] == "Asia/Yekaterinburg"
            assert settings_row["telegram_chat_id"] == "-100123456"
            assert settings_row["bitrix24_user_id"] == "501"
            assert settings_row["notify_telegram"] is False
            assert settings_row["notify_bitrix24"] is True

            cursor.execute(
                """
                SELECT action_code, entity_type, entity_id, old_values, new_values
                FROM audit_log
                WHERE entity_type = 'user' AND entity_id = 93100
                """
            )
            audit = cursor.fetchone()
            assert audit is not None
            assert audit["old_values"]["telegram_chat_id"] is None
            assert audit["old_values"]["bitrix24_user_id"] is None
            assert audit["old_values"]["notify_telegram"] is True
            assert audit["old_values"]["notify_bitrix24"] is True
            assert audit["new_values"]["telegram_chat_id"] == "-100123456"
            assert audit["new_values"]["bitrix24_user_id"] == "501"
            assert audit["new_values"]["notify_telegram"] is False
            assert audit["new_values"]["notify_bitrix24"] is True


def test_patch_user_settings_updates_and_preserves_other_fields(
    database_url: str,
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin_user(connection, 93001, "adm-93001")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, username, password_hash, full_name, is_active)
                VALUES (93101, 'user-93101', 'hash', 'Original Full Name', true)
                """
            )
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (93101, 1)"
            )
            cursor.execute(
                """
                INSERT INTO user_settings (user_id, timezone, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24)
                VALUES (93101, 'Europe/Moscow', '111', '222', true, true)
                """
            )

        admin_auth = UserAuthRecord(
            id=93001,
            username="adm-93001",
            password_hash="hash",
            full_name="Test Admin",
            email=None,
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client = make_client(connection, admin_auth)

        response = client.patch(
            "/api/v1/admin/users/93101",
            json={"telegram_chat_id": "999"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["telegram_chat_id"] == "999"
        assert data["bitrix24_user_id"] == "222"
        assert data["timezone"] == "Europe/Moscow"
        assert data["full_name"] == "Original Full Name"

        with connection.cursor() as cursor:
            cursor.execute("SELECT full_name FROM users WHERE id = 93101")
            assert cursor.fetchone()["full_name"] == "Original Full Name"

            cursor.execute(
                """
                SELECT timezone, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24
                FROM user_settings
                WHERE user_id = 93101
                """
            )
            settings_row = cursor.fetchone()
            assert settings_row["timezone"] == "Europe/Moscow"
            assert settings_row["telegram_chat_id"] == "999"
            assert settings_row["bitrix24_user_id"] == "222"
            assert settings_row["notify_telegram"] is True
            assert settings_row["notify_bitrix24"] is True

            cursor.execute(
                """
                SELECT action_code, entity_type, entity_id, old_values, new_values
                FROM audit_log
                WHERE entity_type = 'user' AND entity_id = 93101
                """
            )
            audit = cursor.fetchone()
            assert audit is not None
            assert audit["old_values"] == {"telegram_chat_id": "111"}
            assert audit["new_values"] == {"telegram_chat_id": "999"}


def test_patch_user_settings_clear_to_null(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_admin_user(connection, 93001, "adm-93001")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO users (id, username, password_hash, full_name, is_active)
                VALUES (93102, 'user-93102', 'hash', 'User 93102', true)
                """
            )
            cursor.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES (93102, 1)"
            )
            cursor.execute(
                """
                INSERT INTO user_settings (user_id, timezone, telegram_chat_id, bitrix24_user_id, notify_telegram, notify_bitrix24)
                VALUES (93102, 'Asia/Yekaterinburg', '111', '222', true, true)
                """
            )

        admin_auth = UserAuthRecord(
            id=93001,
            username="adm-93001",
            password_hash="hash",
            full_name="Test Admin",
            email=None,
            roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
        )
        client = make_client(connection, admin_auth)

        response = client.patch(
            "/api/v1/admin/users/93102",
            json={"telegram_chat_id": None, "bitrix24_user_id": None},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["telegram_chat_id"] is None
        assert data["bitrix24_user_id"] is None

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT telegram_chat_id, bitrix24_user_id
                FROM user_settings
                WHERE user_id = 93102
                """
            )
            settings_row = cursor.fetchone()
            assert settings_row["telegram_chat_id"] is None
            assert settings_row["bitrix24_user_id"] is None


def test_patch_user_settings_non_admin_forbidden_403(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        manager_auth = UserAuthRecord(
            id=93050,
            username="manager-93050",
            password_hash="hash",
            full_name="Manager",
            email=None,
            roles=(RoleRecord(id=int(RoleId.MANAGER), name="Руководитель"),),
        )
        client = make_client(connection, manager_auth)

        response = client.patch(
            "/api/v1/admin/users/93001",
            json={"telegram_chat_id": "12345"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "insufficient_role"

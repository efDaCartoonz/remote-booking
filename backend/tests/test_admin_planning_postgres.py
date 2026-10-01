from __future__ import annotations

import os
from datetime import UTC, datetime

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
            "DELETE FROM audit_log WHERE entity_type IN ('connection_result', 'production_calendar_day', 'distribution_membership', 'system_setting', 'absence', 'schedule')"
        )
        cursor.execute("DELETE FROM absences")
        cursor.execute("DELETE FROM schedules")
        cursor.execute("DELETE FROM production_calendar_days")
        cursor.execute("DELETE FROM distribution_members")
        cursor.execute("DELETE FROM users WHERE id IN (93001, 93002, 93003)")
        cursor.execute("DELETE FROM system_settings WHERE key = 'planning_params'")


def seed_users(connection) -> None:
    with connection.cursor() as cursor:
        for user_id, username, full_name in (
            (93001, "adm-admin", "Admin User"),
            (93002, "adm-manager", "Manager User"),
            (93003, "adm-l2", "L2 Engineer"),
        ):
            cursor.execute(
                "INSERT INTO users (id, username, password_hash, full_name) VALUES (%s, %s, 'hash', %s) ON CONFLICT (id) DO NOTHING",
                (user_id, username, full_name),
            )
    connection.commit()


def make_auth_user(user_id: int, role_id: RoleId) -> UserAuthRecord:
    return UserAuthRecord(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="unused",
        full_name=f"User {user_id}",
        email=None,
        roles=(RoleRecord(id=int(role_id), name=role_id.name),),
    )


def test_schedules_crud_audit_and_rbac(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection

        current_actor = {"user": make_auth_user(93001, RoleId.ADMIN)}
        app.dependency_overrides[get_current_user] = lambda: current_actor["user"]
        client = TestClient(app)

        # 1. PUT schedule as ADMIN
        schedule_payload = [
            {
                "weekday": 1,
                "start_time": "09:00:00",
                "end_time": "18:00:00",
                "timezone": "Europe/Moscow",
            },
            {
                "weekday": 2,
                "start_time": "09:00:00",
                "end_time": "18:00:00",
                "timezone": "Europe/Moscow",
            },
        ]
        put_resp = client.put("/api/v1/admin/schedules/93003", json=schedule_payload)
        assert put_resp.status_code == 200, put_resp.text
        assert len(put_resp.json()) == 2

        # 2. GET schedule as MANAGER
        current_actor["user"] = make_auth_user(93002, RoleId.MANAGER)
        get_resp = client.get("/api/v1/admin/schedules/93003")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert len(data) == 2
        assert data[0]["weekday"] == 1
        assert data[0]["timezone"] == "Europe/Moscow"

        # 3. GET/PUT as L2 (Forbidden)
        current_actor["user"] = make_auth_user(93003, RoleId.L2)
        assert client.get("/api/v1/admin/schedules/93003").status_code == 403
        assert (
            client.put(
                "/api/v1/admin/schedules/93003", json=schedule_payload
            ).status_code
            == 403
        )

        # 4. Audit verification
        audit_rows = connection.execute(
            "SELECT entity_type, action_code, entity_id, actor_user_id FROM audit_log WHERE entity_type='schedule'"
        ).fetchall()
        assert len(audit_rows) >= 1
        assert audit_rows[0]["actor_user_id"] == 93001
        assert audit_rows[0]["entity_id"] == 93003


def test_absences_crud_audit_and_rbac(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection

        current_actor = {"user": make_auth_user(93001, RoleId.ADMIN)}
        app.dependency_overrides[get_current_user] = lambda: current_actor["user"]
        client = TestClient(app)

        start_time = datetime(2030, 6, 1, 10, 0, tzinfo=UTC)
        end_time = datetime(2030, 6, 1, 14, 0, tzinfo=UTC)

        # 1. POST absence as ADMIN
        post_resp = client.post(
            "/api/v1/admin/absences",
            json={
                "user_id": 93003,
                "start_at": start_time.isoformat(),
                "end_at": end_time.isoformat(),
                "reason": "Conference",
            },
        )
        assert post_resp.status_code == 201, post_resp.text
        absence_id = post_resp.json()["id"]

        # 2. GET absences as MANAGER
        current_actor["user"] = make_auth_user(93002, RoleId.MANAGER)
        get_resp = client.get(
            "/api/v1/admin/absences",
            params={"user_id": 93003},
        )
        assert get_resp.status_code == 200
        items = get_resp.json()
        assert len(items) == 1
        assert items[0]["id"] == absence_id
        assert items[0]["reason"] == "Conference"

        # 3. DELETE absence as ADMIN
        current_actor["user"] = make_auth_user(93001, RoleId.ADMIN)
        del_resp = client.delete(f"/api/v1/admin/absences/{absence_id}")
        assert del_resp.status_code == 200
        assert del_resp.json() == {"deleted": True, "id": absence_id}

        # 4. DELETE non-existent returns 404
        del_404 = client.delete(f"/api/v1/admin/absences/{absence_id}")
        assert del_404.status_code == 404

        # 5. Audit verification
        audit_rows = connection.execute(
            "SELECT entity_type, action_code, entity_id FROM audit_log WHERE entity_type='absence' ORDER BY id"
        ).fetchall()
        assert len(audit_rows) == 2  # create + delete


def test_calendar_days_crud_and_rbac(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection

        current_actor = {"user": make_auth_user(93001, RoleId.ADMIN)}
        app.dependency_overrides[get_current_user] = lambda: current_actor["user"]
        client = TestClient(app)

        # 1. PUT calendar day as ADMIN
        cal_date = "2030-05-09"
        put_resp = client.put(
            f"/api/v1/admin/calendar/days/{cal_date}",
            json={
                "day_type_code": 1,
                "is_manual_override": True,
                "comment": "Victory Day",
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["day_type_code"] == 1

        # 2. GET calendar days range as MANAGER
        current_actor["user"] = make_auth_user(93002, RoleId.MANAGER)
        get_resp = client.get(
            "/api/v1/admin/calendar/days",
            params={"from_date": "2030-05-01", "to_date": "2030-05-31"},
        )
        assert get_resp.status_code == 200
        days = get_resp.json()
        assert len(days) == 1
        assert days[0]["date"] == cal_date
        assert days[0]["comment"] == "Victory Day"

        # 3. Audit verification
        audit_row = connection.execute(
            "SELECT entity_type, action_code, actor_user_id FROM audit_log WHERE entity_type='production_calendar_day'"
        ).fetchone()
        assert audit_row is not None
        assert audit_row["actor_user_id"] == 93001


def test_distribution_membership_crud_and_audit(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection

        current_actor = {"user": make_auth_user(93001, RoleId.ADMIN)}
        app.dependency_overrides[get_current_user] = lambda: current_actor["user"]
        client = TestClient(app)

        # 1. PUT without comment -> 422
        bad_put = client.put(
            "/api/v1/admin/distribution/members/93003",
            json={"pool_code": 2, "enabled": True, "comment": "   "},
        )
        assert bad_put.status_code == 422

        # 2. PUT with comment as ADMIN
        put_resp = client.put(
            "/api/v1/admin/distribution/members/93003",
            json={
                "pool_code": 2,
                "enabled": True,
                "comment": "Manager decision #42",
            },
        )
        assert put_resp.status_code == 200, put_resp.text
        assert put_resp.json()["is_enabled"] is True

        # 3. GET distribution members as MANAGER
        current_actor["user"] = make_auth_user(93002, RoleId.MANAGER)
        get_resp = client.get("/api/v1/admin/distribution/members?pool_code=2")
        assert get_resp.status_code == 200
        members = get_resp.json()
        assert len(members) == 1
        assert members[0]["user_id"] == 93003
        assert members[0]["comment"] == "Manager decision #42"

        # 4. Audit verification (ADM-009)
        audit_row = connection.execute(
            "SELECT entity_type, action_code, actor_user_id FROM audit_log WHERE entity_type='distribution_membership'"
        ).fetchone()
        assert audit_row is not None
        assert audit_row["actor_user_id"] == 93001


def test_planning_settings_crud_audit_and_effect(database_url: str) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        seed_users(connection)
        app = create_app()

        app.dependency_overrides[get_db] = lambda: connection

        current_actor = {"user": make_auth_user(93001, RoleId.ADMIN)}
        app.dependency_overrides[get_current_user] = lambda: current_actor["user"]
        client = TestClient(app)

        # 1. GET default planning settings
        get_resp = client.get("/api/v1/admin/settings/planning")
        assert get_resp.status_code == 200
        assert get_resp.json() == {
            "min_lead_minutes": 120,
            "horizon_days": 14,
            "default_duration_minutes": 60,
            "min_duration_minutes": 30,
            "max_duration_minutes": 720,
        }

        # 2. PUT updated settings as ADMIN
        new_settings = {
            "min_lead_minutes": 60,
            "horizon_days": 30,
            "default_duration_minutes": 45,
            "min_duration_minutes": 15,
            "max_duration_minutes": 300,
        }
        put_resp = client.put(
            "/api/v1/admin/settings/planning",
            json=new_settings,
        )
        assert put_resp.status_code == 200
        assert put_resp.json() == new_settings

        # 3. GET returns updated settings
        current_actor["user"] = make_auth_user(93002, RoleId.MANAGER)
        get_updated = client.get("/api/v1/admin/settings/planning")
        assert get_updated.status_code == 200
        assert get_updated.json() == new_settings

        # 4. Audit verification
        audit_row = connection.execute(
            "SELECT entity_type, action_code, actor_user_id FROM audit_log WHERE entity_type='system_setting' AND new_values ? 'planning_params'"
        ).fetchone()
        assert audit_row is not None
        assert audit_row["actor_user_id"] == 93001

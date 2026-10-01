from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.auth.dependencies import get_auth_store
from app.auth.security import hash_password, hash_session_token
from app.auth.store import (
    AuthSessionRecord,
    PostgresAuthStore,
    RoleRecord,
    SessionRecord,
    UserAuthRecord,
)
from app.core.config import settings
from app.main import create_app


class FakeTimezoneAuthStore:
    def __init__(self) -> None:
        self.users: dict[int, UserAuthRecord] = {
            1: UserAuthRecord(
                id=1,
                username="engineer1",
                password_hash=hash_password("password"),
                full_name="Инженер 1",
                email="eng1@test.com",
                roles=(RoleRecord(id=2, name="Специалист Л2"),),
                timezone="Asia/Yekaterinburg",
            ),
            2: UserAuthRecord(
                id=2,
                username="engineer2",
                password_hash=hash_password("password"),
                full_name="Инженер 2",
                email="eng2@test.com",
                roles=(RoleRecord(id=1, name="Специалист Л1"),),
                timezone="Europe/Moscow",
            ),
            3: UserAuthRecord(
                id=3,
                username="admin_user",
                password_hash=hash_password("password"),
                full_name="Администратор",
                email="admin@test.com",
                roles=(RoleRecord(id=4, name="Администратор"),),
                timezone="UTC",
            ),
        }
        self.sessions: dict[str, SessionRecord] = {}
        self.audit_events: list[dict[str, Any]] = []

    def get_user_by_username(self, username: str) -> UserAuthRecord | None:
        for u in self.users.values():
            if u.username == username:
                return u
        return None

    def get_user_by_session_hash(self, session_hash: str) -> AuthSessionRecord | None:
        session = self.sessions.get(session_hash)
        if session is None or session.revoked_at is not None:
            return None
        user = self.users.get(session.user_id)
        if user is None:
            return None
        return AuthSessionRecord(session=session, user=user)

    def create_session(
        self, user_id: int, session_hash: str, expires_at: datetime
    ) -> SessionRecord:
        session = SessionRecord(
            id=len(self.sessions) + 1,
            user_id=user_id,
            session_hash=session_hash,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
            last_seen_at=None,
            revoked_at=None,
        )
        self.sessions[session_hash] = session
        return session

    def touch_session(self, session_id: int, seen_at: datetime) -> None:
        pass

    def revoke_session(self, session_id: int, revoked_at: datetime) -> None:
        for k, s in self.sessions.items():
            if s.id == session_id:
                self.sessions[k] = SessionRecord(
                    id=s.id,
                    user_id=s.user_id,
                    session_hash=s.session_hash,
                    created_at=s.created_at,
                    expires_at=s.expires_at,
                    last_seen_at=s.last_seen_at,
                    revoked_at=revoked_at,
                )

    def log_auth_event(self, **kwargs: Any) -> None:
        self.audit_events.append(kwargs)

    def get_user_timezone(self, user_id: int) -> str | None:
        user = self.users.get(user_id)
        if user is None:
            return None
        return user.timezone

    def set_user_timezone(
        self,
        *,
        actor_user_id: int,
        target_user_id: int,
        timezone: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        user = self.users.get(target_user_id)
        if user is None:
            raise ValueError("user_not_found")
        old_tz = user.timezone
        updated = UserAuthRecord(
            id=user.id,
            username=user.username,
            password_hash=user.password_hash,
            full_name=user.full_name,
            email=user.email,
            roles=user.roles,
            timezone=timezone,
        )
        self.users[target_user_id] = updated
        self.audit_events.append(
            {
                "actor_user_id": actor_user_id,
                "actor_type_code": 0,
                "action_code": 1,
                "entity_type": "user_settings",
                "entity_id": target_user_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
                "old_values": {"timezone": old_tz},
                "new_values": {"timezone": timezone},
            }
        )
        return timezone


def make_client_with_auth(
    store: FakeTimezoneAuthStore, user_id: int
) -> tuple[TestClient, str]:
    app = create_app()
    app.dependency_overrides[get_auth_store] = lambda: store
    client = TestClient(app, base_url="https://testserver")

    token = f"token-for-user-{user_id}"
    store.create_session(
        user_id=user_id,
        session_hash=hash_session_token(token),
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    client.cookies.set(settings.auth_session_cookie_name, token)
    return client, token


def test_get_own_timezone() -> None:
    store = FakeTimezoneAuthStore()
    client, _ = make_client_with_auth(store, user_id=1)

    response = client.get("/api/v1/auth/timezone")
    assert response.status_code == 200
    assert response.json() == {"user_id": 1, "timezone": "Asia/Yekaterinburg"}


def test_put_own_timezone_success_and_audited() -> None:
    store = FakeTimezoneAuthStore()
    client, _ = make_client_with_auth(store, user_id=1)

    response = client.put(
        "/api/v1/auth/timezone",
        json={"timezone": "Europe/Kaliningrad"},
        headers={"User-Agent": "TestBrowser/1.0"},
    )
    assert response.status_code == 200
    assert response.json() == {"user_id": 1, "timezone": "Europe/Kaliningrad"}

    # Verify store updated
    assert store.get_user_timezone(1) == "Europe/Kaliningrad"

    # Verify audit event
    assert len(store.audit_events) == 1
    event = store.audit_events[0]
    assert event["actor_user_id"] == 1
    assert event["action_code"] == 1
    assert event["entity_type"] == "user_settings"
    assert event["entity_id"] == 1
    assert event["old_values"] == {"timezone": "Asia/Yekaterinburg"}
    assert event["new_values"] == {"timezone": "Europe/Kaliningrad"}
    assert event["user_agent"] == "TestBrowser/1.0"


def test_put_timezone_validates_iana_zone() -> None:
    store = FakeTimezoneAuthStore()
    client, _ = make_client_with_auth(store, user_id=1)

    # Invalid zone names
    for bad_zone in ["Invalid/Timezone", "Mars/Olympus", "GMT+99", ""]:
        res = client.put("/api/v1/auth/timezone", json={"timezone": bad_zone})
        assert res.status_code == 422
        assert store.get_user_timezone(1) == "Asia/Yekaterinburg"
        assert len(store.audit_events) == 0


def test_non_admin_cannot_get_other_user_timezone() -> None:
    store = FakeTimezoneAuthStore()
    client, _ = make_client_with_auth(store, user_id=1)

    response = client.get("/api/v1/auth/users/2/timezone")
    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient_role"


def test_non_admin_cannot_put_other_user_timezone() -> None:
    store = FakeTimezoneAuthStore()
    client, _ = make_client_with_auth(store, user_id=1)

    response = client.put(
        "/api/v1/auth/users/2/timezone",
        json={"timezone": "Asia/Novosibirsk"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient_role"
    assert store.get_user_timezone(2) == "Europe/Moscow"
    assert len(store.audit_events) == 0


def test_admin_can_get_other_user_timezone() -> None:
    store = FakeTimezoneAuthStore()
    admin_client, _ = make_client_with_auth(store, user_id=3)

    response = admin_client.get("/api/v1/auth/users/2/timezone")
    assert response.status_code == 200
    assert response.json() == {"user_id": 2, "timezone": "Europe/Moscow"}


def test_admin_can_put_other_user_timezone_with_audit() -> None:
    store = FakeTimezoneAuthStore()
    admin_client, _ = make_client_with_auth(store, user_id=3)

    response = admin_client.put(
        "/api/v1/auth/users/2/timezone",
        json={"timezone": "Asia/Vladivostok"},
    )
    assert response.status_code == 200
    assert response.json() == {"user_id": 2, "timezone": "Asia/Vladivostok"}
    assert store.get_user_timezone(2) == "Asia/Vladivostok"

    assert len(store.audit_events) == 1
    event = store.audit_events[0]
    assert event["actor_user_id"] == 3
    assert event["entity_id"] == 2
    assert event["old_values"] == {"timezone": "Europe/Moscow"}
    assert event["new_values"] == {"timezone": "Asia/Vladivostok"}


def test_user_can_access_own_timezone_via_users_path() -> None:
    store = FakeTimezoneAuthStore()
    client, _ = make_client_with_auth(store, user_id=1)

    # Accessing own timezone via /users/1/timezone is allowed even without admin role
    res = client.get("/api/v1/auth/users/1/timezone")
    assert res.status_code == 200
    assert res.json() == {"user_id": 1, "timezone": "Asia/Yekaterinburg"}

    res = client.put(
        "/api/v1/auth/users/1/timezone",
        json={"timezone": "Asia/Tomsk"},
    )
    assert res.status_code == 200
    assert res.json() == {"user_id": 1, "timezone": "Asia/Tomsk"}


def test_target_user_not_found_returns_404() -> None:
    store = FakeTimezoneAuthStore()
    admin_client, _ = make_client_with_auth(store, user_id=3)

    res = admin_client.get("/api/v1/auth/users/999/timezone")
    assert res.status_code == 404
    assert res.json()["detail"] == "user_not_found"

    res = admin_client.put(
        "/api/v1/auth/users/999/timezone",
        json={"timezone": "UTC"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "user_not_found"


def test_unauthenticated_requests_return_401() -> None:
    app = create_app()
    app.dependency_overrides[get_auth_store] = lambda: FakeTimezoneAuthStore()
    anon_client = TestClient(app, base_url="https://testserver")

    assert anon_client.get("/api/v1/auth/timezone").status_code == 401
    assert (
        anon_client.put("/api/v1/auth/timezone", json={"timezone": "UTC"}).status_code
        == 401
    )
    assert anon_client.get("/api/v1/auth/users/1/timezone").status_code == 401
    assert (
        anon_client.put(
            "/api/v1/auth/users/1/timezone", json={"timezone": "UTC"}
        ).status_code
        == 401
    )


def test_postgres_auth_store_timezone_methods() -> None:
    mock_cursor = MagicMock()
    # Mock row for get_user_timezone
    mock_cursor.fetchone.side_effect = [
        {"timezone": "Asia/Yekaterinburg"},  # first fetch for get_user_timezone
        {"old_timezone": "Asia/Yekaterinburg"},  # fetch for set_user_timezone
    ]
    mock_connection = MagicMock()
    mock_connection.cursor.return_value.__enter__.return_value = mock_cursor

    pg_store = PostgresAuthStore(mock_connection)

    # get_user_timezone
    tz = pg_store.get_user_timezone(10)
    assert tz == "Asia/Yekaterinburg"

    # set_user_timezone
    updated = pg_store.set_user_timezone(
        actor_user_id=1,
        target_user_id=10,
        timezone="Europe/Moscow",
        ip_address="127.0.0.1",
        user_agent="UnitTester",
    )
    assert updated == "Europe/Moscow"
    # Ensure insert into user_settings and audit_log occurred
    assert mock_cursor.execute.call_count >= 3

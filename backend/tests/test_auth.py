from datetime import UTC, datetime, timedelta
from typing import Annotated

from app.auth.dependencies import get_auth_store, require_roles
from app.auth.security import hash_password, hash_session_token
from app.auth.store import AuthSessionRecord, RoleRecord, SessionRecord, UserAuthRecord
from app.core.config import settings
from app.main import create_app
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


class FakeAuthStore:
    def __init__(self) -> None:
        self.user = UserAuthRecord(
            id=1,
            username="ivanov",
            password_hash=hash_password("valid-password"),
            full_name="Иван Иванов",
            email="ivanov@example.test",
            roles=(RoleRecord(id=1, name="Специалист Л1"),),
        )
        self.users = {self.user.username: self.user}
        self.sessions: dict[str, SessionRecord] = {}
        self.audit_events: list[dict] = []
        self.touched_sessions: list[int] = []
        self.revoked_sessions: list[int] = []
        self.next_session_id = 1

    def get_user_by_username(self, username: str) -> UserAuthRecord | None:
        return self.users.get(username)

    def get_user_by_session_hash(self, session_hash: str) -> AuthSessionRecord | None:
        session = self.sessions.get(session_hash)
        if session is None or session.revoked_at is not None:
            return None
        return AuthSessionRecord(session=session, user=self.user)

    def create_session(
        self, user_id: int, session_hash: str, expires_at: datetime
    ) -> SessionRecord:
        session = SessionRecord(
            id=self.next_session_id,
            user_id=user_id,
            session_hash=session_hash,
            created_at=datetime.now(UTC),
            expires_at=expires_at,
            last_seen_at=None,
            revoked_at=None,
        )
        self.next_session_id += 1
        self.sessions[session_hash] = session
        return session

    def touch_session(self, session_id: int, seen_at: datetime) -> None:
        self.touched_sessions.append(session_id)

    def revoke_session(self, session_id: int, revoked_at: datetime) -> None:
        self.revoked_sessions.append(session_id)
        for key, session in self.sessions.items():
            if session.id == session_id:
                self.sessions[key] = SessionRecord(
                    id=session.id,
                    user_id=session.user_id,
                    session_hash=session.session_hash,
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                    last_seen_at=session.last_seen_at,
                    revoked_at=revoked_at,
                )
                break

    def log_auth_event(
        self,
        *,
        user_id: int,
        action_code: int,
        session_id: int | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self.audit_events.append(
            {
                "user_id": user_id,
                "action_code": action_code,
                "session_id": session_id,
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
        )


def make_client(store: FakeAuthStore) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_auth_store] = lambda: store
    return TestClient(app, base_url="https://testserver")


def add_role_protected_route(app: FastAPI) -> None:
    @app.post("/protected")
    def protected(
        _: Annotated[UserAuthRecord, Depends(require_roles(4))],
    ) -> dict[str, bool]:
        return {"ok": True}


def test_login_success_creates_session_cookie_and_audit_event() -> None:
    store = FakeAuthStore()
    client = make_client(store)

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "ivanov", "password": "valid-password"},
    )

    assert response.status_code == 200
    assert response.json()["user"] == {
        "id": 1,
        "username": "ivanov",
        "full_name": "Иван Иванов",
        "email": "ivanov@example.test",
        "roles": [{"id": 1, "name": "Специалист Л1"}],
    }
    set_cookie = response.headers["set-cookie"]
    assert f"{settings.auth_session_cookie_name}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "Max-Age=" in set_cookie
    assert len(store.sessions) == 1
    assert [event["action_code"] for event in store.audit_events] == [3]


def test_login_rejects_wrong_password() -> None:
    store = FakeAuthStore()
    client = make_client(store)

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "ivanov", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_credentials"
    assert store.sessions == {}
    assert store.audit_events == []


def test_me_rejects_expired_session() -> None:
    store = FakeAuthStore()
    token = "expired-session-token"
    expired_session = SessionRecord(
        id=1,
        user_id=1,
        session_hash=hash_session_token(token),
        created_at=datetime.now(UTC) - timedelta(hours=2),
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        last_seen_at=None,
        revoked_at=None,
    )
    store.sessions[expired_session.session_hash] = expired_session
    client = make_client(store)

    response = client.get(
        "/api/v1/auth/me",
        headers={"cookie": f"{settings.auth_session_cookie_name}={token}"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "not_authenticated"
    assert store.touched_sessions == []


def test_logout_revokes_session_clears_cookie_and_writes_audit_event() -> None:
    store = FakeAuthStore()
    client = make_client(store)
    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "ivanov", "password": "valid-password"},
    )
    assert login_response.status_code == 200

    logout_response = client.post("/api/v1/auth/logout")

    assert logout_response.status_code == 204
    assert store.revoked_sessions == [1]
    assert [event["action_code"] for event in store.audit_events] == [3, 4]
    assert f"{settings.auth_session_cookie_name}=" in logout_response.headers["set-cookie"]
    assert "Max-Age=0" in logout_response.headers["set-cookie"]
    assert client.get("/api/v1/auth/me").status_code == 401


def test_role_dependency_rejects_request_without_authentication() -> None:
    store = FakeAuthStore()
    app = create_app()
    app.dependency_overrides[get_auth_store] = lambda: store
    add_role_protected_route(app)
    client = TestClient(app, base_url="https://testserver")

    response = client.post("/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "not_authenticated"

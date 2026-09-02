from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import psycopg
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class RoleRecord:
    id: int
    name: str


@dataclass(frozen=True)
class UserAuthRecord:
    id: int
    username: str
    password_hash: str
    full_name: str
    email: str | None
    roles: tuple[RoleRecord, ...]


@dataclass(frozen=True)
class SessionRecord:
    id: int
    user_id: int
    session_hash: str
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True)
class AuthSessionRecord:
    session: SessionRecord
    user: UserAuthRecord


class AuthStore(Protocol):
    def get_user_by_username(self, username: str) -> UserAuthRecord | None: ...

    def get_user_by_session_hash(self, session_hash: str) -> AuthSessionRecord | None: ...

    def create_session(
        self, user_id: int, session_hash: str, expires_at: datetime
    ) -> SessionRecord: ...

    def touch_session(self, session_id: int, seen_at: datetime) -> None: ...

    def revoke_session(self, session_id: int, revoked_at: datetime) -> None: ...

    def log_auth_event(
        self,
        *,
        user_id: int,
        action_code: int,
        session_id: int | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None: ...


class PostgresAuthStore:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def get_user_by_username(self, username: str) -> UserAuthRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, username, password_hash, full_name, email
                FROM users
                WHERE username = %(username)s AND is_active = true
                """,
                {"username": username},
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return UserAuthRecord(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            full_name=row["full_name"],
            email=row["email"],
            roles=self._load_roles(row["id"]),
        )

    def get_user_by_session_hash(self, session_hash: str) -> AuthSessionRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    s.id AS session_id,
                    s.user_id,
                    s.session_hash,
                    s.created_at,
                    s.expires_at,
                    s.last_seen_at,
                    s.revoked_at,
                    u.id AS user_id,
                    u.username,
                    u.password_hash,
                    u.full_name,
                    u.email
                FROM auth_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.session_hash = %(session_hash)s
                  AND s.revoked_at IS NULL
                  AND u.is_active = true
                """,
                {"session_hash": session_hash},
            )
            row = cursor.fetchone()

        if row is None:
            return None

        user = UserAuthRecord(
            id=row["user_id"],
            username=row["username"],
            password_hash=row["password_hash"],
            full_name=row["full_name"],
            email=row["email"],
            roles=self._load_roles(row["user_id"]),
        )
        session = SessionRecord(
            id=row["session_id"],
            user_id=row["user_id"],
            session_hash=row["session_hash"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_seen_at=row["last_seen_at"],
            revoked_at=row["revoked_at"],
        )
        return AuthSessionRecord(session=session, user=user)

    def create_session(
        self, user_id: int, session_hash: str, expires_at: datetime
    ) -> SessionRecord:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO auth_sessions (user_id, session_hash, expires_at)
                VALUES (%(user_id)s, %(session_hash)s, %(expires_at)s)
                RETURNING id, user_id, session_hash, created_at, expires_at, last_seen_at, revoked_at
                """,
                {
                    "user_id": user_id,
                    "session_hash": session_hash,
                    "expires_at": expires_at,
                },
            )
            row = cursor.fetchone()

        return SessionRecord(
            id=row["id"],
            user_id=row["user_id"],
            session_hash=row["session_hash"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_seen_at=row["last_seen_at"],
            revoked_at=row["revoked_at"],
        )

    def touch_session(self, session_id: int, seen_at: datetime) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE auth_sessions
                SET last_seen_at = %(seen_at)s
                WHERE id = %(session_id)s AND revoked_at IS NULL
                """,
                {"session_id": session_id, "seen_at": seen_at},
            )

    def revoke_session(self, session_id: int, revoked_at: datetime) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE auth_sessions
                SET revoked_at = %(revoked_at)s
                WHERE id = %(session_id)s AND revoked_at IS NULL
                """,
                {"session_id": session_id, "revoked_at": revoked_at},
            )

    def log_auth_event(
        self,
        *,
        user_id: int,
        action_code: int,
        session_id: int | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_log (
                    actor_user_id,
                    actor_type_code,
                    action_code,
                    entity_type,
                    entity_id,
                    ip_address,
                    user_agent,
                    new_values
                )
                VALUES (
                    %(user_id)s,
                    0,
                    %(action_code)s,
                    'auth_session',
                    %(session_id)s,
                    %(ip_address)s,
                    %(user_agent)s,
                    %(new_values)s
                )
                """,
                {
                    "user_id": user_id,
                    "action_code": action_code,
                    "session_id": session_id,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    "new_values": Jsonb({"session_id": session_id}),
                },
            )

    def _load_roles(self, user_id: int) -> tuple[RoleRecord, ...]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id, r.name
                FROM roles r
                JOIN user_roles ur ON ur.role_id = r.id
                WHERE ur.user_id = %(user_id)s AND r.visible = true
                ORDER BY r.id
                """,
                {"user_id": user_id},
            )
            rows = cursor.fetchall()

        return tuple(RoleRecord(id=row["id"], name=row["name"]) for row in rows)

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import redis

from app.auth.security import hash_session_token, new_session_token
from app.core.config import settings

FRAME_TOKEN_HEADER = "x-rdm-frame-token"
FRAME_SESSION_PERMISSIONS = ("cards:read", "cards:create")


@dataclass(frozen=True)
class FrameSession:
    omnidesk_ticket_number: str
    omnidesk_user_id: str
    created_at: datetime
    expires_at: datetime
    origin: str | None
    permissions: tuple[str, ...]
    omnidesk_company_id: str | None = None


@dataclass(frozen=True)
class CreatedFrameSession:
    token: str
    session: FrameSession


class FrameSessionStore(Protocol):
    def create_session(
        self,
        *,
        omnidesk_ticket_number: str,
        omnidesk_user_id: str,
        omnidesk_company_id: str | None,
        origin: str | None,
    ) -> CreatedFrameSession: ...

    def get_session(self, token: str) -> FrameSession | None: ...


class RedisFrameSessionStore:
    def __init__(self, redis_client: redis.Redis) -> None:
        self.redis_client = redis_client

    def create_session(
        self,
        *,
        omnidesk_ticket_number: str,
        omnidesk_user_id: str,
        omnidesk_company_id: str | None,
        origin: str | None,
    ) -> CreatedFrameSession:
        token = new_session_token()
        now = datetime.now(UTC)
        ttl_seconds = settings.frame_session_ttl_minutes * 60
        session = FrameSession(
            omnidesk_ticket_number=omnidesk_ticket_number,
            omnidesk_user_id=omnidesk_user_id,
            omnidesk_company_id=omnidesk_company_id,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            origin=origin,
            permissions=FRAME_SESSION_PERMISSIONS,
        )
        payload = asdict(session)
        payload["created_at"] = session.created_at.isoformat()
        payload["expires_at"] = session.expires_at.isoformat()
        payload["permissions"] = list(session.permissions)
        self.redis_client.setex(_session_key(token), ttl_seconds, json.dumps(payload))
        return CreatedFrameSession(token=token, session=session)

    def get_session(self, token: str) -> FrameSession | None:
        raw = self.redis_client.get(_session_key(token))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        expires_at = datetime.fromisoformat(data["expires_at"])
        if expires_at <= datetime.now(UTC):
            return None
        return FrameSession(
            omnidesk_ticket_number=data["omnidesk_ticket_number"],
            omnidesk_user_id=data["omnidesk_user_id"],
            omnidesk_company_id=data.get("omnidesk_company_id"),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=expires_at,
            origin=data.get("origin"),
            permissions=tuple(data["permissions"]),
        )


def get_frame_session_store() -> FrameSessionStore:
    redis_client = redis.Redis.from_url(settings.redis_url)
    return RedisFrameSessionStore(redis_client)


def _session_key(token: str) -> str:
    return f"frame_session:{hash_session_token(token)}"

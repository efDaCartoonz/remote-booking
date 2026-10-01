from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.cards.constants import CardStatusSlug


class CancellationVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


class CancellationConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str


class CancellationLinkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "link_queued"
    expires_in_seconds: int = 300


class CancellationVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    status: str
    card_public_id: UUID | None = None
    omnidesk_ticket_number: str | None = None
    planned_start_at: datetime | None = None
    planned_duration_minutes: int | None = None
    card_status: CardStatusSlug | None = None
    card_status_label: str | None = None
    expires_at: datetime | None = None
    can_cancel: bool = False


class CancellationConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    card_public_id: UUID
    cancelled_at: datetime
    idempotent: bool = False

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.cards.constants import CardStatus, CardStatusSlug, status_label, status_slug
from app.cards.repository import CardRecord
from app.cards.schemas import TicketNumber
from app.frame.sessions import FrameSession

TimezoneName = Annotated[str, Field(min_length=1, max_length=64)]
OmnideskCaseId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^\d+$")]


class FrameSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: OmnideskCaseId
    omnidesk_ticket_number: TicketNumber


class FrameSessionResponse(BaseModel):
    token: str
    omnidesk_ticket_number: str
    expires_at: datetime
    permissions: list[str]


class FrameCardCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    planned_start_at: datetime
    planned_duration_minutes: int = Field(default=60, ge=30, le=720)
    client_timezone_at_creation: TimezoneName = "Europe/Moscow"
    timezone_source_code: int | None = Field(default=None, ge=0)
    client_contact_type_code: int | None = Field(default=None, ge=0)
    client_contact_value: str | None = Field(default=None, max_length=255)
    description: str | None = None

    @field_validator("planned_start_at")
    @classmethod
    def planned_start_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("planned_start_at_must_be_timezone_aware")
        return value

    @field_validator("client_timezone_at_creation")
    @classmethod
    def client_timezone_must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("client_timezone_unknown") from exc
        return value


class FrameCardResponse(BaseModel):
    id: UUID
    status: CardStatusSlug
    status_label: str
    planned_start_at: datetime
    planned_end_at: datetime
    planned_duration_minutes: int
    client_timezone_at_creation: str | None
    description: str | None
    available_actions: list[str]


class FrameCardsResponse(BaseModel):
    omnidesk_ticket_number: str
    can_create: bool
    cards: list[FrameCardResponse]


def frame_session_response(
    created_session: FrameSession, token: str
) -> FrameSessionResponse:
    return FrameSessionResponse(
        token=token,
        omnidesk_ticket_number=created_session.omnidesk_ticket_number,
        expires_at=created_session.expires_at,
        permissions=list(created_session.permissions),
    )


def frame_cards_response(
    session: FrameSession, cards: list[CardRecord]
) -> FrameCardsResponse:
    return FrameCardsResponse(
        omnidesk_ticket_number=session.omnidesk_ticket_number,
        can_create=not any(_is_active(card) for card in cards),
        cards=[frame_card_response(card) for card in cards],
    )


def frame_card_response(card: CardRecord) -> FrameCardResponse:
    card_status = CardStatus(card.status_code)
    return FrameCardResponse(
        id=card.public_id,
        status=status_slug(card_status),
        status_label=status_label(card_status),
        planned_start_at=card.planned_start_at,
        planned_end_at=card.planned_start_at
        + timedelta(minutes=card.planned_duration_minutes),
        planned_duration_minutes=card.planned_duration_minutes,
        client_timezone_at_creation=card.client_timezone_at_creation,
        description=card.description,
        available_actions=["read"],
    )


def _is_active(card: CardRecord) -> bool:
    return CardStatus(card.status_code) not in {
        CardStatus.COMPLETED,
        CardStatus.CANCELLED,
    }

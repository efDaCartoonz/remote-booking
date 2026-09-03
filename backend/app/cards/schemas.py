from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.cards.constants import CardStatus, CardStatusSlug, status_label, status_slug
from app.cards.repository import CardRecord

TicketNumber = Annotated[str, Field(pattern=r"^[0-9]{3}-[0-9]{6}$")]


class CardCreateRequest(BaseModel):
    omnidesk_ticket_number: TicketNumber
    planned_start_at: datetime
    planned_duration_minutes: int = Field(default=60, ge=30, le=720)
    client_id: int | None = None
    criticality_code: int = Field(default=0, ge=0)
    urgency_code: int = Field(default=0, ge=0)
    client_timezone_at_creation: str | None = Field(default=None, max_length=64)
    timezone_source_code: int | None = Field(default=None, ge=0)
    l1_owner_id: int | None = None
    l2_engineer_id: int | None = None
    assignment_method_code: int | None = Field(default=None, ge=0)
    client_contact_type_code: int | None = Field(default=None, ge=0)
    client_contact_value: str | None = Field(default=None, max_length=255)
    description: str | None = None
    urgent_reason: str | None = None
    out_of_hours_flag: bool = False
    retroactive_flag: bool = False

    @field_validator("planned_start_at")
    @classmethod
    def planned_start_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("planned_start_at_must_be_timezone_aware")
        return value


class CardAssignRequest(BaseModel):
    l2_engineer_id: int = Field(gt=0)
    comment: str | None = None


class CardStatusChangeRequest(BaseModel):
    comment: str | None = None


class CardRejectRequest(BaseModel):
    rejection_reason: str = Field(min_length=1)


class CardCompleteRequest(BaseModel):
    result_code: int = Field(ge=0)
    engineer_report: str = Field(min_length=1)
    comment: str | None = None


class CardResponse(BaseModel):
    id: UUID
    number: str
    omnidesk_ticket_number: str
    status: CardStatusSlug
    status_code: int
    status_label: str
    planned_start_at: datetime
    planned_end_at: datetime
    planned_duration_minutes: int
    l1_owner_id: int | None
    l2_engineer_id: int | None
    criticality_code: int
    urgency_code: int
    overdue_flag: bool
    out_of_hours_flag: bool
    retroactive_flag: bool
    description: str | None
    result_code: int | None
    engineer_report: str | None
    actual_start_at: datetime | None
    actual_end_at: datetime | None
    created_by_id: int | None
    created_at: datetime
    updated_at: datetime


def card_response(card: CardRecord) -> CardResponse:
    card_status = CardStatus(card.status_code)
    return CardResponse(
        id=card.public_id,
        number=card.number,
        omnidesk_ticket_number=card.omnidesk_ticket_number,
        status=status_slug(card_status),
        status_code=int(card_status),
        status_label=status_label(card_status),
        planned_start_at=card.planned_start_at,
        planned_end_at=card.planned_start_at
        + timedelta(minutes=card.planned_duration_minutes),
        planned_duration_minutes=card.planned_duration_minutes,
        l1_owner_id=card.l1_owner_id,
        l2_engineer_id=card.l2_engineer_id,
        criticality_code=card.criticality_code,
        urgency_code=card.urgency_code,
        overdue_flag=card.overdue_flag,
        out_of_hours_flag=card.out_of_hours_flag,
        retroactive_flag=card.retroactive_flag,
        description=card.description,
        result_code=card.result_code,
        engineer_report=card.engineer_report,
        actual_start_at=card.actual_start_at,
        actual_end_at=card.actual_end_at,
        created_by_id=card.created_by_id,
        created_at=card.created_at,
        updated_at=card.updated_at,
    )

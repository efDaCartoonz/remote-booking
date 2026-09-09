from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator, model_validator

from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import CardStatus, RoleId, status_slug
from app.cards.repository import CardRepository, PostgresCardRepository
from app.cards.schemas import status_label
from app.db import get_db

router = APIRouter(prefix="/api/v1/manager", tags=["manager"])


class ManagerSummary(BaseModel):
    assigned: int
    confirmed: int
    rejected: int
    overdue: int


class ManagerCard(BaseModel):
    public_id: str
    number: str
    omnidesk_ticket_number: str
    status: str
    status_label: str
    planned_start_at: datetime
    planned_end_at: datetime
    planned_duration_minutes: int
    l1_owner_name: str | None
    l2_engineer_name: str | None
    urgent: bool
    overdue: bool
    out_of_hours: bool


class ManagerCardsResponse(BaseModel):
    summary: ManagerSummary
    items: list[ManagerCard]
    limit: int


class ManagerFilters(BaseModel):
    status: int | None = None
    period_from: datetime | None = None
    period_to: datetime | None = None
    limit: int = Field(default=100, ge=1, le=200)

    @field_validator("status", mode="before")
    @classmethod
    def parse_status(cls, value: str | int | None) -> int | None:
        if value in (None, ""):
            return None
        try:
            return next(
                int(item) for item in CardStatus if status_slug(item).value == value
            )
        except (StopIteration, TypeError) as exc:
            raise ValueError("invalid_status") from exc

    @field_validator("period_from", "period_to")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timezone_required")
        return value

    @model_validator(mode="after")
    def validate_period(self) -> "ManagerFilters":
        if (
            self.period_from is not None
            and self.period_to is not None
            and self.period_from >= self.period_to
        ):
            raise ValueError("period_from_must_be_before_period_to")
        return self


def get_manager_repository(
    connection: Annotated[object, Depends(get_db)],
) -> CardRepository:
    return PostgresCardRepository(connection)


@router.get("/cards", response_model=ManagerCardsResponse)
def list_manager_cards(
    _: Annotated[UserAuthRecord, Depends(require_roles(int(RoleId.MANAGER)))],
    repository: Annotated[CardRepository, Depends(get_manager_repository)],
    filters: Annotated[ManagerFilters, Depends()],
) -> ManagerCardsResponse:
    rows, counts = repository.list_manager_cards(
        status_code=filters.status,
        period_from=filters.period_from,
        period_to=filters.period_to,
        limit=filters.limit,
    )
    return ManagerCardsResponse(
        summary=ManagerSummary(**counts),
        items=[
            ManagerCard(
                public_id=str(card.public_id),
                number=card.number,
                omnidesk_ticket_number=card.omnidesk_ticket_number,
                status=status_slug(card.status_code).value,
                status_label=status_label(card.status_code),
                planned_start_at=card.planned_start_at,
                planned_end_at=card.planned_start_at
                + timedelta(minutes=card.planned_duration_minutes),
                planned_duration_minutes=card.planned_duration_minutes,
                l1_owner_name=card.l1_owner_name,
                l2_engineer_name=card.l2_engineer_name,
                urgent=card.urgency_code > 0,
                overdue=card.overdue_flag,
                out_of_hours=card.out_of_hours_flag,
            )
            for card in rows
        ],
        limit=filters.limit,
    )

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from collections.abc import Generator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel

from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import CardStatus, CardStatusSlug, RoleId, status_slug
from app.cards.repository import CardRepository, PostgresCardRepository
from app.cards.schemas import status_label
from app.db import db_connection

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


@dataclass(frozen=True)
class ManagerFilters:
    status: CardStatusSlug | None
    period_from: datetime | None
    period_to: datetime | None
    limit: int


@dataclass(frozen=True)
class ManagerAccessContext:
    user: UserAuthRecord
    filters: ManagerFilters


def get_manager_filters(
    status: CardStatusSlug | None = Query(default=None),
    period_from: datetime | None = Query(default=None),
    period_to: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> ManagerFilters:
    if period_from is not None and (
        period_from.tzinfo is None or period_from.utcoffset() is None
    ):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_from_timezone_required",
        )
    if period_to is not None and (
        period_to.tzinfo is None or period_to.utcoffset() is None
    ):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_to_timezone_required",
        )
    if period_from is not None and period_to is not None and period_from >= period_to:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_from_must_be_before_period_to",
        )
    return ManagerFilters(status, period_from, period_to, limit)


require_manager_role = require_roles(int(RoleId.MANAGER))


def get_manager_context(
    user: Annotated[UserAuthRecord, Depends(require_manager_role)],
    filters: Annotated[ManagerFilters, Depends(get_manager_filters)],
) -> ManagerAccessContext:
    return ManagerAccessContext(user=user, filters=filters)


def get_manager_repository(
    _context: Annotated[ManagerAccessContext, Depends(get_manager_context)],
) -> Generator[CardRepository, None, None]:
    with db_connection() as connection:
        yield PostgresCardRepository(connection)


@router.get("/cards", response_model=ManagerCardsResponse)
def list_manager_cards(
    repository: Annotated[CardRepository, Depends(get_manager_repository)],
    context: Annotated[ManagerAccessContext, Depends(get_manager_context)],
) -> ManagerCardsResponse:
    rows, counts = repository.list_manager_cards(
        status_code=(
            next(item for item in CardStatus if status_slug(item) == context.filters.status)
            if context.filters.status is not None
            else None
        ),
        period_from=context.filters.period_from,
        period_to=context.filters.period_to,
        limit=context.filters.limit,
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
        limit=context.filters.limit,
    )

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status

from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import RoleId
from app.cards.repository import CardRepository, PostgresCardRepository
from app.cards.schemas import status_label, status_slug
from app.db import get_db

from pydantic import BaseModel

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


def get_manager_repository(connection: Annotated[object, Depends(get_db)]) -> CardRepository:
    return PostgresCardRepository(connection)


@router.get("/cards", response_model=ManagerCardsResponse)
def list_manager_cards(
    _: Annotated[UserAuthRecord, Depends(require_roles(int(RoleId.MANAGER)))],
    repository: Annotated[CardRepository, Depends(get_manager_repository)],
    status: str | None = Query(default=None),
    period_from: datetime | None = Query(default=None),
    period_to: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> ManagerCardsResponse:
    try:
        rows, counts = repository.list_manager_cards(
            status=status, period_from=period_from, period_to=period_to, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_status") from exc
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
                planned_end_at=card.planned_start_at + timedelta(minutes=card.planned_duration_minutes),
                planned_duration_minutes=card.planned_duration_minutes,
                l1_owner_name=card.l1_owner_name,
                l2_engineer_name=card.l2_engineer_name,
                urgent=card.urgency_code > 0,
                overdue=card.overdue_flag,
                out_of_hours=card.out_of_hours_flag,
            )
            for card in rows
        ],
        limit=limit,
    )

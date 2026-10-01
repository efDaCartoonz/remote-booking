from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from pydantic import BaseModel

from app.auth.dependencies import require_roles
from app.auth.store import UserAuthRecord
from app.cards.constants import RoleId
from app.db import db_connection
from app.reports.service import PostgresReportsRepository, ReportsService

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

require_reports_access = require_roles(int(RoleId.MANAGER), int(RoleId.ADMIN))


class ShareReportResponse(BaseModel):
    numerator: int
    denominator: int
    value: float | None = None


class CountReportResponse(BaseModel):
    count: int


class SummaryReportResponse(BaseModel):
    created: int
    completed: int
    rejected_share: ShareReportResponse
    repeat_rejected_share: ShareReportResponse
    overdue: CountReportResponse
    urgent: CountReportResponse
    urgent_collisions: CountReportResponse


class OverdueCardResponseItem(BaseModel):
    public_id: str
    number: str
    status: str
    status_label: str
    planned_start_at: datetime


class OverdueCardsResponse(BaseModel):
    items: list[OverdueCardResponseItem]
    total: int
    limit: int
    offset: int


class L2LoadResponseItem(BaseModel):
    user_id: int
    full_name: str
    assigned: int
    completed: int
    planned_minutes: int


class L2LoadResponse(BaseModel):
    items: list[L2LoadResponseItem]


def validate_report_period(
    period_from: Annotated[datetime, Query(alias="from")],
    period_to: Annotated[datetime, Query(alias="to")],
) -> tuple[datetime, datetime]:
    if period_from.tzinfo is None or period_from.utcoffset() is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_from_timezone_required",
        )
    if period_to.tzinfo is None or period_to.utcoffset() is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_to_timezone_required",
        )
    if period_from >= period_to:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_from_must_be_before_period_to",
        )
    if period_to - period_from > timedelta(days=366):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_range_exceeds_maximum_366_days",
        )
    return period_from, period_to


def get_reports_service() -> Generator[ReportsService, None, None]:
    with db_connection() as connection:
        yield ReportsService(PostgresReportsRepository(connection))


@router.get("/summary", response_model=SummaryReportResponse)
def get_reports_summary(
    period: Annotated[tuple[datetime, datetime], Depends(validate_report_period)],
    _user: Annotated[UserAuthRecord, Depends(require_reports_access)],
    service: Annotated[ReportsService, Depends(get_reports_service)],
) -> SummaryReportResponse:
    period_from, period_to = period
    summary = service.get_summary(period_from=period_from, period_to=period_to)
    return SummaryReportResponse(
        created=summary.created,
        completed=summary.completed,
        rejected_share=ShareReportResponse(
            numerator=summary.rejected_share.numerator,
            denominator=summary.rejected_share.denominator,
            value=summary.rejected_share.value,
        ),
        repeat_rejected_share=ShareReportResponse(
            numerator=summary.repeat_rejected_share.numerator,
            denominator=summary.repeat_rejected_share.denominator,
            value=summary.repeat_rejected_share.value,
        ),
        overdue=CountReportResponse(count=summary.overdue.count),
        urgent=CountReportResponse(count=summary.urgent.count),
        urgent_collisions=CountReportResponse(count=summary.urgent_collisions.count),
    )


@router.get("/overdue", response_model=OverdueCardsResponse)
def get_reports_overdue(
    period: Annotated[tuple[datetime, datetime], Depends(validate_report_period)],
    _user: Annotated[UserAuthRecord, Depends(require_reports_access)],
    service: Annotated[ReportsService, Depends(get_reports_service)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OverdueCardsResponse:
    period_from, period_to = period
    items, total = service.get_overdue_cards(
        period_from=period_from,
        period_to=period_to,
        limit=limit,
        offset=offset,
    )
    return OverdueCardsResponse(
        items=[
            OverdueCardResponseItem(
                public_id=item.public_id,
                number=item.number,
                status=item.status,
                status_label=item.status_label,
                planned_start_at=item.planned_start_at,
            )
            for item in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/l2-load", response_model=L2LoadResponse)
def get_reports_l2_load(
    period: Annotated[tuple[datetime, datetime], Depends(validate_report_period)],
    _user: Annotated[UserAuthRecord, Depends(require_reports_access)],
    service: Annotated[ReportsService, Depends(get_reports_service)],
) -> L2LoadResponse:
    period_from, period_to = period
    items = service.get_l2_load(period_from=period_from, period_to=period_to)
    return L2LoadResponse(
        items=[
            L2LoadResponseItem(
                user_id=item.user_id,
                full_name=item.full_name,
                assigned=item.assigned,
                completed=item.completed,
                planned_minutes=item.planned_minutes,
            )
            for item in items
        ]
    )

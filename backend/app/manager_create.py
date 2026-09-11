from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TypeVar

from psycopg.errors import ExclusionViolation, UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.cards.schemas import TicketNumber


class ManagerAssignmentMethod(str, Enum):
    AUTO = "auto"


class ManagerCreateConflictError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


T = TypeVar("T")


def run_manager_create_transaction(
    operation: Callable[[], T], *, rollback: Callable[[], None]
) -> T:
    try:
        return operation()
    except ExclusionViolation as exc:
        constraint_name = getattr(getattr(exc, "diag", None), "constraint_name", None)
        if constraint_name != "ex_connection_cards_l2_no_overlap":
            raise
        rollback()
        raise ManagerCreateConflictError("l2_assignment_conflict") from None
    except UniqueViolation as exc:
        constraint_name = getattr(getattr(exc, "diag", None), "constraint_name", None)
        if constraint_name != "ux_connection_cards_one_active_per_ticket":
            raise
        rollback()
        raise ManagerCreateConflictError("active_card_exists_for_ticket") from None


class ManagerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=32, pattern=r"^\d+$")
    case_number: TicketNumber
    planned_start_at: datetime
    planned_duration_minutes: int = Field(default=60, ge=30, le=720)
    description: str | None = None
    assignment_method: ManagerAssignmentMethod | None = ManagerAssignmentMethod.AUTO
    l2_user_id: int | None = Field(default=None, gt=0)

    @field_validator("planned_start_at")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("planned_start_at_must_be_timezone_aware")
        return value.astimezone(UTC)

    @field_validator("assignment_method")
    @classmethod
    def assignment_method_is_explicit(cls, value):
        return value

    @model_validator(mode="after")
    def one_assignment_method(self):
        if (
            self.l2_user_id is None
            and self.assignment_method != ManagerAssignmentMethod.AUTO
        ):
            raise ValueError("assignment_method_and_l2_user_id_mismatch")
        return self


class ManagerTicketPreflightResponse(BaseModel):
    case_id: str
    case_number: str
    status: str
    client_display_name: str | None
    can_create: bool


class ManagerL2Option(BaseModel):
    user_id: int
    display_name: str
    available: bool
    reason_code: str | None


class ManagerL2OptionsResponse(BaseModel):
    items: list[ManagerL2Option]


def validate_manager_window(start: datetime, duration: int) -> None:
    now = datetime.now(UTC)
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("planned_start_at_must_be_timezone_aware")
    if start < now + timedelta(minutes=120):
        raise ValueError("planned_start_too_soon")
    if start > now + timedelta(days=14):
        raise ValueError("planned_start_too_far")

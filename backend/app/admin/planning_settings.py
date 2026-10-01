from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.admin.repository import AdministrativeRepository


@dataclass(frozen=True)
class PlanningSettings:
    min_lead_minutes: int = 120
    horizon_days: int = 14
    default_duration_minutes: int = 60
    min_duration_minutes: int = 30
    max_duration_minutes: int = 720

    def validate(self) -> None:
        if self.min_lead_minutes <= 0:
            raise ValueError("min_lead_minutes_must_be_positive")
        if self.horizon_days <= 0:
            raise ValueError("horizon_days_must_be_positive")
        if self.min_duration_minutes <= 0:
            raise ValueError("min_duration_minutes_must_be_positive")
        if self.default_duration_minutes <= 0:
            raise ValueError("default_duration_minutes_must_be_positive")
        if self.max_duration_minutes <= 0:
            raise ValueError("max_duration_minutes_must_be_positive")
        if not (
            self.min_duration_minutes
            <= self.default_duration_minutes
            <= self.max_duration_minutes
        ):
            raise ValueError("duration_bounds_inconsistent")

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PlanningSettings:
        if not data:
            return cls()
        return cls(
            min_lead_minutes=int(data.get("min_lead_minutes", 120)),
            horizon_days=int(data.get("horizon_days", 14)),
            default_duration_minutes=int(data.get("default_duration_minutes", 60)),
            min_duration_minutes=int(data.get("min_duration_minutes", 30)),
            max_duration_minutes=int(data.get("max_duration_minutes", 720)),
        )


DEFAULT_PLANNING_SETTINGS = PlanningSettings()


def get_planning_settings(
    source: AdministrativeRepository | Any | None = None,
) -> PlanningSettings:
    """Retrieve planning settings with fallback to safe defaults.

    Accepts an AdministrativeRepository, a psycopg Connection, or None.
    If no source or settings record is available, returns DEFAULT_PLANNING_SETTINGS.
    """
    if source is None:
        return DEFAULT_PLANNING_SETTINGS
    if isinstance(source, AdministrativeRepository):
        return PlanningSettings.from_dict(source.get_planning_settings())
    if hasattr(source, "cursor"):
        return PlanningSettings.from_dict(
            AdministrativeRepository(source).get_planning_settings()
        )
    return DEFAULT_PLANNING_SETTINGS


def validate_duration(minutes: int, settings: PlanningSettings) -> None:
    if not (settings.min_duration_minutes <= minutes <= settings.max_duration_minutes):
        raise ValueError("planned_duration_out_of_range")

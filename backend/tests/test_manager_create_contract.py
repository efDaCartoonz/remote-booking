from datetime import UTC, datetime, timedelta

import pytest
from app.manager_create import ManagerCreateRequest, validate_manager_window
from pydantic import ValidationError


def valid_payload(**overrides):
    payload = {
        "case_id": "123",
        "case_number": "123-456789",
        "planned_start_at": datetime.now(UTC) + timedelta(hours=3),
        "assignment_method": "auto",
    }
    payload.update(overrides)
    return payload


def test_manager_schema_forbids_server_fields() -> None:
    with pytest.raises(ValidationError):
        ManagerCreateRequest(**valid_payload(client_id=42, status_code=1))


def test_manager_schema_accepts_manual_l2_without_pool_flag() -> None:
    request = ManagerCreateRequest(**valid_payload(l2_user_id=42))
    assert request.l2_user_id == 42
    assert request.planned_start_at.tzinfo is UTC


def test_manager_schema_requires_timezone_aware_start() -> None:
    with pytest.raises(ValidationError):
        ManagerCreateRequest(
            **valid_payload(
                planned_start_at=datetime(2026, 9, 11, 12, 0, tzinfo=UTC).replace(
                    tzinfo=None
                )
            )
        )


@pytest.mark.parametrize("minutes", [29, 721])
def test_manager_schema_rejects_invalid_duration(minutes: int) -> None:
    with pytest.raises(ValidationError):
        ManagerCreateRequest(**valid_payload(planned_duration_minutes=minutes))


def test_manager_window_rejects_too_soon_and_too_far() -> None:
    with pytest.raises(ValueError, match="planned_start_too_soon"):
        validate_manager_window(datetime.now(UTC) + timedelta(minutes=119), 60)
    with pytest.raises(ValueError, match="planned_start_too_far"):
        validate_manager_window(datetime.now(UTC) + timedelta(days=15), 60)

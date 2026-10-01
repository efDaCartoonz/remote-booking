from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.reports import (
    get_reports_service,
    router,
    validate_report_period,
)
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import RoleId
from app.reports.service import (
    CountReport,
    L2LoadItem,
    OverdueCardItem,
    ReportsService,
    ShareReport,
    SummaryReport,
    calculate_share,
)


def test_calculate_share_division_by_zero() -> None:
    assert calculate_share(0, 0) is None
    assert calculate_share(5, 0) is None


def test_calculate_share_normal() -> None:
    assert calculate_share(1, 3) == 0.3333
    assert calculate_share(2, 4) == 0.5
    assert calculate_share(0, 10) == 0.0
    assert calculate_share(10, 10) == 1.0


def test_validate_report_period_success() -> None:
    period_from = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    period_to = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    res_from, res_to = validate_report_period(period_from, period_to)
    assert res_from == period_from
    assert res_to == period_to


def test_validate_report_period_boundary_366_days() -> None:
    period_from = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    period_to = period_from + timedelta(days=366)
    res_from, res_to = validate_report_period(period_from, period_to)
    assert res_to - res_from == timedelta(days=366)


def test_validate_report_period_naive_timezone() -> None:
    naive_from = datetime(2026, 1, 1, 0, 0)
    aware_to = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)

    with pytest.raises(HTTPException) as exc_info:
        validate_report_period(naive_from, aware_to)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "period_from_timezone_required"

    aware_from = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    naive_to = datetime(2026, 1, 10, 0, 0)
    with pytest.raises(HTTPException) as exc_info:
        validate_report_period(aware_from, naive_to)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "period_to_timezone_required"


def test_validate_report_period_inverted() -> None:
    period_from = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    period_to = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    with pytest.raises(HTTPException) as exc_info:
        validate_report_period(period_from, period_to)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "period_from_must_be_before_period_to"

    equal_to = datetime(2026, 1, 10, 0, 0, tzinfo=UTC)
    with pytest.raises(HTTPException) as exc_info:
        validate_report_period(period_from, equal_to)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "period_from_must_be_before_period_to"


def test_validate_report_period_exceeds_366_days() -> None:
    period_from = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    period_to = period_from + timedelta(days=367)

    with pytest.raises(HTTPException) as exc_info:
        validate_report_period(period_from, period_to)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "period_range_exceeds_maximum_366_days"


class FakeReportsRepository:
    def get_summary(self, period_from: datetime, period_to: datetime) -> SummaryReport:
        return SummaryReport(
            created=10,
            completed=5,
            rejected_share=ShareReport(numerator=2, denominator=10, value=0.2),
            repeat_rejected_share=ShareReport(numerator=1, denominator=2, value=0.5),
            overdue=CountReport(count=3),
            urgent=CountReport(count=4),
            urgent_collisions=CountReport(count=1),
        )

    def get_overdue_cards(
        self,
        period_from: datetime,
        period_to: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[OverdueCardItem], int]:
        return (
            [
                OverdueCardItem(
                    public_id="11111111-1111-1111-1111-111111111111",
                    number="RDM-000001",
                    status="assigned",
                    status_label="Назначено",
                    planned_start_at=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
                )
            ],
            1,
        )

    def get_l2_load(
        self, period_from: datetime, period_to: datetime
    ) -> list[L2LoadItem]:
        return [
            L2LoadItem(
                user_id=101,
                full_name="Инженер Л2",
                assigned=5,
                completed=4,
                planned_minutes=300,
            )
        ]


def _create_test_app(user_roles: tuple[RoleId, ...] | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    fake_service = ReportsService(FakeReportsRepository())
    app.dependency_overrides[get_reports_service] = lambda: fake_service

    if user_roles is not None:
        app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
            id=1,
            username="test_user",
            password_hash="test",
            full_name="Test User",
            email=None,
            roles=tuple(
                RoleRecord(id=int(role), name=role.name) for role in user_roles
            ),
        )

    return TestClient(app)


def test_reports_endpoints_forbidden_for_l1_and_l2() -> None:
    for role in (RoleId.L1, RoleId.L2):
        client = _create_test_app(user_roles=(role,))
        for path in (
            "/api/v1/reports/summary",
            "/api/v1/reports/overdue",
            "/api/v1/reports/l2-load",
        ):
            resp = client.get(
                f"{path}?from=2026-01-01T00:00:00Z&to=2026-01-10T00:00:00Z"
            )
            assert resp.status_code == 403
            assert resp.json() == {"detail": "insufficient_role"}


def test_reports_endpoints_allowed_for_manager_and_admin() -> None:
    for role in (RoleId.MANAGER, RoleId.ADMIN):
        client = _create_test_app(user_roles=(role,))

        # 1. Summary
        resp = client.get(
            "/api/v1/reports/summary?from=2026-01-01T00:00:00Z&to=2026-01-10T00:00:00Z"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 10
        assert data["completed"] == 5
        assert data["rejected_share"] == {
            "numerator": 2,
            "denominator": 10,
            "value": 0.2,
        }
        assert data["repeat_rejected_share"] == {
            "numerator": 1,
            "denominator": 2,
            "value": 0.5,
        }
        assert data["overdue"] == {"count": 3}
        assert data["urgent"] == {"count": 4}
        assert data["urgent_collisions"] == {"count": 1}
        assert "case_id" not in resp.text

        # 2. Overdue
        resp = client.get(
            "/api/v1/reports/overdue?from=2026-01-01T00:00:00Z&to=2026-01-10T00:00:00Z&limit=10&offset=0"
        )
        assert resp.status_code == 200
        overdue_data = resp.json()
        assert overdue_data["total"] == 1
        assert len(overdue_data["items"]) == 1
        assert overdue_data["items"][0]["number"] == "RDM-000001"
        assert overdue_data["items"][0]["status"] == "assigned"
        assert overdue_data["items"][0]["status_label"] == "Назначено"
        assert "case_id" not in resp.text

        # 3. L2 load
        resp = client.get(
            "/api/v1/reports/l2-load?from=2026-01-01T00:00:00Z&to=2026-01-10T00:00:00Z"
        )
        assert resp.status_code == 200
        l2_data = resp.json()
        assert len(l2_data["items"]) == 1
        assert l2_data["items"][0]["user_id"] == 101
        assert l2_data["items"][0]["full_name"] == "Инженер Л2"
        assert l2_data["items"][0]["assigned"] == 5
        assert l2_data["items"][0]["completed"] == 4
        assert l2_data["items"][0]["planned_minutes"] == 300
        assert "case_id" not in resp.text


def test_reports_endpoints_validation_errors() -> None:
    client = _create_test_app(user_roles=(RoleId.MANAGER,))

    # Naive date (no timezone offset)
    resp = client.get(
        "/api/v1/reports/summary?from=2026-01-01T00:00:00&to=2026-01-10T00:00:00Z"
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "period_from_timezone_required"

    # Inverted date
    resp = client.get(
        "/api/v1/reports/summary?from=2026-01-10T00:00:00Z&to=2026-01-01T00:00:00Z"
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "period_from_must_be_before_period_to"

    # Exceeding 366 days
    resp = client.get(
        "/api/v1/reports/summary?from=2025-01-01T00:00:00Z&to=2026-01-10T00:00:00Z"
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "period_range_exceeds_maximum_366_days"

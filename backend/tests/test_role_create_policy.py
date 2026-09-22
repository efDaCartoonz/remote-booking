from datetime import UTC, datetime, timedelta

import pytest

from app.cards.constants import AssignmentMethod, CardStatus
from app.cards.create_policy import (
    CreateScenario,
    validate_reschedule_window,
    validate_role_create,
)
from app.cards.policy import CardActionPolicyError, authorize_create


NOW = datetime(2026, 9, 17, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    ("scenario", "roles"),
    [
        (CreateScenario.L1, {1}),
        (CreateScenario.L2_SELF, {2}),
        (CreateScenario.L2_URGENT, {2}),
        (CreateScenario.L2_RETROACTIVE, {2}),
    ],
)
def test_role_create_policy_allows_only_the_business_role(scenario, roles) -> None:
    authorize_create(actor_role_ids=roles, scenario=scenario)


@pytest.mark.parametrize("scenario", list(CreateScenario))
def test_admin_does_not_gain_business_create_rights(scenario) -> None:
    with pytest.raises(CardActionPolicyError, match="action_forbidden"):
        authorize_create(actor_role_ids={4}, scenario=scenario)


@pytest.mark.parametrize(
    "scenario",
    [CreateScenario.L2_SELF, CreateScenario.L2_URGENT, CreateScenario.L2_RETROACTIVE],
)
def test_l1_cannot_use_l2_create_contracts(scenario) -> None:
    with pytest.raises(CardActionPolicyError, match="action_forbidden"):
        authorize_create(actor_role_ids={1}, scenario=scenario)


def test_normal_create_window_and_default_assignment_plan() -> None:
    l1_plan = validate_role_create(
        scenario=CreateScenario.L1,
        planned_start_at=NOW + timedelta(hours=3),
        planned_duration_minutes=60,
        now=NOW,
    )
    l2_plan = validate_role_create(
        scenario=CreateScenario.L2_SELF,
        planned_start_at=NOW + timedelta(hours=3),
        planned_duration_minutes=60,
        now=NOW,
    )
    assert l1_plan.status == CardStatus.CREATED
    assert l1_plan.assignment_method_code == int(AssignmentMethod.AUTO)
    assert l2_plan.status == CardStatus.ASSIGNED
    assert l2_plan.assignment_method_code == int(AssignmentMethod.L2_SELF)


@pytest.mark.parametrize(
    ("start", "detail"),
    [
        (NOW + timedelta(minutes=119), "planned_start_too_soon"),
        (NOW + timedelta(days=15), "planned_start_too_far"),
    ],
)
def test_normal_create_rejects_invalid_window(start, detail) -> None:
    with pytest.raises(ValueError, match=detail):
        validate_role_create(
            scenario=CreateScenario.L2_SELF,
            planned_start_at=start,
            planned_duration_minutes=60,
            now=NOW,
        )


def test_reschedule_uses_normal_create_window() -> None:
    validate_reschedule_window(
        planned_start_at=NOW + timedelta(hours=2), urgency_code=0, now=NOW
    )
    with pytest.raises(ValueError, match="planned_start_too_soon"):
        validate_reschedule_window(
            planned_start_at=NOW + timedelta(minutes=119),
            urgency_code=0,
            now=NOW,
        )
    with pytest.raises(ValueError, match="planned_start_too_far"):
        validate_reschedule_window(
            planned_start_at=NOW + timedelta(days=15), urgency_code=0, now=NOW
        )


def test_urgent_reschedule_keeps_create_exception_to_minimum_delay() -> None:
    validate_reschedule_window(
        planned_start_at=NOW + timedelta(minutes=5), urgency_code=1, now=NOW
    )
    with pytest.raises(ValueError, match="urgent_planned_start_must_not_be_in_past"):
        validate_reschedule_window(
            planned_start_at=NOW - timedelta(minutes=1), urgency_code=1, now=NOW
        )


def test_reschedule_requires_timezone_aware_start() -> None:
    with pytest.raises(ValueError, match="planned_start_at_must_be_timezone_aware"):
        validate_reschedule_window(
            planned_start_at=datetime(2026, 9, 17, 12), urgency_code=0, now=NOW
        )


def test_urgent_requires_reason_but_not_120_minute_delay() -> None:
    with pytest.raises(ValueError, match="urgent_reason_required"):
        validate_role_create(
            scenario=CreateScenario.L2_URGENT,
            planned_start_at=NOW + timedelta(minutes=5),
            planned_duration_minutes=60,
            now=NOW,
        )
    plan = validate_role_create(
        scenario=CreateScenario.L2_URGENT,
        planned_start_at=NOW + timedelta(minutes=5),
        planned_duration_minutes=60,
        urgent_reason="production incident",
        now=NOW,
    )
    assert plan.status == CardStatus.ASSIGNED
    assert plan.urgency_code == 1
    assert plan.urgent_reason == "production incident"


def test_retroactive_completed_requires_result_and_report() -> None:
    kwargs = {
        "scenario": CreateScenario.L2_RETROACTIVE,
        "planned_start_at": NOW - timedelta(hours=2),
        "planned_duration_minutes": 60,
        "now": NOW,
    }
    with pytest.raises(ValueError, match="retroactive_result_required"):
        validate_role_create(**kwargs)
    with pytest.raises(ValueError, match="retroactive_engineer_report_required"):
        validate_role_create(**kwargs, result_code=0)
    plan = validate_role_create(
        **kwargs, result_code=0, engineer_report="Restored service"
    )
    assert plan.status == CardStatus.COMPLETED
    assert plan.assignment_method_code == int(AssignmentMethod.RETROACTIVE)
    assert plan.actual_start_at == NOW - timedelta(hours=2)
    assert plan.actual_end_at == NOW - timedelta(hours=1)


def test_retroactive_in_progress_does_not_require_completion_fields() -> None:
    plan = validate_role_create(
        scenario=CreateScenario.L2_RETROACTIVE,
        planned_start_at=NOW - timedelta(minutes=20),
        planned_duration_minutes=60,
        now=NOW,
    )
    assert plan.status == CardStatus.IN_PROGRESS
    assert plan.actual_start_at == NOW - timedelta(minutes=20)
    assert plan.actual_end_at is None


def test_retroactive_cannot_be_used_for_future_booking() -> None:
    with pytest.raises(ValueError, match="retroactive_planned_start_must_be_in_past"):
        validate_role_create(
            scenario=CreateScenario.L2_RETROACTIVE,
            planned_start_at=NOW + timedelta(minutes=1),
            planned_duration_minutes=60,
            now=NOW,
        )

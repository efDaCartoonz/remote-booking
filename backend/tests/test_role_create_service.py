from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

from app.assignments.types import L2DistributionCandidate, ScheduleWindow
from app.cards.constants import CardStatus
from app.cards.create_policy import CreateScenario, validate_role_create
from app.cards.repository import CardRecord, CreateCardData
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService


NOW = datetime(2026, 9, 17, 10, tzinfo=UTC)


class RecordingRepository:
    def __init__(self) -> None:
        self.created: CreateCardData | None = None
        self.events: list[dict] = []
        self.audit: list[dict] = []

    def create_card(self, data: CreateCardData) -> CardRecord:
        self.created = data
        return CardRecord(
            id=1,
            public_id=uuid4(),
            number="RDM-000001",
            omnidesk_ticket_number=data.omnidesk_ticket_number,
            client_id=data.client_id,
            status_code=int(data.status),
            criticality_code=data.criticality_code,
            urgency_code=data.urgency_code,
            planned_start_at=data.planned_start_at,
            planned_duration_minutes=data.planned_duration_minutes,
            client_timezone_at_creation=None,
            timezone_source_code=None,
            actual_start_at=data.actual_start_at,
            actual_end_at=data.actual_end_at,
            l1_owner_id=data.l1_owner_id,
            l2_engineer_id=data.l2_engineer_id,
            assignment_method_code=data.assignment_method_code,
            unsuccessful_cycle_count=0,
            client_contact_type_code=None,
            client_contact_value=None,
            description=data.description,
            urgent_reason=data.urgent_reason,
            out_of_hours_flag=False,
            retroactive_flag=data.retroactive_flag,
            overdue_flag=False,
            result_code=data.result_code,
            engineer_report=data.engineer_report,
            created_source_code=data.created_source_code,
            created_by_id=data.created_by_id,
            created_at=NOW,
            updated_at=NOW,
        )

    def add_card_event(self, **kwargs):
        self.events.append(kwargs)
        return 1

    def add_audit_log(self, **kwargs):
        self.audit.append(kwargs)

    def list_all_l2_candidates(self, **_kwargs):
        return [
            L2DistributionCandidate(
                user_id=42,
                schedules=tuple(
                    ScheduleWindow(
                        weekday=weekday,
                        start_time=time.min,
                        end_time=time.max,
                        timezone="UTC",
                        valid_from=None,
                        valid_to=None,
                    )
                    for weekday in range(1, 8)
                ),
                absences=(),
                active_cards=(),
            )
        ]


def _payload(start: datetime) -> CardCreateRequest:
    return CardCreateRequest(
        omnidesk_ticket_number="123-456789",
        planned_start_at=start,
        planned_duration_minutes=60,
        client_id=7,
    )


def test_l2_role_plan_sets_self_as_owner_and_actual_actor_in_audit() -> None:
    repository = RecordingRepository()
    plan = validate_role_create(
        scenario=CreateScenario.L2_SELF,
        planned_start_at=NOW + timedelta(hours=3),
        planned_duration_minutes=60,
        now=NOW,
    )
    card = CardService(repository).create_card(
        _payload(NOW + timedelta(hours=3)),
        actor_user_id=42,
        ip_address="127.0.0.1",
        user_agent="test",
        role_create_plan=plan,
    )
    assert card.status_code == int(CardStatus.ASSIGNED)
    assert repository.created is not None
    assert repository.created.l2_engineer_id == 42
    assert repository.created.created_by_id == 42
    assert repository.audit[0]["actor_user_id"] == 42
    assert repository.events[0]["actor_user_id"] == 42


def test_completed_retroactive_plan_persists_completion_facts_without_distribution() -> (
    None
):
    repository = RecordingRepository()
    plan = validate_role_create(
        scenario=CreateScenario.L2_RETROACTIVE,
        planned_start_at=NOW - timedelta(hours=2),
        planned_duration_minutes=60,
        result_code=0,
        engineer_report="Service restored",
        now=NOW,
    )
    card = CardService(repository).create_card(
        _payload(NOW - timedelta(hours=2)),
        actor_user_id=42,
        ip_address=None,
        user_agent=None,
        role_create_plan=plan,
    )
    assert card.status_code == int(CardStatus.COMPLETED)
    assert repository.created is not None
    assert repository.created.l2_engineer_id == 42
    assert repository.created.actual_start_at == NOW - timedelta(hours=2)
    assert repository.created.actual_end_at == NOW - timedelta(hours=1)
    assert repository.created.result_code == 0
    assert repository.created.engineer_report == "Service restored"

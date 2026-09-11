from __future__ import annotations

from datetime import time, timedelta

from app.assignments.types import TimeInterval
from app.cards.constants import (
    AssignmentAttemptStatus,
    AssignmentCycleStatus,
    AuditAction,
    CardEventType,
    CardStatus,
)
from app.cards.service import CardService
from test_cards import (
    DEFAULT_PLANNED_START_AT,
    FakeCardRepository,
    create_payload,
    seed_l2_candidate,
)


def test_initial_l2_distribution_assigns_available_candidate() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = CardService(repository)

    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == 20
    assert repository.distribution_last_user_id == 20
    assert len(repository.cycles) == 1
    assert repository.cycles[0].status_code == int(AssignmentCycleStatus.ASSIGNED)
    assert len(repository.attempts) == 1
    assert repository.attempts[0].l2_engineer_id == 20
    assert repository.attempts[0].status_code == int(AssignmentAttemptStatus.PENDING)
    assert repository.events[-1]["event_type"] == CardEventType.ENGINEER_ASSIGNED
    assert [audit["entity_type"] for audit in repository.audit] == [
        "connection_card",
        "assignment_cycle",
        "assignment_attempt",
        "connection_card",
    ]
    assert [audit["action"] for audit in repository.audit] == [
        AuditAction.CREATE,
        AuditAction.CREATE,
        AuditAction.CREATE,
        AuditAction.UPDATE,
    ]


def test_initial_l2_distribution_rejects_card_when_no_candidates() -> None:
    repository = FakeCardRepository()
    service = CardService(repository)

    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.REJECTED)
    assert card.l2_engineer_id is None
    assert card.unsuccessful_cycle_count == 1
    assert len(repository.cycles) == 1
    assert repository.cycles[0].status_code == int(AssignmentCycleStatus.ALL_REJECTED)
    assert repository.attempts == []
    assert repository.events[-1]["comment"] == "no_available_l2_candidates"


def test_initial_l2_distribution_excludes_l2_outside_schedule() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(
        repository,
        20,
        schedule_start=time(0, 0),
        schedule_end=time(1, 0),
    )
    seed_l2_candidate(repository, 30)
    service = CardService(repository)

    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == 30
    assert [attempt.l2_engineer_id for attempt in repository.attempts] == [30]


def test_initial_l2_distribution_excludes_absent_l2() -> None:
    repository = FakeCardRepository()
    planned_start = DEFAULT_PLANNED_START_AT
    seed_l2_candidate(
        repository,
        20,
        planned_start_at=planned_start,
        absence=TimeInterval(
            start_at=planned_start - timedelta(minutes=15),
            end_at=planned_start + timedelta(minutes=15),
        ),
    )
    seed_l2_candidate(repository, 30, planned_start_at=planned_start)
    service = CardService(repository)

    card = service.create_card(
        create_payload(planned_start_at=planned_start),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == 30
    assert [attempt.l2_engineer_id for attempt in repository.attempts] == [30]


def test_initial_l2_distribution_excludes_l2_with_active_overlap() -> None:
    repository = FakeCardRepository()
    planned_start = DEFAULT_PLANNED_START_AT
    seed_l2_candidate(repository, 20, planned_start_at=planned_start)
    seed_l2_candidate(repository, 30, planned_start_at=planned_start)
    service = CardService(repository)
    service.create_card(
        create_payload(
            l2_engineer_id=20,
            omnidesk_ticket_number="999-000001",
            planned_start_at=planned_start,
        ),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    card = service.create_card(
        create_payload(planned_start_at=planned_start),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == 30
    assert [attempt.l2_engineer_id for attempt in repository.attempts] == [30]


def test_initial_l2_distribution_uses_round_robin_after_last_user() -> None:
    repository = FakeCardRepository()
    repository.distribution_last_user_id = 20
    seed_l2_candidate(repository, 10)
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    service = CardService(repository)

    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == 30
    assert repository.distribution_last_user_id == 30


def test_initial_l2_distribution_round_robin_wraps_to_first_user() -> None:
    repository = FakeCardRepository()
    repository.distribution_last_user_id = 30
    seed_l2_candidate(repository, 10)
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    service = CardService(repository)

    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == 10
    assert repository.distribution_last_user_id == 10

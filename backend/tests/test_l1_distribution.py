from __future__ import annotations

from dataclasses import replace
from datetime import time, timedelta

from test_cards import (
    DEFAULT_PLANNED_START_AT,
    FakeCardRepository,
    create_payload,
    seed_l1_candidate,
    seed_l2_candidate,
)

from app.assignments.l1_service import L1DistributionService
from app.assignments.types import TimeInterval
from app.cards.constants import AssignmentAttemptStatus, CardStatus
from app.cards.service import CardService


def test_no_l2_candidate_assigns_available_l1_without_l2_attempt() -> None:
    repository = FakeCardRepository()
    seed_l1_candidate(repository, 10)
    card = CardService(repository).create_card(
        create_payload(), actor_user_id=1, ip_address=None, user_agent=None
    )

    assert card.status_code == int(CardStatus.REJECTED)
    assert card.l1_owner_id == 10
    assert repository.attempts == []
    assert repository.distribution_last_user_id is None
    assert repository.l1_distribution_last_user_id == 10
    assert repository.events[-1]["comment"] == "l1_assigned"
    assert repository.audit[-1]["entity_type"] == "l1_assignment"


def test_l1_round_robin_is_separate_from_l2_state() -> None:
    repository = FakeCardRepository()
    repository.distribution_last_user_id = 70
    repository.l1_distribution_last_user_id = 10
    seed_l1_candidate(repository, 10)
    seed_l1_candidate(repository, 20)
    service = CardService(repository)

    card = service.create_card(
        create_payload(), actor_user_id=1, ip_address=None, user_agent=None
    )

    assert card.l1_owner_id == 20
    assert repository.l1_distribution_last_user_id == 20
    assert repository.distribution_last_user_id == 70


def test_l1_excludes_absent_outside_schedule_and_overlapping_candidates() -> None:
    repository = FakeCardRepository()
    planned_start = DEFAULT_PLANNED_START_AT
    seed_l1_candidate(
        repository,
        10,
        planned_start_at=planned_start,
        absence=TimeInterval(
            start_at=planned_start - timedelta(minutes=10),
            end_at=planned_start + timedelta(minutes=10),
        ),
    )
    seed_l1_candidate(
        repository,
        20,
        planned_start_at=planned_start,
        schedule_start=time(0, 0),
        schedule_end=time(1, 0),
    )
    seed_l1_candidate(repository, 30, planned_start_at=planned_start)
    seed_l1_candidate(repository, 40, planned_start_at=planned_start)
    service = CardService(repository)
    busy = service.create_card(
        create_payload(
            l2_engineer_id=999,
            omnidesk_ticket_number="998-000001",
            planned_start_at=planned_start,
        ),
        actor_user_id=1,
        ip_address=None,
        user_agent=None,
    )
    repository.cards[busy.public_id] = replace(busy, l1_owner_id=30)

    card = service.create_card(
        create_payload(planned_start_at=planned_start),
        actor_user_id=1,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.REJECTED)
    assert card.l1_owner_id == 40


def test_full_l2_rejection_assigns_l1_and_preserves_l2_attempt() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l1_candidate(repository, 10)
    service = CardService(repository)
    assigned = service.create_card(
        create_payload(), actor_user_id=1, ip_address=None, user_agent=None
    )

    rejected = service.reject_card(
        assigned.public_id,
        actor_user_id=20,
        rejection_reason="stage-test",
        ip_address=None,
        user_agent=None,
    )

    assert rejected.status_code == int(CardStatus.REJECTED)
    assert rejected.l1_owner_id == 10
    assert len(repository.attempts) == 1
    assert repository.attempts[0].status_code == int(AssignmentAttemptStatus.REJECTED)
    assert repository.distribution_last_user_id == 20
    assert repository.l1_distribution_last_user_id == 10


def test_repeated_l1_assignment_does_not_change_owner_or_round_robin_state() -> None:
    repository = FakeCardRepository()
    seed_l1_candidate(repository, 10)
    card = CardService(repository).create_card(
        create_payload(), actor_user_id=1, ip_address=None, user_agent=None
    )
    event_count = len(repository.events)
    audit_count = len(repository.audit)

    repeated = L1DistributionService(repository).assign(
        card, ip_address=None, user_agent=None
    )

    assert repeated == card
    assert repository.l1_distribution_last_user_id == 10
    assert len(repository.events) == event_count
    assert len(repository.audit) == audit_count


def test_no_l1_candidate_leaves_rejected_card_without_owner() -> None:
    repository = FakeCardRepository()
    repository.l1_distribution_last_user_id = 10
    card = CardService(repository).create_card(
        create_payload(), actor_user_id=1, ip_address=None, user_agent=None
    )

    assert card.status_code == int(CardStatus.REJECTED)
    assert card.l1_owner_id is None
    assert repository.l1_distribution_last_user_id == 10
    assert repository.audit[-1]["new_values"]["reason"] == "no_available_l1_candidates"

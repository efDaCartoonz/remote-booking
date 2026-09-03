from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.cards import get_card_repository
from app.assignments.types import (
    AssignmentAttemptRecord,
    AssignmentCycleRecord,
    L1DistributionCandidate,
    L2DistributionCandidate,
    ScheduleWindow,
    TimeInterval,
)
from app.auth.dependencies import get_auth_store, get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import (
    ActorType,
    AssignmentAttemptStatus,
    AssignmentCycleStatus,
    AuditAction,
    CardEventType,
    CardStatus,
    DistributionPool,
)
from app.cards.repository import (
    CardRecord,
    ClientRecord,
    ClientSyncData,
    CreateCardData,
    L1FollowupUpdateData,
    StatusUpdateData,
)
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService, InvalidCardTransitionError
from app.main import create_app

DEFAULT_PLANNED_START_AT = datetime(2026, 9, 7, 10, tzinfo=UTC)


class FakeCardRepository:
    def __init__(self) -> None:
        self.cards: dict[UUID, CardRecord] = {}
        self.clients: dict[str, ClientRecord] = {}
        self.events: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.l2_candidate_schedules: dict[int, list[ScheduleWindow]] = {}
        self.l2_candidate_absences: dict[int, list[TimeInterval]] = {}
        self.l1_candidate_schedules: dict[int, list[ScheduleWindow]] = {}
        self.l1_candidate_absences: dict[int, list[TimeInterval]] = {}
        self.distribution_last_user_id: int | None = None
        self.l1_distribution_last_user_id: int | None = None
        self.cycles: list[AssignmentCycleRecord] = []
        self.attempts: list[AssignmentAttemptRecord] = []
        self.next_id = 1
        self.next_client_id = 1
        self.next_cycle_id = 1
        self.next_attempt_id = 1

    def create_card(self, data: CreateCardData) -> CardRecord:
        now = datetime.now(UTC)
        card = CardRecord(
            id=self.next_id,
            public_id=uuid4(),
            number=f"RDM-{self.next_id:06d}",
            omnidesk_ticket_number=data.omnidesk_ticket_number,
            client_id=data.client_id,
            status_code=int(data.status),
            criticality_code=data.criticality_code,
            urgency_code=data.urgency_code,
            planned_start_at=data.planned_start_at,
            planned_duration_minutes=data.planned_duration_minutes,
            client_timezone_at_creation=data.client_timezone_at_creation,
            timezone_source_code=data.timezone_source_code,
            actual_start_at=None,
            actual_end_at=None,
            l1_owner_id=data.l1_owner_id,
            l2_engineer_id=data.l2_engineer_id,
            assignment_method_code=data.assignment_method_code,
            unsuccessful_cycle_count=0,
            client_contact_type_code=data.client_contact_type_code,
            client_contact_value=data.client_contact_value,
            description=data.description,
            urgent_reason=data.urgent_reason,
            out_of_hours_flag=data.out_of_hours_flag,
            retroactive_flag=data.retroactive_flag,
            overdue_flag=False,
            result_code=None,
            engineer_report=None,
            created_source_code=data.created_source_code,
            created_by_id=data.created_by_id,
            created_at=now,
            updated_at=now,
        )
        self.next_id += 1
        self.cards[card.public_id] = card
        return card

    def get_or_create_client(self, data: ClientSyncData) -> ClientRecord:
        client = self.clients.get(data.omnidesk_user_id)
        if client is not None:
            return client
        client = ClientRecord(
            id=self.next_client_id,
            omnidesk_user_id=data.omnidesk_user_id,
            omnidesk_company_id=data.omnidesk_company_id,
            display_name=data.display_name,
        )
        self.next_client_id += 1
        self.clients[data.omnidesk_user_id] = client
        return client

    def list_cards_by_ticket(self, omnidesk_ticket_number: str) -> list[CardRecord]:
        return [
            card
            for card in self.cards.values()
            if card.omnidesk_ticket_number == omnidesk_ticket_number
        ]

    def has_active_card_for_ticket(self, omnidesk_ticket_number: str) -> bool:
        return any(
            card.omnidesk_ticket_number == omnidesk_ticket_number
            and CardStatus(card.status_code)
            not in {CardStatus.COMPLETED, CardStatus.CANCELLED}
            for card in self.cards.values()
        )

    def get_card_by_public_id(self, public_id: UUID) -> CardRecord | None:
        return self.cards.get(public_id)

    def get_card_by_public_id_for_update(self, public_id: UUID) -> CardRecord | None:
        return self.cards.get(public_id)

    def update_card_status(
        self,
        public_id: UUID,
        data: StatusUpdateData,
    ) -> CardRecord | None:
        card = self.cards.get(public_id)
        if card is None:
            return None
        updated = replace(
            card,
            status_code=int(data.status),
            l2_engineer_id=(
                data.l2_engineer_id
                if data.update_l2_engineer_id
                else card.l2_engineer_id
            ),
            actual_start_at=data.actual_start_at or card.actual_start_at,
            actual_end_at=data.actual_end_at or card.actual_end_at,
            result_code=data.result_code
            if data.result_code is not None
            else card.result_code,
            engineer_report=data.engineer_report or card.engineer_report,
            updated_at=datetime.now(UTC),
        )
        self.cards[public_id] = updated
        return updated

    def update_l1_followup(
        self, public_id: UUID, data: L1FollowupUpdateData
    ) -> CardRecord | None:
        card = self.cards.get(public_id)
        if (
            card is None
            or CardStatus(card.status_code) != CardStatus.REJECTED
            or card.l1_owner_id is None
        ):
            return None
        updated = replace(
            card,
            planned_start_at=data.planned_start_at or card.planned_start_at,
            planned_duration_minutes=data.planned_duration_minutes
            or card.planned_duration_minutes,
            description=data.description
            if data.description is not None
            else card.description,
            client_informed=data.client_informed
            if data.client_informed is not None
            else card.client_informed,
            status_code=0 if data.reset_for_new_cycle else card.status_code,
            l1_owner_id=None if data.reset_for_new_cycle else card.l1_owner_id,
            updated_at=datetime.now(UTC),
        )
        self.cards[public_id] = updated
        return updated

    def list_l2_distribution_candidates(
        self, *, planned_start_at: datetime, planned_end_at: datetime
    ) -> list[L2DistributionCandidate]:
        candidates = []
        for user_id in sorted(self.l2_candidate_schedules):
            active_cards = []
            for card in self.cards.values():
                card_start = card.planned_start_at
                card_end = card_start + timedelta(minutes=card.planned_duration_minutes)
                if (
                    card.l2_engineer_id == user_id
                    and CardStatus(card.status_code)
                    in {
                        CardStatus.ASSIGNED,
                        CardStatus.CONFIRMED,
                        CardStatus.IN_PROGRESS,
                    }
                    and card_start < planned_end_at
                    and planned_start_at < card_end
                ):
                    active_cards.append(
                        TimeInterval(start_at=card_start, end_at=card_end)
                    )
            candidates.append(
                L2DistributionCandidate(
                    user_id=user_id,
                    schedules=tuple(self.l2_candidate_schedules[user_id]),
                    absences=tuple(self.l2_candidate_absences.get(user_id, [])),
                    active_cards=tuple(active_cards),
                )
            )
        return candidates

    def list_l1_distribution_candidates(
        self, *, planned_start_at: datetime, planned_end_at: datetime
    ) -> list[L1DistributionCandidate]:
        candidates = []
        for user_id in sorted(self.l1_candidate_schedules):
            active_cards = []
            for card in self.cards.values():
                card_start = card.planned_start_at
                card_end = card_start + timedelta(minutes=card.planned_duration_minutes)
                if (
                    card.l1_owner_id == user_id
                    and CardStatus(card.status_code)
                    in {
                        CardStatus.ASSIGNED,
                        CardStatus.CONFIRMED,
                        CardStatus.IN_PROGRESS,
                    }
                    and card_start < planned_end_at
                    and planned_start_at < card_end
                ):
                    active_cards.append(
                        TimeInterval(start_at=card_start, end_at=card_end)
                    )
            candidates.append(
                L1DistributionCandidate(
                    user_id=user_id,
                    schedules=tuple(self.l1_candidate_schedules[user_id]),
                    absences=tuple(self.l1_candidate_absences.get(user_id, [])),
                    active_cards=tuple(active_cards),
                )
            )
        return candidates

    def get_distribution_last_user_id_for_update(
        self, pool: DistributionPool
    ) -> int | None:
        if pool == DistributionPool.L2:
            return self.distribution_last_user_id
        assert pool == DistributionPool.L1
        return self.l1_distribution_last_user_id

    def update_distribution_state(
        self, *, pool: DistributionPool, last_user_id: int
    ) -> None:
        if pool == DistributionPool.L2:
            self.distribution_last_user_id = last_user_id
            return
        assert pool == DistributionPool.L1
        self.l1_distribution_last_user_id = last_user_id

    def update_l1_owner(self, *, card_id: int, l1_owner_id: int) -> CardRecord | None:
        for public_id, card in self.cards.items():
            if card.id != card_id:
                continue
            if CardStatus(card.status_code) != CardStatus.REJECTED or card.l1_owner_id:
                return None
            updated = replace(
                card, l1_owner_id=l1_owner_id, updated_at=datetime.now(UTC)
            )
            self.cards[public_id] = updated
            return updated
        raise AssertionError(f"Card {card_id} not found")

    def get_next_assignment_cycle_number(self, card_id: int) -> int:
        return (
            max(
                (
                    cycle.cycle_number
                    for cycle in self.cycles
                    if cycle.card_id == card_id
                ),
                default=0,
            )
            + 1
        )

    def create_assignment_cycle(
        self,
        *,
        card_id: int,
        cycle_number: int,
        status: AssignmentCycleStatus,
    ) -> AssignmentCycleRecord:
        cycle = AssignmentCycleRecord(
            id=self.next_cycle_id,
            card_id=card_id,
            cycle_number=cycle_number,
            status_code=int(status),
        )
        self.next_cycle_id += 1
        self.cycles.append(cycle)
        return cycle

    def update_assignment_cycle_status(
        self, *, cycle_id: int, status: AssignmentCycleStatus
    ) -> None:
        self.cycles = [
            replace(cycle, status_code=int(status)) if cycle.id == cycle_id else cycle
            for cycle in self.cycles
        ]

    def get_current_assignment_cycle_for_update(
        self, card_id: int
    ) -> AssignmentCycleRecord | None:
        active_statuses = {
            int(AssignmentCycleStatus.IN_PROGRESS),
            int(AssignmentCycleStatus.ASSIGNED),
        }
        candidates = [
            cycle
            for cycle in self.cycles
            if cycle.card_id == card_id and cycle.status_code in active_statuses
        ]
        return max(
            candidates, key=lambda cycle: (cycle.cycle_number, cycle.id), default=None
        )

    def create_assignment_attempt(
        self,
        *,
        cycle_id: int,
        card_id: int,
        l2_engineer_id: int,
        status: AssignmentAttemptStatus,
    ) -> AssignmentAttemptRecord:
        attempt = AssignmentAttemptRecord(
            id=self.next_attempt_id,
            cycle_id=cycle_id,
            card_id=card_id,
            l2_engineer_id=l2_engineer_id,
            status_code=int(status),
        )
        self.next_attempt_id += 1
        self.attempts.append(attempt)
        return attempt

    def get_pending_assignment_attempt_for_update(
        self, *, card_id: int, l2_engineer_id: int
    ) -> AssignmentAttemptRecord | None:
        for attempt in reversed(self.attempts):
            if (
                attempt.card_id == card_id
                and attempt.l2_engineer_id == l2_engineer_id
                and attempt.status_code == int(AssignmentAttemptStatus.PENDING)
            ):
                return attempt
        return None

    def list_attempted_l2_engineer_ids(self, cycle_id: int) -> set[int]:
        return {
            attempt.l2_engineer_id
            for attempt in self.attempts
            if attempt.cycle_id == cycle_id
        }

    def update_assignment_attempt_response(
        self,
        *,
        attempt_id: int,
        status: AssignmentAttemptStatus,
        actor_user_id: int,
        rejection_reason: str | None,
    ) -> AssignmentAttemptRecord | None:
        for index, attempt in enumerate(self.attempts):
            if attempt.id != attempt_id:
                continue
            if attempt.status_code != int(AssignmentAttemptStatus.PENDING):
                return None
            updated = replace(
                attempt,
                status_code=int(status),
                responded_at=datetime.now(UTC),
                actor_user_id=actor_user_id,
                rejection_reason=rejection_reason,
            )
            self.attempts[index] = updated
            return updated
        return None

    def update_card_distribution_result(
        self,
        *,
        card_id: int,
        status: CardStatus,
        l2_engineer_id: int | None,
        increment_unsuccessful_cycle_count: bool,
    ) -> CardRecord:
        for public_id, card in self.cards.items():
            if card.id != card_id:
                continue
            updated = replace(
                card,
                status_code=int(status),
                l2_engineer_id=l2_engineer_id,
                unsuccessful_cycle_count=card.unsuccessful_cycle_count
                + int(increment_unsuccessful_cycle_count),
                updated_at=datetime.now(UTC),
            )
            self.cards[public_id] = updated
            return updated
        raise AssertionError(f"Card {card_id} not found")

    def add_card_event(
        self,
        *,
        card_id: int,
        event_type: CardEventType,
        actor_user_id: int | None,
        actor_type: ActorType,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        comment: str | None,
    ) -> None:
        self.events.append(
            {
                "card_id": card_id,
                "event_type": event_type,
                "actor_user_id": actor_user_id,
                "actor_type": actor_type,
                "old_values": old_values,
                "new_values": new_values,
                "comment": comment,
            }
        )

    def add_audit_log(
        self,
        *,
        actor_user_id: int | None,
        actor_type: ActorType,
        action: AuditAction,
        entity_id: int,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        ip_address: str | None,
        user_agent: str | None,
        entity_type: str = "connection_card",
    ) -> None:
        self.audit.append(
            {
                "actor_user_id": actor_user_id,
                "actor_type": actor_type,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "old_values": old_values,
                "new_values": new_values,
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
        )


def create_payload(
    *,
    l2_engineer_id: int | None = None,
    omnidesk_ticket_number: str = "123-456789",
    planned_start_at: datetime | None = None,
) -> CardCreateRequest:
    return CardCreateRequest(
        omnidesk_ticket_number=omnidesk_ticket_number,
        planned_start_at=planned_start_at or DEFAULT_PLANNED_START_AT,
        planned_duration_minutes=60,
        l2_engineer_id=l2_engineer_id,
        description="Проверить удаленный доступ",
    )


def seed_l2_candidate(
    repository: FakeCardRepository,
    user_id: int,
    *,
    planned_start_at: datetime | None = None,
    schedule_start: time = time(0, 0),
    schedule_end: time = time(23, 59, 59),
    absence: TimeInterval | None = None,
) -> None:
    planned_start_at = planned_start_at or DEFAULT_PLANNED_START_AT
    repository.l2_candidate_schedules[user_id] = [
        ScheduleWindow(
            weekday=planned_start_at.isoweekday(),
            start_time=schedule_start,
            end_time=schedule_end,
            timezone="UTC",
            valid_from=None,
            valid_to=None,
        )
    ]
    if absence is not None:
        repository.l2_candidate_absences[user_id] = [absence]


def seed_l1_candidate(
    repository: FakeCardRepository,
    user_id: int,
    *,
    planned_start_at: datetime | None = None,
    schedule_start: time = time(0, 0),
    schedule_end: time = time(23, 59, 59),
    absence: TimeInterval | None = None,
) -> None:
    planned_start_at = planned_start_at or DEFAULT_PLANNED_START_AT
    repository.l1_candidate_schedules[user_id] = [
        ScheduleWindow(
            weekday=planned_start_at.isoweekday(),
            start_time=schedule_start,
            end_time=schedule_end,
            timezone="UTC",
            valid_from=None,
            valid_to=None,
        )
    ]
    if absence is not None:
        repository.l1_candidate_absences[user_id] = [absence]


def make_service(repository: FakeCardRepository) -> CardService:
    return CardService(repository)


def test_create_card_without_l2_rejects_when_no_distribution_candidates() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)

    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert card.status_code == int(CardStatus.REJECTED)
    assert card.l2_engineer_id is None
    assert card.unsuccessful_cycle_count == 1
    assert [event["event_type"] for event in repository.events] == [
        CardEventType.CREATED,
        CardEventType.STATUS_CHANGED,
    ]
    assert [event["action"] for event in repository.audit] == [
        AuditAction.CREATE,
        AuditAction.CREATE,
        AuditAction.UPDATE,
        AuditAction.UPDATE,
    ]
    assert repository.audit[-1]["entity_type"] == "l1_assignment"
    assert repository.events[0]["new_values"]["status_code"] == int(CardStatus.CREATED)
    assert repository.events[-1]["comment"] == "no_available_l2_candidates"


def test_create_card_with_l2_starts_assigned() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)

    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == 20


def test_allowed_lifecycle_path_writes_status_history_and_audit() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    confirmed = service.confirm_card(
        card.public_id,
        actor_user_id=20,
        comment="Подтверждаю",
        ip_address=None,
        user_agent=None,
    )
    started = service.start_card(
        card.public_id,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )
    completed = service.complete_card(
        card.public_id,
        result_code=0,
        engineer_report="Подключение выполнено успешно",
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    assert [confirmed.status_code, started.status_code, completed.status_code] == [
        int(CardStatus.CONFIRMED),
        int(CardStatus.IN_PROGRESS),
        int(CardStatus.COMPLETED),
    ]
    assert [event["event_type"] for event in repository.events] == [
        CardEventType.CREATED,
        CardEventType.ENGINEER_ASSIGNED,
        CardEventType.STATUS_CHANGED,
        CardEventType.STATUS_CHANGED,
        CardEventType.STATUS_CHANGED,
    ]
    assert [event["action"] for event in repository.audit] == [
        AuditAction.CREATE,
        AuditAction.CREATE,
        AuditAction.CREATE,
        AuditAction.UPDATE,
        AuditAction.UPDATE,
        AuditAction.UPDATE,
        AuditAction.UPDATE,
        AuditAction.UPDATE,
    ]


def test_created_card_cannot_be_cancelled_by_user_action() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = repository.create_card(
        CreateCardData(
            omnidesk_ticket_number="123-456789",
            planned_start_at=datetime.now(UTC) + timedelta(hours=3),
            planned_duration_minutes=60,
            created_by_id=10,
            status=CardStatus.CREATED,
        )
    )

    with pytest.raises(
        InvalidCardTransitionError, match="status_transition_not_allowed"
    ):
        service.cancel_card(
            card.public_id,
            actor_user_id=10,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

    assert len(repository.events) == 0
    assert len(repository.audit) == 0


def test_card_cannot_be_completed_without_in_progress_status() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(
        InvalidCardTransitionError, match="status_transition_not_allowed"
    ):
        service.complete_card(
            card.public_id,
            result_code=0,
            engineer_report="Готово",
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )


def test_assigned_l2_confirms_card_and_assignment_attempt() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    confirmed = service.confirm_card(
        card.public_id,
        actor_user_id=20,
        comment="Подтверждаю",
        ip_address=None,
        user_agent=None,
    )

    assert confirmed.status_code == int(CardStatus.CONFIRMED)
    assert confirmed.l2_engineer_id == 20
    assert repository.attempts[0].status_code == int(AssignmentAttemptStatus.CONFIRMED)
    assert repository.attempts[0].responded_at is not None
    assert repository.attempts[0].actor_user_id == 20
    assert repository.events[-1]["old_values"]["status_code"] == int(
        CardStatus.ASSIGNED
    )
    assert repository.events[-1]["new_values"]["status_code"] == int(
        CardStatus.CONFIRMED
    )
    assert repository.audit[-2]["entity_type"] == "assignment_attempt"
    assert repository.audit[-1]["entity_type"] == "connection_card"


def test_reject_requires_reason() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(InvalidCardTransitionError, match="rejection_reason_required"):
        service.reject_card(
            card.public_id,
            actor_user_id=20,
            rejection_reason="   ",
            ip_address=None,
            user_agent=None,
        )

    assert repository.attempts[0].status_code == int(AssignmentAttemptStatus.PENDING)
    assert repository.cards[card.public_id].status_code == int(CardStatus.ASSIGNED)


def test_reject_reassigns_next_l2_in_current_cycle() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    service = make_service(repository)
    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    reassigned = service.reject_card(
        card.public_id,
        actor_user_id=20,
        rejection_reason="Занят на аварии",
        ip_address=None,
        user_agent=None,
    )

    assert reassigned.status_code == int(CardStatus.ASSIGNED)
    assert reassigned.l2_engineer_id == 30
    assert reassigned.unsuccessful_cycle_count == 0
    assert [attempt.l2_engineer_id for attempt in repository.attempts] == [20, 30]
    assert [attempt.status_code for attempt in repository.attempts] == [
        int(AssignmentAttemptStatus.REJECTED),
        int(AssignmentAttemptStatus.PENDING),
    ]
    assert repository.attempts[0].rejection_reason == "Занят на аварии"
    assert repository.cycles[0].status_code == int(AssignmentCycleStatus.ASSIGNED)
    assert repository.distribution_last_user_id == 30
    assert repository.events[-1]["event_type"] == CardEventType.ENGINEER_ASSIGNED
    assert repository.events[-1]["comment"] == "Занят на аварии"


def test_reject_exhausts_candidates_and_rejects_card() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    rejected = service.reject_card(
        card.public_id,
        actor_user_id=20,
        rejection_reason="Нет доступа к стенду",
        ip_address=None,
        user_agent=None,
    )

    assert rejected.status_code == int(CardStatus.REJECTED)
    assert rejected.l2_engineer_id is None
    assert rejected.unsuccessful_cycle_count == 1
    assert repository.attempts[0].status_code == int(AssignmentAttemptStatus.REJECTED)
    assert repository.attempts[0].rejection_reason == "Нет доступа к стенду"
    assert repository.cycles[0].status_code == int(AssignmentCycleStatus.ALL_REJECTED)
    assert repository.events[-1]["comment"] == (
        "all_l2_candidates_rejected: Нет доступа к стенду"
    )


def test_foreign_l2_cannot_confirm_or_reject_assigned_card() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(InvalidCardTransitionError, match="assigned_l2_required"):
        service.confirm_card(
            card.public_id,
            actor_user_id=30,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

    with pytest.raises(InvalidCardTransitionError, match="assigned_l2_required"):
        service.reject_card(
            card.public_id,
            actor_user_id=30,
            rejection_reason="Не мой слот",
            ip_address=None,
            user_agent=None,
        )

    assert repository.attempts[0].status_code == int(AssignmentAttemptStatus.PENDING)


def test_l2_decision_requires_assigned_status() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = repository.create_card(
        CreateCardData(
            omnidesk_ticket_number="123-456789",
            planned_start_at=datetime.now(UTC) + timedelta(hours=3),
            planned_duration_minutes=60,
            created_by_id=10,
            status=CardStatus.CREATED,
            l2_engineer_id=20,
        )
    )

    with pytest.raises(
        InvalidCardTransitionError, match="status_transition_not_allowed"
    ):
        service.confirm_card(
            card.public_id,
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )


def test_complete_requires_engineer_report() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    service.start_card(
        card.public_id,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(InvalidCardTransitionError, match="engineer_report_required"):
        service.complete_card(
            card.public_id,
            result_code=0,
            engineer_report="   ",
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )


def test_terminal_statuses_are_immutable_for_user_actions() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    service.start_card(
        card.public_id,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )
    completed = service.complete_card(
        card.public_id,
        result_code=0,
        engineer_report="Готово",
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(
        InvalidCardTransitionError, match="status_transition_not_allowed"
    ):
        service.cancel_card(
            completed.public_id,
            actor_user_id=10,
            comment=None,
            ip_address=None,
            user_agent=None,
        )


def test_cards_api_requires_authorized_internal_user() -> None:
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: FakeCardRepository()
    app.dependency_overrides[get_auth_store] = lambda: object()
    client = TestClient(app, base_url="https://testserver")

    response = client.post(
        "/api/v1/cards",
        json={
            "omnidesk_ticket_number": "123-456789",
            "planned_start_at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "not_authenticated"


def test_cards_api_creates_and_reads_card() -> None:
    repository = FakeCardRepository()
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: repository
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        id=10,
        username="manager",
        password_hash="unused",
        full_name="Руководитель",
        email=None,
        roles=(RoleRecord(id=3, name="Руководитель"),),
    )
    client = TestClient(app, base_url="https://testserver")

    create_response = client.post(
        "/api/v1/cards",
        json={
            "omnidesk_ticket_number": "123-456789",
            "planned_start_at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
            "planned_duration_minutes": 60,
            "l2_engineer_id": 20,
        },
    )

    assert create_response.status_code == 201
    created_body = create_response.json()
    assert created_body["status"] == "assigned"
    assert created_body["status_label"] == "Назначено"
    assert created_body["l2_engineer_id"] == 20

    read_response = client.get(f"/api/v1/cards/{created_body['id']}")

    assert read_response.status_code == 200
    assert read_response.json()["id"] == created_body["id"]


def test_cards_api_returns_conflict_for_forbidden_transition() -> None:
    repository = FakeCardRepository()
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: repository
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        id=10,
        username="l1",
        password_hash="unused",
        full_name="Специалист Л1",
        email=None,
        roles=(RoleRecord(id=1, name="Специалист Л1"),),
    )
    client = TestClient(app, base_url="https://testserver")
    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    response = client.post(
        f"/api/v1/cards/{card.public_id}/complete",
        json={"result_code": 0, "engineer_report": "Готово"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "status_transition_not_allowed"

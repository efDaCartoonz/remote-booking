from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.cards as cards_api

from app.api.cards import get_card_repository, get_card_service
from app.assignments.types import (
    AssignmentAttemptRecord,
    AssignmentCycleRecord,
    L1DistributionCandidate,
    L2DistributionCandidate,
    ScheduleWindow,
    TimeInterval,
)
from app.assignments.manager_escalation import ManagerRecipient
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
    RoleId,
)
from app.cards.create_policy import CreateScenario, validate_role_create
from app.cards.policy import CardActionPolicyError
from app.cards.repository import (
    CardHistoryRecord,
    CardRecord,
    ClientRecord,
    ClientSyncData,
    CreateCardData,
    L1FollowupUpdateData,
    ScheduleUpdateData,
    StatusUpdateData,
    _card_from_row,
)
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService, InvalidCardTransitionError
from app.main import create_app
from app.notifications import RecordingNotificationService

DEFAULT_PLANNED_START_AT = datetime(2026, 9, 7, 10, tzinfo=UTC)
RESCHEDULE_NOW = DEFAULT_PLANNED_START_AT - timedelta(hours=3)


class FakeCardRepository:
    def __init__(self) -> None:
        self.cards: dict[UUID, CardRecord] = {}
        self.clients: dict[str, ClientRecord] = {}
        self.events: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.l2_candidate_schedules: dict[int, list[ScheduleWindow]] = {}
        self.l2_candidate_absences: dict[int, list[TimeInterval]] = {}
        self.l2_candidate_non_working_dates: dict[int, frozenset[date]] = {}
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
        self.schedules: list[dict[str, Any]] = []
        self.due_reminders: list[Any] = []
        self.reminder_advances: list[dict[str, Any]] = []
        self.active_result_codes: set[int] = {0}
        self.omnidesk_note_intents: list[dict[str, Any]] = []

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
            actual_start_at=data.actual_start_at,
            actual_end_at=data.actual_end_at,
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
            result_code=data.result_code,
            engineer_report=data.engineer_report,
            created_source_code=data.created_source_code,
            created_by_id=data.created_by_id,
            created_at=now,
            updated_at=now,
            actual_duration_minutes=data.actual_duration_minutes,
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

    def has_active_result_code(self, result_code: int) -> bool:
        return result_code in self.active_result_codes

    def create_omnidesk_internal_note_intent(
        self,
        *,
        card_id: int,
        source_event_id: int,
        omnidesk_ticket_number: str,
        payload: dict[str, Any],
    ) -> int:
        if any(
            intent["source_event_id"] == source_event_id
            for intent in self.omnidesk_note_intents
        ):
            return 0
        intent_id = len(self.omnidesk_note_intents) + 1
        self.omnidesk_note_intents.append(
            {
                "id": intent_id,
                "card_id": card_id,
                "source_event_id": source_event_id,
                "omnidesk_ticket_number": omnidesk_ticket_number,
                "payload": payload,
            }
        )
        return intent_id

    def list_conflicting_active_cards_for_update(
        self,
        *,
        l2_engineer_id: int,
        planned_start_at: datetime,
        planned_end_at: datetime,
    ) -> list[CardRecord]:
        result = []
        for card in self.cards.values():
            if (
                card.l2_engineer_id == l2_engineer_id
                and CardStatus(card.status_code)
                in {CardStatus.ASSIGNED, CardStatus.CONFIRMED, CardStatus.IN_PROGRESS}
                and (
                    card.urgency_code == 0
                    or CardStatus(card.status_code) == CardStatus.IN_PROGRESS
                )
                and card.planned_start_at < planned_end_at
                and planned_start_at
                < card.planned_start_at
                + timedelta(minutes=card.planned_duration_minutes)
            ):
                result.append(card)
        return sorted(result, key=lambda item: (item.planned_start_at, item.id))

    def get_card_by_public_id(self, public_id: UUID) -> CardRecord | None:
        return self.cards.get(public_id)

    def get_card_by_public_id_for_update(self, public_id: UUID) -> CardRecord | None:
        return self.cards.get(public_id)

    def get_card_by_id_for_update(self, card_id: int) -> CardRecord | None:
        return next((card for card in self.cards.values() if card.id == card_id), None)

    def has_card_event_comment(self, *, card_id: int, comment: str) -> bool:
        return any(
            event["card_id"] == card_id and event["comment"] == comment
            for event in self.events
        )

    def list_card_history(self, public_id: UUID) -> list[CardHistoryRecord] | None:
        card = self.cards.get(public_id)
        if card is None:
            return None
        return [
            CardHistoryRecord(
                event_type_code=int(event["event_type"]),
                actor_type_code=int(event["actor_type"]),
                actor_name=None,
                created_at=event["created_at"],
            )
            for event in reversed(self.events)
            if event["card_id"] == card.id
        ]

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
            actual_duration_minutes=(
                data.actual_duration_minutes
                if data.actual_duration_minutes is not None
                else card.actual_duration_minutes
            ),
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

    def reset_card_for_reschedule(
        self, public_id: UUID, data: ScheduleUpdateData
    ) -> CardRecord | None:
        card = self.cards.get(public_id)
        if card is None or CardStatus(card.status_code) not in {
            CardStatus.ASSIGNED,
            CardStatus.CONFIRMED,
            CardStatus.REJECTED,
        }:
            return None
        updated = replace(
            card,
            planned_start_at=data.planned_start_at,
            planned_duration_minutes=data.planned_duration_minutes,
            description=data.description
            if data.description is not None
            else card.description,
            status_code=int(CardStatus.CREATED),
            l2_engineer_id=None,
            l1_owner_id=None,
            client_informed=False,
            overdue_flag=False,
            out_of_hours_flag=data.out_of_hours_flag,
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
                    non_working_dates=self.l2_candidate_non_working_dates.get(
                        user_id, frozenset()
                    ),
                )
            )
        return candidates

    def list_all_l2_candidates(
        self,
        *,
        planned_start_at: datetime,
        planned_end_at: datetime,
        exclude_card_id: int | None = None,
    ) -> list[L2DistributionCandidate]:
        candidates = []
        for user_id in sorted(self.l2_candidate_schedules):
            active_cards = []
            for card in self.cards.values():
                card_start = card.planned_start_at
                card_end = card_start + timedelta(minutes=card.planned_duration_minutes)
                if (
                    card.id != exclude_card_id
                    and card.l2_engineer_id == user_id
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
                    non_working_dates=self.l2_candidate_non_working_dates.get(
                        user_id, frozenset()
                    ),
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
            if (
                CardStatus(card.status_code)
                not in {CardStatus.REJECTED, CardStatus.ASSIGNED}
                or (
                    CardStatus(card.status_code) == CardStatus.ASSIGNED
                    and not card.overdue_flag
                )
                or card.l1_owner_id
            ):
                return None
            updated = replace(
                card, l1_owner_id=l1_owner_id, updated_at=datetime.now(UTC)
            )
            self.cards[public_id] = updated
            return updated
        raise AssertionError(f"Card {card_id} not found")

    def release_l1_followup(self, *, card_id: int) -> CardRecord | None:
        for public_id, card in self.cards.items():
            if card.id != card_id or card.l1_owner_id is None:
                continue
            updated = replace(
                card,
                l1_owner_id=None,
                client_informed=False,
                updated_at=datetime.now(UTC),
            )
            self.cards[public_id] = updated
            return updated
        return None

    def mark_l2_assignment_overdue(self, *, card_id: int):
        for public_id, card in self.cards.items():
            if (
                card.id != card_id
                or CardStatus(card.status_code) != CardStatus.ASSIGNED
                or card.overdue_flag
            ):
                continue
            updated = replace(card, overdue_flag=True, updated_at=datetime.now(UTC))
            self.cards[public_id] = updated
            event_id = self.add_card_event(
                card_id=card_id,
                event_type=CardEventType.STATUS_CHANGED,
                actor_user_id=None,
                actor_type=ActorType.SYSTEM,
                old_values={"overdue": False},
                new_values={"overdue": True},
                comment="l2_overdue",
            )
            self.add_audit_log(
                actor_user_id=None,
                actor_type=ActorType.SYSTEM,
                action=AuditAction.UPDATE,
                entity_id=card_id,
                old_values={"overdue": False},
                new_values={"overdue": True},
                ip_address=None,
                user_agent=None,
            )
            return updated, event_id
        return None

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
        clear_overdue_flag: bool = False,
    ) -> CardRecord:
        for public_id, card in self.cards.items():
            if card.id != card_id:
                continue
            updated = replace(
                card,
                status_code=int(status),
                l2_engineer_id=l2_engineer_id,
                overdue_flag=(False if clear_overdue_flag else card.overdue_flag),
                unsuccessful_cycle_count=card.unsuccessful_cycle_count
                + int(increment_unsuccessful_cycle_count),
                updated_at=datetime.now(UTC),
            )
            self.cards[public_id] = updated
            return updated
        raise AssertionError(f"Card {card_id} not found")

    def create_reminder_schedule(self, **data: Any) -> None:
        self.schedules.append({**data, "closed_at": None})

    def claim_due(self, **_: Any) -> list[Any]:
        return self.due_reminders

    def current(self, _: Any) -> bool:
        return True

    def record_timer_event(self, **_: Any) -> int:
        return len(self.events) + 1

    def recipients(self, **_: Any) -> list[tuple[str, str]]:
        return []

    def managers(self) -> list[tuple[int, str, str]]:
        return []

    def advance(self, **data: Any) -> None:
        self.reminder_advances.append(data)

    def close_reminder_schedules(
        self, *, card_id: int, kind: str | None = None
    ) -> None:
        for schedule in self.schedules:
            if schedule["card_id"] == card_id and (
                kind is None or schedule["kind"] == kind
            ):
                schedule["closed_at"] = datetime.now(UTC)

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
    ) -> int:
        event_id = len(self.events) + 1
        self.events.append(
            {
                "card_id": card_id,
                "event_type": event_type,
                "actor_user_id": actor_user_id,
                "actor_type": actor_type,
                "old_values": old_values,
                "new_values": new_values,
                "comment": comment,
                "created_at": datetime.now(UTC),
            }
        )
        return event_id

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
    planned_duration_minutes: int = 60,
) -> CardCreateRequest:
    return CardCreateRequest(
        omnidesk_ticket_number=omnidesk_ticket_number,
        planned_start_at=planned_start_at or DEFAULT_PLANNED_START_AT,
        planned_duration_minutes=planned_duration_minutes,
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


def make_service(
    repository: FakeCardRepository,
    notifications: RecordingNotificationService | None = None,
) -> CardService:
    return CardService(repository, notifications)


def test_card_row_mapping_preserves_client_informed_marker() -> None:
    repository = FakeCardRepository()
    card = repository.create_card(
        CreateCardData(
            omnidesk_ticket_number="999-000001",
            planned_start_at=DEFAULT_PLANNED_START_AT,
            planned_duration_minutes=60,
            created_by_id=None,
            status=CardStatus.CREATED,
        )
    )
    row = asdict(card)
    row["client_informed"] = True

    assert _card_from_row(row).client_informed is True


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


def test_cancel_closes_pending_assignment_lifecycle_and_reminders() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )

    cancelled = service.cancel_card(
        card.public_id,
        actor_user_id=10,
        actor_role_ids={int(RoleId.MANAGER)},
        comment="client_requested",
        ip_address=None,
        user_agent=None,
    )

    assert cancelled.status_code == int(CardStatus.CANCELLED)
    assert [cycle.status_code for cycle in repository.cycles] == [
        int(AssignmentCycleStatus.CANCELLED)
    ]
    assert [attempt.status_code for attempt in repository.attempts] == [
        int(AssignmentAttemptStatus.SKIPPED)
    ]
    assert repository.attempts[0].rejection_reason == "cancelled"
    assert all(item["closed_at"] is not None for item in repository.schedules)


def test_manager_reschedule_releases_assignment_and_starts_fresh_cycle() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )

    rescheduled = service.reschedule_card(
        card.public_id,
        actor_user_id=10,
        actor_role_ids={int(RoleId.MANAGER)},
        planned_start_at=DEFAULT_PLANNED_START_AT + timedelta(hours=2),
        planned_duration_minutes=90,
        description="Новое согласованное время",
        reason="client_requested",
        ip_address=None,
        user_agent=None,
        now=RESCHEDULE_NOW,
    )

    assert rescheduled.status_code == int(CardStatus.ASSIGNED)
    assert rescheduled.l2_engineer_id == 20
    assert rescheduled.planned_duration_minutes == 90
    assert [cycle.status_code for cycle in repository.cycles] == [
        int(AssignmentCycleStatus.CANCELLED),
        int(AssignmentCycleStatus.ASSIGNED),
    ]
    assert [attempt.status_code for attempt in repository.attempts] == [
        int(AssignmentAttemptStatus.SKIPPED),
        int(AssignmentAttemptStatus.PENDING),
    ]
    assert repository.attempts[0].rejection_reason == "rescheduled"
    assert (
        len([item for item in repository.schedules if item["closed_at"] is None]) == 1
    )
    assert repository.events[-2]["comment"] == "client_requested"


def test_manager_reschedule_to_selected_l2_persists_out_of_hours_flag() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    new_start = DEFAULT_PLANNED_START_AT.replace(hour=20)
    seed_l2_candidate(
        repository,
        20,
        planned_start_at=new_start,
        schedule_start=time(9),
        schedule_end=time(17),
    )

    rescheduled = service.reschedule_card(
        card.public_id,
        actor_user_id=10,
        actor_role_ids={int(RoleId.MANAGER)},
        planned_start_at=new_start,
        planned_duration_minutes=60,
        description=None,
        reason="client_requested",
        ip_address=None,
        user_agent=None,
        selected_l2_engineer_id=20,
        now=RESCHEDULE_NOW,
    )

    assert rescheduled.status_code == int(CardStatus.ASSIGNED)
    assert rescheduled.l2_engineer_id == 20
    assert rescheduled.out_of_hours_flag is True
    assert [cycle.status_code for cycle in repository.cycles] == [
        int(AssignmentCycleStatus.CANCELLED),
        int(AssignmentCycleStatus.ASSIGNED),
    ]


@pytest.mark.parametrize(
    ("initial_status", "expected_attempt_count"),
    ((CardStatus.CONFIRMED, 2), (CardStatus.REJECTED, 1)),
)
def test_manager_reschedule_selected_l2_from_confirmed_or_rejected_has_one_fresh_cycle(
    initial_status: CardStatus,
    expected_attempt_count: int,
) -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    if initial_status == CardStatus.CONFIRMED:
        seed_l2_candidate(repository, 20)
        card = service.create_card(
            create_payload(), actor_user_id=10, ip_address=None, user_agent=None
        )
        card = service.confirm_card(
            card.public_id,
            actor_user_id=20,
            comment="Подтверждаю",
            ip_address=None,
            user_agent=None,
        )
    else:
        card = service.create_card(
            create_payload(), actor_user_id=10, ip_address=None, user_agent=None
        )
        assert card.status_code == int(CardStatus.REJECTED)
        seed_l2_candidate(repository, 20)

    new_start = DEFAULT_PLANNED_START_AT + timedelta(hours=2)
    rescheduled = service.reschedule_card(
        card.public_id,
        actor_user_id=10,
        actor_role_ids={int(RoleId.MANAGER)},
        planned_start_at=new_start,
        planned_duration_minutes=90,
        description="Новое согласованное время",
        reason="client_requested",
        ip_address=None,
        user_agent=None,
        selected_l2_engineer_id=20,
        now=RESCHEDULE_NOW,
    )

    assert rescheduled.status_code == int(CardStatus.ASSIGNED)
    assert rescheduled.l2_engineer_id == 20
    assert rescheduled.planned_duration_minutes == 90
    assert len(repository.cycles) == 2
    assert repository.cycles[-1].status_code == int(AssignmentCycleStatus.ASSIGNED)
    assert len(repository.attempts) == expected_attempt_count
    assert repository.attempts[-1].status_code == int(AssignmentAttemptStatus.PENDING)
    assert (
        len([item for item in repository.schedules if item["closed_at"] is None]) == 1
    )


def test_manager_reschedule_selected_l2_collision_has_no_lifecycle_side_effects() -> (
    None
):
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    first = service.create_card(
        create_payload(omnidesk_ticket_number="123-456789"),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    colliding_start = DEFAULT_PLANNED_START_AT + timedelta(hours=2)
    second = service.create_card(
        create_payload(
            omnidesk_ticket_number="123-456788", planned_start_at=colliding_start
        ),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    before = (
        repository.cards[first.public_id],
        list(repository.events),
        list(repository.audit),
        list(repository.cycles),
        list(repository.attempts),
        list(repository.schedules),
    )

    with pytest.raises(InvalidCardTransitionError, match="l2_unavailable"):
        service.reschedule_card(
            first.public_id,
            actor_user_id=10,
            actor_role_ids={int(RoleId.MANAGER)},
            planned_start_at=colliding_start,
            planned_duration_minutes=60,
            description=None,
            reason="client_requested",
            ip_address=None,
            user_agent=None,
            selected_l2_engineer_id=20,
            now=RESCHEDULE_NOW,
        )

    assert repository.cards[first.public_id] == before[0]
    assert repository.cards[second.public_id].planned_start_at == colliding_start
    assert (
        repository.events,
        repository.audit,
        repository.cycles,
        repository.attempts,
        repository.schedules,
    ) == before[1:]


def test_reschedule_selected_l2_rejection_has_no_lifecycle_side_effects() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    new_start = DEFAULT_PLANNED_START_AT + timedelta(hours=2)
    repository.l2_candidate_absences[20] = [
        TimeInterval(start_at=new_start, end_at=new_start + timedelta(hours=1))
    ]
    before = (
        list(repository.events),
        list(repository.audit),
        list(repository.cycles),
        list(repository.attempts),
    )

    with pytest.raises(InvalidCardTransitionError, match="l2_unavailable"):
        service.reschedule_card(
            card.public_id,
            actor_user_id=10,
            actor_role_ids={int(RoleId.MANAGER)},
            planned_start_at=new_start,
            planned_duration_minutes=60,
            description=None,
            reason="client_requested",
            ip_address=None,
            user_agent=None,
            selected_l2_engineer_id=20,
            now=RESCHEDULE_NOW,
        )

    assert (
        repository.events,
        repository.audit,
        repository.cycles,
        repository.attempts,
    ) == before
    assert repository.cards[card.public_id] == card


def test_only_manager_can_select_l2_during_reschedule() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    before = (list(repository.events), list(repository.audit), list(repository.cycles))

    with pytest.raises(
        CardActionPolicyError, match="manager_required_for_manual_assignment"
    ):
        service.reschedule_card(
            card.public_id,
            actor_user_id=20,
            actor_role_ids={int(RoleId.L2)},
            planned_start_at=DEFAULT_PLANNED_START_AT + timedelta(hours=2),
            planned_duration_minutes=60,
            description=None,
            reason="client_requested",
            ip_address=None,
            user_agent=None,
            selected_l2_engineer_id=20,
            now=RESCHEDULE_NOW,
        )

    assert (repository.events, repository.audit, repository.cycles) == before
    assert repository.cards[card.public_id] == card


def test_reschedule_policy_failure_has_no_side_effects() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    service = make_service(repository)
    card = service.create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    before = (list(repository.events), list(repository.audit), list(repository.cycles))

    with pytest.raises(CardActionPolicyError, match="assigned_l2_required"):
        service.reschedule_card(
            card.public_id,
            actor_user_id=30,
            actor_role_ids={int(RoleId.L2)},
            planned_start_at=DEFAULT_PLANNED_START_AT + timedelta(hours=2),
            planned_duration_minutes=90,
            description=None,
            reason="client_requested",
            ip_address=None,
            user_agent=None,
        )

    assert (repository.events, repository.audit, repository.cycles) == before


@pytest.mark.parametrize(
    "initial_status",
    (
        CardStatus.CREATED,
        CardStatus.IN_PROGRESS,
        CardStatus.COMPLETED,
        CardStatus.CANCELLED,
    ),
)
def test_service_reschedule_status_matrix_rejects_non_time_change_states(
    initial_status: CardStatus,
) -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = repository.create_card(
        CreateCardData(
            omnidesk_ticket_number="123-456789",
            planned_start_at=DEFAULT_PLANNED_START_AT,
            planned_duration_minutes=60,
            created_by_id=10,
            status=initial_status,
            l1_owner_id=11,
            l2_engineer_id=(20 if initial_status != CardStatus.REJECTED else None),
        )
    )
    before = repository.cards[card.public_id]

    with pytest.raises(CardActionPolicyError, match="action_not_allowed_for_status"):
        service.reschedule_card(
            card.public_id,
            actor_user_id=10,
            actor_role_ids={int(RoleId.MANAGER)},
            planned_start_at=DEFAULT_PLANNED_START_AT + timedelta(hours=2),
            planned_duration_minutes=90,
            description=None,
            reason=None,
            ip_address=None,
            user_agent=None,
        )

    assert repository.cards[card.public_id] == before


def test_service_reschedule_allows_owning_l1_on_rejected_card() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    assert card.status_code == int(CardStatus.REJECTED)
    repository.cards[card.public_id] = replace(card, l1_owner_id=11)
    new_start = DEFAULT_PLANNED_START_AT + timedelta(hours=2)
    seed_l2_candidate(repository, 20, planned_start_at=new_start)

    updated = service.reschedule_card(
        card.public_id,
        actor_user_id=11,
        actor_role_ids={int(RoleId.L1)},
        planned_start_at=new_start,
        planned_duration_minutes=60,
        description=None,
        reason="client_requested",
        ip_address=None,
        user_agent=None,
        now=RESCHEDULE_NOW,
    )

    assert updated.status_code == int(CardStatus.ASSIGNED)
    assert updated.l2_engineer_id == 20


def test_service_reschedule_rejects_non_owning_l1_before_mutation() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    repository.cards[card.public_id] = replace(card, l1_owner_id=11)
    before = repository.cards[card.public_id]

    with pytest.raises(CardActionPolicyError, match="assigned_l1_required"):
        service.reschedule_card(
            card.public_id,
            actor_user_id=99,
            actor_role_ids={int(RoleId.L1)},
            planned_start_at=DEFAULT_PLANNED_START_AT + timedelta(hours=2),
            planned_duration_minutes=60,
            description=None,
            reason="client_requested",
            ip_address=None,
            user_agent=None,
        )

    assert repository.cards[card.public_id] == before


def test_known_reschedule_exclusion_is_mapped_to_safe_conflict(monkeypatch) -> None:
    class FakeExclusionViolation(Exception):
        pass

    monkeypatch.setattr(cards_api, "ExclusionViolation", FakeExclusionViolation)
    error = FakeExclusionViolation("database failure")
    error.diag = SimpleNamespace(constraint_name="ex_connection_cards_l2_no_overlap")

    rollback_calls: list[str] = []
    with pytest.raises(HTTPException) as conflict:
        cards_api._handle_change(
            lambda: (_ for _ in ()).throw(error),
            rollback=lambda: rollback_calls.append("rollback"),
        )

    assert conflict.value.status_code == 409
    assert conflict.value.detail == "l2_assignment_conflict"
    assert rollback_calls == ["rollback"]


def test_unknown_reschedule_exclusion_is_not_masked(monkeypatch) -> None:
    class FakeExclusionViolation(Exception):
        pass

    monkeypatch.setattr(cards_api, "ExclusionViolation", FakeExclusionViolation)
    error = FakeExclusionViolation("database failure")
    error.diag = SimpleNamespace(constraint_name="unexpected_constraint")

    with pytest.raises(FakeExclusionViolation, match="database failure"):
        cards_api._handle_change(lambda: (_ for _ in ()).throw(error))


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
    notifications = RecordingNotificationService()
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    service = make_service(repository, notifications)
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
    assert [schedule["owner_id"] for schedule in repository.schedules] == [20, 30]
    assert repository.schedules[0]["closed_at"] is not None
    assert repository.schedules[1]["closed_at"] is None
    assert repository.schedules[1]["cycle_id"] == repository.cycles[0].id
    assert repository.schedules[1]["attempt_id"] == repository.attempts[1].id
    assert {item.recipient_user_id for item in notifications.notifications} == {30}
    assert [item.recipient_user_id for item in notifications.notifications].count(
        30
    ) == 2
    assert all(
        item.source_event_id == len(repository.events)
        for item in notifications.notifications
        if item.recipient_user_id == 30
    )
    lifecycle_counts = (
        len(repository.attempts),
        len(repository.schedules),
        len(repository.events),
        len(notifications.notifications),
    )
    with pytest.raises(InvalidCardTransitionError):
        service.reject_card(
            card.public_id,
            actor_user_id=20,
            rejection_reason="Повторный отказ",
            ip_address=None,
            user_agent=None,
        )
    assert lifecycle_counts == (
        len(repository.attempts),
        len(repository.schedules),
        len(repository.events),
        len(notifications.notifications),
    )


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


def test_complete_rejects_missing_or_inactive_result_code() -> None:
    repository = FakeCardRepository()
    repository.active_result_codes = {1}
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

    with pytest.raises(
        InvalidCardTransitionError, match="result_code_inactive_or_unknown"
    ):
        service.complete_card(
            card.public_id,
            result_code=999,
            engineer_report="Unknown code",
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

    # Inactive code (0 is not in active_result_codes)
    with pytest.raises(
        InvalidCardTransitionError, match="result_code_inactive_or_unknown"
    ):
        service.complete_card(
            card.public_id,
            result_code=0,
            engineer_report="Inactive code",
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

    # Status must not have mutated
    persisted = repository.cards[card.public_id]
    assert persisted.status_code == int(CardStatus.IN_PROGRESS)
    assert persisted.result_code is None
    assert persisted.engineer_report is None

    # Valid active code succeeds
    completed = service.complete_card(
        card.public_id,
        result_code=1,
        engineer_report="Active code succeeded",
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )
    assert completed.status_code == int(CardStatus.COMPLETED)
    assert completed.result_code == 1
    assert completed.engineer_report == "Active code succeeded"


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

    assert response.status_code == 404


def test_generic_cards_create_endpoint_is_not_available() -> None:
    app = create_app()
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

    assert create_response.status_code == 404


def test_cards_api_returns_safe_card_history() -> None:
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
    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    response = client.get(f"/api/v1/cards/{card.public_id}/history")

    assert response.status_code == 200
    assert response.json()[0]["event_label"] == "Карточка создана"
    assert response.json()[0]["actor_label"] == "Сотрудник"
    assert "old_values" not in response.json()[0]
    assert "comment" not in response.json()[0]


def test_cards_api_returns_not_found_for_missing_card_history() -> None:
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

    response = client.get(f"/api/v1/cards/{uuid4()}/history")

    assert response.status_code == 404
    assert response.json()["detail"] == "card_not_found"


def test_cards_api_forbids_unauthorized_action_before_card_mutation() -> None:
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
    event_count = len(repository.events)
    audit_count = len(repository.audit)

    response = client.post(
        f"/api/v1/cards/{card.public_id}/complete",
        json={"result_code": 0, "engineer_report": "Готово"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "action_forbidden"
    assert repository.cards[card.public_id] == card
    assert len(repository.events) == event_count
    assert len(repository.audit) == audit_count


def test_cards_api_manager_action_records_actual_actor() -> None:
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
    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    response = TestClient(app, base_url="https://testserver").post(
        f"/api/v1/cards/{card.public_id}/start", json={"comment": "manager_start"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    assert repository.audit[-1]["actor_user_id"] == 10
    assert repository.events[-1]["actor_user_id"] == 10


def test_cards_api_manager_can_cancel_in_progress_card() -> None:
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
    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    CardService(repository).start_card(
        card.public_id,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    response = TestClient(app, base_url="https://testserver").post(
        f"/api/v1/cards/{card.public_id}/cancel",
        json={"comment": "client_requested"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert repository.audit[-1]["actor_user_id"] == 10


@pytest.mark.parametrize(
    "initial_status",
    (CardStatus.CREATED, CardStatus.COMPLETED, CardStatus.CANCELLED),
)
def test_cards_api_cancel_rejects_forbidden_status_without_mutation(
    initial_status: CardStatus,
) -> None:
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
    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    card = replace(card, status_code=int(initial_status))
    repository.cards[card.public_id] = card
    before = (
        repository.cards[card.public_id],
        list(repository.events),
        list(repository.audit),
    )

    response = TestClient(app, base_url="https://testserver").post(
        f"/api/v1/cards/{card.public_id}/cancel", json={"comment": "client_requested"}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "action_not_allowed_for_status"
    assert (
        repository.cards[card.public_id],
        repository.events,
        repository.audit,
    ) == before


@pytest.mark.parametrize(
    "initial_status",
    (
        CardStatus.CREATED,
        CardStatus.IN_PROGRESS,
        CardStatus.COMPLETED,
        CardStatus.CANCELLED,
    ),
)
def test_cards_api_reschedule_rejects_forbidden_status_without_mutation(
    initial_status: CardStatus,
) -> None:
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
    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    card = replace(card, status_code=int(initial_status))
    repository.cards[card.public_id] = card
    before = (
        repository.cards[card.public_id],
        list(repository.events),
        list(repository.audit),
    )

    response = TestClient(app, base_url="https://testserver").post(
        f"/api/v1/cards/{card.public_id}/l1/reschedule",
        json={
            "planned_start_at": (
                DEFAULT_PLANNED_START_AT + timedelta(hours=2)
            ).isoformat(),
            "planned_duration_minutes": 60,
            "reason": "client_requested",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "action_not_allowed_for_status"
    assert (
        repository.cards[card.public_id],
        repository.events,
        repository.audit,
    ) == before


def test_cards_api_reschedule_selected_l2_uses_manual_assignment_contract() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: repository
    app.dependency_overrides[get_card_service] = lambda: CardService(
        repository, clock=lambda: RESCHEDULE_NOW
    )
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        id=10,
        username="manager",
        password_hash="unused",
        full_name="Руководитель",
        email=None,
        roles=(RoleRecord(id=3, name="Руководитель"),),
    )
    card = CardService(repository).create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    new_start = DEFAULT_PLANNED_START_AT + timedelta(hours=2)
    seed_l2_candidate(repository, 20, planned_start_at=new_start)
    seed_l2_candidate(repository, 30, planned_start_at=new_start)

    response = TestClient(app, base_url="https://testserver").post(
        f"/api/v1/cards/{card.public_id}/l1/reschedule",
        json={
            "planned_start_at": new_start.isoformat(),
            "planned_duration_minutes": 60,
            "reason": "client_requested",
            "l2_engineer_id": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "assigned"
    assert response.json()["l2_engineer_id"] == 30
    assert repository.events[-1]["comment"] == "manager_manual_assignment"
    assert repository.distribution_last_user_id == 20


def test_cards_api_rejects_non_manager_selected_l2_without_mutation() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: repository
    app.dependency_overrides[get_card_service] = lambda: CardService(
        repository, clock=lambda: RESCHEDULE_NOW
    )
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        id=20,
        username="l2",
        password_hash="unused",
        full_name="Инженер L2",
        email=None,
        roles=(RoleRecord(id=2, name="Инженер L2"),),
    )
    card = CardService(repository).create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    before = repository.cards[card.public_id]
    events_before = list(repository.events)
    audit_before = list(repository.audit)
    new_start = DEFAULT_PLANNED_START_AT + timedelta(hours=2)
    seed_l2_candidate(repository, 20, planned_start_at=new_start)
    seed_l2_candidate(repository, 30, planned_start_at=new_start)

    response = TestClient(app, base_url="https://testserver").post(
        f"/api/v1/cards/{card.public_id}/l1/reschedule",
        json={
            "planned_start_at": new_start.isoformat(),
            "planned_duration_minutes": 60,
            "reason": "client_requested",
            "l2_engineer_id": 30,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "manager_required_for_manual_assignment"
    assert repository.cards[card.public_id] == before
    assert repository.events == events_before
    assert repository.audit == audit_before


def test_cards_api_reschedule_without_selected_l2_uses_automatic_distribution() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: repository
    app.dependency_overrides[get_card_service] = lambda: CardService(
        repository, clock=lambda: RESCHEDULE_NOW
    )
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        id=10,
        username="manager",
        password_hash="unused",
        full_name="Руководитель",
        email=None,
        roles=(RoleRecord(id=3, name="Руководитель"),),
    )
    card = CardService(repository).create_card(
        create_payload(), actor_user_id=10, ip_address=None, user_agent=None
    )
    assert card.l2_engineer_id == 20
    new_start = DEFAULT_PLANNED_START_AT + timedelta(hours=2)
    seed_l2_candidate(repository, 20, planned_start_at=new_start)
    seed_l2_candidate(repository, 30, planned_start_at=new_start)

    response = TestClient(app, base_url="https://testserver").post(
        f"/api/v1/cards/{card.public_id}/l1/reschedule",
        json={
            "planned_start_at": new_start.isoformat(),
            "planned_duration_minutes": 60,
            "reason": "client_requested",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "assigned"
    assert response.json()["l2_engineer_id"] == 30
    assert repository.distribution_last_user_id == 30


def test_manager_manual_assignment_computes_inside_schedule_flag() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)

    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
        manual_assignment=True,
        allow_out_of_hours=True,
    )

    assert card.out_of_hours_flag is False
    assert card.l2_engineer_id == 20
    assert len(repository.cycles) == 1
    assert len(repository.attempts) == 1


def test_manager_manual_assignment_computes_out_of_hours_flag() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20, schedule_end=time(1))

    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
        manual_assignment=True,
        allow_out_of_hours=True,
    )

    assert card.out_of_hours_flag is True
    assert card.l2_engineer_id == 20
    assert len(repository.cycles) == 1
    assert len(repository.attempts) == 1


def test_l2_self_create_allows_out_of_hours_and_marks_card() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20, schedule_end=time(1))
    plan = validate_role_create(
        scenario=CreateScenario.L2_SELF,
        planned_start_at=DEFAULT_PLANNED_START_AT,
        planned_duration_minutes=60,
        now=DEFAULT_PLANNED_START_AT - timedelta(hours=3),
    )

    card = CardService(repository).create_card(
        create_payload(),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        allow_out_of_hours=True,
        role_create_plan=plan,
    )

    assert card.l2_engineer_id == 20
    assert card.out_of_hours_flag is True
    assert repository.cycles == []
    assert repository.attempts == []


def test_l2_urgent_create_allows_out_of_hours_and_marks_card() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20, schedule_end=time(1))
    plan = validate_role_create(
        scenario=CreateScenario.L2_URGENT,
        planned_start_at=DEFAULT_PLANNED_START_AT,
        planned_duration_minutes=60,
        urgent_reason="incident",
        now=DEFAULT_PLANNED_START_AT - timedelta(hours=3),
    )

    card = CardService(repository).create_card(
        create_payload(),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        allow_out_of_hours=True,
        role_create_plan=plan,
    )

    assert card.l2_engineer_id == 20
    assert card.out_of_hours_flag is True
    assert card.urgency_code == 1
    assert repository.cycles == []
    assert repository.attempts == []


def _urgent_plan() -> object:
    return validate_role_create(
        scenario=CreateScenario.L2_URGENT,
        planned_start_at=DEFAULT_PLANNED_START_AT,
        planned_duration_minutes=60,
        urgent_reason="production incident",
        now=DEFAULT_PLANNED_START_AT - timedelta(hours=3),
    )


def test_l2_urgent_collision_displaces_and_reassigns_to_next_l2() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    repository.list_active_manager_recipients = lambda: [
        ManagerRecipient(900, telegram_chat_id="manager-chat")
    ]
    normal = CardService(repository).create_card(
        create_payload(l2_engineer_id=20, omnidesk_ticket_number="123-456788"),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        manual_assignment=True,
        allow_out_of_hours=True,
    )
    notifications = RecordingNotificationService()
    urgent = CardService(repository, notifications).create_card(
        create_payload(omnidesk_ticket_number="123-456789"),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        allow_out_of_hours=True,
        role_create_plan=_urgent_plan(),
    )

    displaced = repository.get_card_by_id_for_update(normal.id)
    assert urgent.urgency_code == 1
    assert urgent.l2_engineer_id == 20
    assert displaced is not None
    assert displaced.status_code == int(CardStatus.ASSIGNED)
    assert displaced.l2_engineer_id == 30
    assert any(
        event["event_type"] == CardEventType.URGENT_COLLISION
        and event["comment"] == "urgent_collision"
        for event in repository.events
    )
    assert any(
        item.event == "urgent_collision" and item.recipient_user_id == 20
        for item in notifications.notifications
    )
    assert any(
        item.event == "manager_escalation" and item.recipient_user_id == 900
        for item in notifications.notifications
    )
    assert repository.cycles[-1].card_id == normal.id


def test_l2_urgent_collision_exhaustion_rejects_and_assigns_l1() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l1_candidate(repository, 100)
    repository.list_active_manager_recipients = lambda: [
        ManagerRecipient(900, telegram_chat_id="manager-chat")
    ]
    normal = CardService(repository).create_card(
        create_payload(l2_engineer_id=20, omnidesk_ticket_number="123-456788"),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        manual_assignment=True,
        allow_out_of_hours=True,
    )
    notifications = RecordingNotificationService()
    urgent = CardService(repository, notifications).create_card(
        create_payload(omnidesk_ticket_number="123-456789"),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        allow_out_of_hours=True,
        role_create_plan=_urgent_plan(),
    )

    displaced = repository.get_card_by_id_for_update(normal.id)
    assert urgent.status_code == int(CardStatus.ASSIGNED)
    assert displaced is not None
    assert displaced.status_code == int(CardStatus.REJECTED)
    assert displaced.l2_engineer_id is None
    assert displaced.l1_owner_id == 100
    collision_intents = [
        item for item in notifications.notifications if item.event == "urgent_collision"
    ]
    assert {item.recipient_user_id for item in collision_intents} == {20, 100}

    event_id = next(
        index + 1
        for index, event in enumerate(repository.events)
        if event["card_id"] == normal.id and event["comment"] == "urgent_collision"
    )
    before = len(notifications.notifications)
    CardService(repository, notifications)._notify_urgent_collision(
        displaced=displaced,
        event_id=event_id,
        affected_l1_id=100,
        affected_l2_id=20,
        ip_address=None,
        user_agent=None,
    )
    assert len(notifications.notifications) == before


def test_l2_urgent_collision_rejects_in_progress_without_mutations() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    service = CardService(repository)
    normal = service.create_card(
        create_payload(l2_engineer_id=20, omnidesk_ticket_number="123-456788"),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        manual_assignment=True,
        allow_out_of_hours=True,
    )
    service.start_card(
        normal.public_id,
        actor_user_id=20,
        comment="Started",
        ip_address=None,
        user_agent=None,
    )
    before_cards = dict(repository.cards)
    before_events = list(repository.events)
    before_audit = list(repository.audit)
    before_cycles = list(repository.cycles)
    before_attempts = list(repository.attempts)

    with pytest.raises(
        InvalidCardTransitionError, match="urgent_collision_in_progress"
    ):
        service.create_card(
            create_payload(omnidesk_ticket_number="123-456789"),
            actor_user_id=20,
            ip_address=None,
            user_agent=None,
            allow_out_of_hours=True,
            role_create_plan=_urgent_plan(),
        )

    assert repository.cards == before_cards
    assert repository.events == before_events
    assert repository.audit == before_audit
    assert repository.cycles == before_cycles
    assert repository.attempts == before_attempts

    in_progress = repository.cards[normal.public_id]
    assert in_progress.status_code == int(CardStatus.IN_PROGRESS)
    assert in_progress.l2_engineer_id == 20


@pytest.mark.parametrize("initial_status", (CardStatus.ASSIGNED, CardStatus.CONFIRMED))
def test_l2_urgent_collision_displacement_clears_overdue_flag(
    initial_status: CardStatus,
) -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l2_candidate(repository, 30)
    service = CardService(repository)
    normal = service.create_card(
        create_payload(l2_engineer_id=20, omnidesk_ticket_number="123-456788"),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        manual_assignment=True,
        allow_out_of_hours=True,
    )
    overdue_card, _ = repository.mark_l2_assignment_overdue(card_id=normal.id)
    assert overdue_card.overdue_flag is True

    if initial_status == CardStatus.CONFIRMED:
        service.confirm_card(
            normal.public_id,
            actor_user_id=20,
            comment="Confirmed while overdue",
            ip_address=None,
            user_agent=None,
        )
        confirmed = repository.cards[normal.public_id]
        assert confirmed.status_code == int(CardStatus.CONFIRMED)
        assert confirmed.overdue_flag is True

    urgent = service.create_card(
        create_payload(omnidesk_ticket_number="123-456789"),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        allow_out_of_hours=True,
        role_create_plan=_urgent_plan(),
    )

    displaced = repository.get_card_by_id_for_update(normal.id)
    assert urgent.status_code == int(CardStatus.ASSIGNED)
    assert urgent.l2_engineer_id == 20
    assert displaced is not None
    assert displaced.overdue_flag is False
    assert displaced.l2_engineer_id == 30
    assert displaced.status_code == int(CardStatus.ASSIGNED)


@pytest.mark.parametrize("reason", ("absence", "collision"))
def test_manager_manual_assignment_never_bypasses_unavailability(reason: str) -> None:
    repository = FakeCardRepository()
    start = DEFAULT_PLANNED_START_AT
    seed_l2_candidate(
        repository,
        20,
        schedule_end=time(1),
        absence=(
            TimeInterval(start_at=start, end_at=start + timedelta(minutes=60))
            if reason == "absence"
            else None
        ),
    )
    service = CardService(repository)
    if reason == "collision":
        service.create_card(
            create_payload(l2_engineer_id=20, omnidesk_ticket_number="123-456788"),
            actor_user_id=10,
            ip_address=None,
            user_agent=None,
            manual_assignment=True,
            allow_out_of_hours=True,
        )
    before = (len(repository.cards), len(repository.cycles), len(repository.attempts))

    with pytest.raises(InvalidCardTransitionError, match="l2_unavailable"):
        service.create_card(
            create_payload(l2_engineer_id=20),
            actor_user_id=10,
            ip_address=None,
            user_agent=None,
            manual_assignment=True,
            allow_out_of_hours=True,
        )

    assert (
        len(repository.cards),
        len(repository.cycles),
        len(repository.attempts),
    ) == before


def test_non_exempt_selected_l2_has_no_out_of_hours_side_effects() -> None:
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20, schedule_end=time(1))

    with pytest.raises(InvalidCardTransitionError, match="out_of_hours_not_permitted"):
        CardService(repository).create_card(
            create_payload(l2_engineer_id=20),
            actor_user_id=20,
            ip_address=None,
            user_agent=None,
            manual_assignment=True,
        )

    assert repository.cards == {}
    assert repository.cycles == []
    assert repository.attempts == []


def test_complete_card_persists_actual_duration_and_creates_note_intent() -> None:
    repository = FakeCardRepository()
    repository.active_result_codes = {1}
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
        result_code=1,
        engineer_report="Completed successfully",
        actual_duration_minutes=45,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    assert completed.status_code == int(CardStatus.COMPLETED)
    assert completed.actual_duration_minutes == 45
    assert completed.result_code == 1
    assert completed.engineer_report == "Completed successfully"

    # Outbox intent must have been created in the same transaction
    assert len(repository.omnidesk_note_intents) == 1
    intent = repository.omnidesk_note_intents[0]
    assert intent["card_id"] == completed.id
    assert intent["omnidesk_ticket_number"] == completed.omnidesk_ticket_number
    assert intent["payload"]["card_id"] == completed.id
    assert intent["payload"]["ticket_number"] == completed.omnidesk_ticket_number
    assert intent["payload"]["result_code"] == 1
    assert intent["payload"]["engineer_report"] == "Completed successfully"
    assert intent["payload"]["actual_duration_minutes"] == 45
    assert "case_id" not in intent["payload"]


def test_start_and_complete_use_injected_clock() -> None:
    repository = FakeCardRepository()
    repository.active_result_codes = {1}
    started_at = datetime(2026, 9, 22, 8, tzinfo=UTC)
    completed_at = started_at + timedelta(minutes=37)
    clock_values = iter((started_at, completed_at))
    service = CardService(repository, clock=lambda: next(clock_values))
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
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
        result_code=1,
        engineer_report="Completed",
        actual_duration_minutes=37,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    assert started.actual_start_at == started_at
    assert completed.actual_end_at == completed_at


def test_complete_card_rejects_non_positive_actual_duration() -> None:
    repository = FakeCardRepository()
    repository.active_result_codes = {1}
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

    with pytest.raises(
        InvalidCardTransitionError, match="actual_duration_must_be_positive"
    ):
        service.complete_card(
            card.public_id,
            result_code=1,
            engineer_report="Completed",
            actual_duration_minutes=0,
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

    with pytest.raises(
        InvalidCardTransitionError, match="actual_duration_must_be_positive"
    ):
        service.complete_card(
            card.public_id,
            result_code=1,
            engineer_report="Completed",
            actual_duration_minutes=-10,
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )


def test_complete_card_optional_actual_duration_defaults_to_none() -> None:
    repository = FakeCardRepository()
    repository.active_result_codes = {1}
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
        result_code=1,
        engineer_report="Completed without duration",
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    assert completed.actual_duration_minutes is None
    assert len(repository.omnidesk_note_intents) == 1
    assert (
        "actual_duration_minutes" not in repository.omnidesk_note_intents[0]["payload"]
    )


def test_completed_retroactive_validates_result_and_derives_duration() -> None:
    now = datetime(2026, 9, 20, 12, tzinfo=UTC)
    repository = FakeCardRepository()
    repository.active_result_codes = {5}
    seed_l2_candidate(repository, 20, planned_start_at=now - timedelta(hours=2))
    service = make_service(repository)

    # Inactive result code is rejected
    inactive_plan = validate_role_create(
        scenario=CreateScenario.L2_RETROACTIVE,
        planned_start_at=now - timedelta(hours=2),
        planned_duration_minutes=90,
        result_code=99,
        engineer_report="Done",
        now=now,
    )
    with pytest.raises(
        InvalidCardTransitionError, match="result_code_inactive_or_unknown"
    ):
        service.create_card(
            create_payload(
                planned_start_at=now - timedelta(hours=2), planned_duration_minutes=90
            ),
            actor_user_id=20,
            ip_address=None,
            user_agent=None,
            role_create_plan=inactive_plan,
        )
    assert repository.cards == {}
    assert repository.omnidesk_note_intents == []

    # Active result code succeeds, duration derived from start/end (90 min), note intent created
    active_plan = validate_role_create(
        scenario=CreateScenario.L2_RETROACTIVE,
        planned_start_at=now - timedelta(hours=2),
        planned_duration_minutes=90,
        result_code=5,
        engineer_report="Retroactive done",
        now=now,
    )
    completed = service.create_card(
        create_payload(
            planned_start_at=now - timedelta(hours=2), planned_duration_minutes=90
        ),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        role_create_plan=active_plan,
    )
    assert completed.status_code == int(CardStatus.COMPLETED)
    assert completed.actual_duration_minutes == 90
    assert completed.result_code == 5
    assert completed.engineer_report == "Retroactive done"
    assert len(repository.omnidesk_note_intents) == 1
    intent = repository.omnidesk_note_intents[0]
    assert intent["card_id"] == completed.id
    assert intent["payload"]["actual_duration_minutes"] == 90
    assert intent["payload"]["result_code"] == 5
    assert "case_id" not in intent["payload"]


def test_in_progress_retroactive_does_not_create_note_intent() -> None:
    now = datetime(2026, 9, 20, 12, tzinfo=UTC)
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20, planned_start_at=now - timedelta(minutes=30))
    service = make_service(repository)

    in_progress_plan = validate_role_create(
        scenario=CreateScenario.L2_RETROACTIVE,
        planned_start_at=now - timedelta(minutes=30),
        planned_duration_minutes=60,
        now=now,
    )
    card = service.create_card(
        create_payload(
            planned_start_at=now - timedelta(minutes=30), planned_duration_minutes=60
        ),
        actor_user_id=20,
        ip_address=None,
        user_agent=None,
        role_create_plan=in_progress_plan,
    )
    assert card.status_code == int(CardStatus.IN_PROGRESS)
    assert repository.omnidesk_note_intents == []


def test_omnidesk_note_intent_idempotency() -> None:
    repository = FakeCardRepository()
    intent_id_1 = repository.create_omnidesk_internal_note_intent(
        card_id=1,
        source_event_id=100,
        omnidesk_ticket_number="123-456789",
        payload={"card_id": 1, "result_code": 0},
    )
    assert intent_id_1 > 0
    assert len(repository.omnidesk_note_intents) == 1

    # Duplicate call with same source_event_id is idempotent
    intent_id_2 = repository.create_omnidesk_internal_note_intent(
        card_id=1,
        source_event_id=100,
        omnidesk_ticket_number="123-456789",
        payload={"card_id": 1, "result_code": 0},
    )
    assert intent_id_2 == 0
    assert len(repository.omnidesk_note_intents) == 1


def test_end_pending_result_api_success() -> None:
    repository = FakeCardRepository()

    seed_l2_candidate(repository, 101)
    repository.users = [
        type(
            "User", (), {"id": 101, "roles": [type("Role", (), {"id": RoleId.L2})()]}
        )()
    ]

    service = CardService(repository=repository)

    card = service.create_card(
        CardCreateRequest(
            omnidesk_ticket_number="101-123456",
            planned_start_at=datetime.now(UTC),
            planned_duration_minutes=30,
            l2_engineer_id=101,
        ),
        actor_user_id=101,
        ip_address="127.0.0.1",
        user_agent="test",
        manual_assignment=True,
        allow_out_of_hours=True,
    )
    service.confirm_card(
        card.public_id,
        actor_user_id=101,
        comment=None,
        ip_address=None,
        user_agent=None,
    )
    service.start_card(
        card.public_id,
        actor_user_id=101,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    import app.api.cards as cards_api
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app_fixture = FastAPI()
    app_fixture.include_router(cards_api.router)

    from app.auth.dependencies import get_current_user

    app_fixture.dependency_overrides[get_current_user] = lambda: repository.users[0]
    app_fixture.dependency_overrides[cards_api.get_card_service] = lambda: service

    test_client = TestClient(app_fixture)

    response = test_client.post(
        f"/api/v1/cards/{card.public_id}/end-pending-result",
        headers={"Authorization": "Bearer test"},
        json={"comment": "ending without result"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed_pending_result"

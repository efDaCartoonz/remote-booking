from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.assignments.types import (
    AssignmentAttemptRecord,
    AssignmentCycleRecord,
    L2DistributionCandidate,
    L1DistributionCandidate,
)
from app.cards.constants import (
    ActorType,
    AssignmentAttemptStatus,
    AssignmentCycleStatus,
    AuditAction,
    CardEventType,
    CardStatus,
    DistributionPool,
)
from app.cards.repository import CardRecord


class AssignmentRepository(Protocol):
    def list_l1_distribution_candidates(
        self, *, planned_start_at: datetime, planned_end_at: datetime
    ) -> list[L1DistributionCandidate]: ...

    def update_l1_owner(self, *, card_id: int, l1_owner_id: int | None) -> CardRecord: ...
    def list_l2_distribution_candidates(
        self, *, planned_start_at: datetime, planned_end_at: datetime
    ) -> list[L2DistributionCandidate]: ...

    def get_distribution_last_user_id_for_update(
        self, pool: DistributionPool
    ) -> int | None: ...

    def update_distribution_state(
        self, *, pool: DistributionPool, last_user_id: int
    ) -> None: ...

    def get_next_assignment_cycle_number(self, card_id: int) -> int: ...

    def create_assignment_cycle(
        self,
        *,
        card_id: int,
        cycle_number: int,
        status: AssignmentCycleStatus,
    ) -> AssignmentCycleRecord: ...

    def update_assignment_cycle_status(
        self, *, cycle_id: int, status: AssignmentCycleStatus
    ) -> None: ...

    def get_current_assignment_cycle_for_update(
        self, card_id: int
    ) -> AssignmentCycleRecord | None: ...

    def create_assignment_attempt(
        self,
        *,
        cycle_id: int,
        card_id: int,
        l2_engineer_id: int,
        status: AssignmentAttemptStatus,
    ) -> AssignmentAttemptRecord: ...

    def get_pending_assignment_attempt_for_update(
        self, *, card_id: int, l2_engineer_id: int
    ) -> AssignmentAttemptRecord | None: ...

    def list_attempted_l2_engineer_ids(self, cycle_id: int) -> set[int]: ...

    def update_assignment_attempt_response(
        self,
        *,
        attempt_id: int,
        status: AssignmentAttemptStatus,
        actor_user_id: int,
        rejection_reason: str | None,
    ) -> AssignmentAttemptRecord | None: ...

    def update_card_distribution_result(
        self,
        *,
        card_id: int,
        status: CardStatus,
        l2_engineer_id: int | None,
        increment_unsuccessful_cycle_count: bool,
    ) -> CardRecord: ...

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
    ) -> None: ...

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
    ) -> None: ...

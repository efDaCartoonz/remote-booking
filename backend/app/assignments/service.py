from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.assignments.repository import AssignmentRepository
from app.assignments.types import L2DistributionCandidate, TimeInterval
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

NO_AVAILABLE_L2_REASON = "no_available_l2_candidates"


class L2DistributionService:
    def __init__(self, repository: AssignmentRepository) -> None:
        self.repository = repository

    def run_initial_distribution(
        self,
        card: CardRecord,
        *,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        if CardStatus(card.status_code) != CardStatus.CREATED:
            return card

        planned_end_at = card.planned_start_at + timedelta(
            minutes=card.planned_duration_minutes
        )
        cycle = self.repository.create_assignment_cycle(
            card_id=card.id,
            cycle_number=self.repository.get_next_assignment_cycle_number(card.id),
            status=AssignmentCycleStatus.IN_PROGRESS,
        )
        self.repository.add_audit_log(
            actor_user_id=None,
            actor_type=ActorType.SYSTEM,
            action=AuditAction.CREATE,
            entity_type="assignment_cycle",
            entity_id=cycle.id,
            old_values=None,
            new_values={
                "card_id": cycle.card_id,
                "cycle_number": cycle.cycle_number,
                "status_code": cycle.status_code,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

        last_user_id = self.repository.get_distribution_last_user_id_for_update(
            DistributionPool.L2
        )
        candidates = [
            candidate
            for candidate in self.repository.list_l2_distribution_candidates(
                planned_start_at=card.planned_start_at,
                planned_end_at=planned_end_at,
            )
            if _candidate_is_available(
                candidate,
                planned_start_at=card.planned_start_at,
                planned_end_at=planned_end_at,
            )
        ]
        selected_l2_id = _choose_round_robin_candidate(candidates, last_user_id)
        if selected_l2_id is None:
            return self._reject_without_candidates(
                card,
                cycle_id=cycle.id,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        attempt = self.repository.create_assignment_attempt(
            cycle_id=cycle.id,
            card_id=card.id,
            l2_engineer_id=selected_l2_id,
            status=AssignmentAttemptStatus.PENDING,
        )
        self.repository.add_audit_log(
            actor_user_id=None,
            actor_type=ActorType.SYSTEM,
            action=AuditAction.CREATE,
            entity_type="assignment_attempt",
            entity_id=attempt.id,
            old_values=None,
            new_values={
                "cycle_id": attempt.cycle_id,
                "card_id": attempt.card_id,
                "l2_engineer_id": attempt.l2_engineer_id,
                "status_code": attempt.status_code,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.repository.update_assignment_cycle_status(
            cycle_id=cycle.id, status=AssignmentCycleStatus.ASSIGNED
        )
        self.repository.update_distribution_state(
            pool=DistributionPool.L2, last_user_id=selected_l2_id
        )
        updated = self.repository.update_card_distribution_result(
            card_id=card.id,
            status=CardStatus.ASSIGNED,
            l2_engineer_id=selected_l2_id,
            increment_unsuccessful_cycle_count=False,
        )
        self._record_card_update(
            old_card=card,
            updated_card=updated,
            event_type=CardEventType.ENGINEER_ASSIGNED,
            comment=None,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return updated

    def _reject_without_candidates(
        self,
        card: CardRecord,
        *,
        cycle_id: int,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        self.repository.update_assignment_cycle_status(
            cycle_id=cycle_id, status=AssignmentCycleStatus.ALL_REJECTED
        )
        updated = self.repository.update_card_distribution_result(
            card_id=card.id,
            status=CardStatus.REJECTED,
            l2_engineer_id=None,
            increment_unsuccessful_cycle_count=True,
        )
        self._record_card_update(
            old_card=card,
            updated_card=updated,
            event_type=CardEventType.STATUS_CHANGED,
            comment=NO_AVAILABLE_L2_REASON,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return updated

    def _record_card_update(
        self,
        *,
        old_card: CardRecord,
        updated_card: CardRecord,
        event_type: CardEventType,
        comment: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        old_snapshot = _card_distribution_snapshot(old_card)
        new_snapshot = _card_distribution_snapshot(updated_card)
        self.repository.add_card_event(
            card_id=updated_card.id,
            event_type=event_type,
            actor_user_id=None,
            actor_type=ActorType.SYSTEM,
            old_values=old_snapshot,
            new_values=new_snapshot,
            comment=comment,
        )
        self.repository.add_audit_log(
            actor_user_id=None,
            actor_type=ActorType.SYSTEM,
            action=AuditAction.UPDATE,
            entity_type="connection_card",
            entity_id=updated_card.id,
            old_values=old_snapshot,
            new_values=new_snapshot,
            ip_address=ip_address,
            user_agent=user_agent,
        )


def _choose_round_robin_candidate(
    candidates: list[L2DistributionCandidate], last_user_id: int | None
) -> int | None:
    ordered_user_ids = sorted(candidate.user_id for candidate in candidates)
    if not ordered_user_ids:
        return None
    if last_user_id is None:
        return ordered_user_ids[0]
    for user_id in ordered_user_ids:
        if user_id > last_user_id:
            return user_id
    return ordered_user_ids[0]


def _candidate_is_available(
    candidate: L2DistributionCandidate, *, planned_start_at, planned_end_at
) -> bool:
    return (
        _schedule_covers_interval(
            candidate, planned_start_at=planned_start_at, planned_end_at=planned_end_at
        )
        and not _has_overlap(
            candidate.absences,
            planned_start_at=planned_start_at,
            planned_end_at=planned_end_at,
        )
        and not _has_overlap(
            candidate.active_cards,
            planned_start_at=planned_start_at,
            planned_end_at=planned_end_at,
        )
    )


def _schedule_covers_interval(
    candidate: L2DistributionCandidate, *, planned_start_at, planned_end_at
) -> bool:
    for schedule in candidate.schedules:
        try:
            timezone = ZoneInfo(schedule.timezone)
        except ZoneInfoNotFoundError:
            continue
        local_start = planned_start_at.astimezone(timezone)
        local_end = planned_end_at.astimezone(timezone)
        local_date = local_start.date()
        if local_end.date() != local_date:
            continue
        if local_start.isoweekday() != schedule.weekday:
            continue
        if schedule.valid_from is not None and local_date < schedule.valid_from:
            continue
        if schedule.valid_to is not None and local_date > schedule.valid_to:
            continue
        if (
            local_start.time() >= schedule.start_time
            and local_end.time() <= schedule.end_time
        ):
            return True
    return False


def _has_overlap(
    intervals: tuple[TimeInterval, ...], *, planned_start_at, planned_end_at
) -> bool:
    return any(
        interval.start_at < planned_end_at and planned_start_at < interval.end_at
        for interval in intervals
    )


def _card_distribution_snapshot(card: CardRecord) -> dict[str, object]:
    return {
        "id": card.id,
        "public_id": str(card.public_id),
        "status_code": card.status_code,
        "l2_engineer_id": card.l2_engineer_id,
        "unsuccessful_cycle_count": card.unsuccessful_cycle_count,
    }

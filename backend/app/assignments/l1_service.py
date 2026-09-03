from __future__ import annotations

from datetime import timedelta

from app.cards.constants import ActorType, AuditAction, CardEventType, DistributionPool
from app.cards.repository import CardRecord


class L1DistributionService:
    """Assigns the follow-up owner; it deliberately does not create L2 attempts."""

    def __init__(self, repository) -> None:
        self.repository = repository

    def assign(self, card: CardRecord, *, ip_address: str | None, user_agent: str | None) -> CardRecord:
        from app.assignments.service import _candidate_is_available, _choose_round_robin_candidate
        list_candidates = getattr(self.repository, "list_l1_distribution_candidates", None)
        if list_candidates is None:
            return card
        end = card.planned_start_at + timedelta(minutes=card.planned_duration_minutes)
        candidates = [
            c
            for c in list_candidates(
                planned_start_at=card.planned_start_at, planned_end_at=end
            )
            if _candidate_is_available(
                c, planned_start_at=card.planned_start_at, planned_end_at=end
            )
        ]
        last = self.repository.get_distribution_last_user_id_for_update(DistributionPool.L1)
        selected = _choose_round_robin_candidate(candidates, last)
        if selected is None:
            self.repository.add_audit_log(actor_user_id=None, actor_type=ActorType.SYSTEM,
                action=AuditAction.UPDATE, entity_type="l1_assignment", entity_id=card.id,
                old_values={"l1_owner_id": card.l1_owner_id}, new_values={"l1_owner_id": None, "reason": "no_available_l1_candidates"},
                ip_address=ip_address, user_agent=user_agent)
            return card
        updated = self.repository.update_l1_owner(card_id=card.id, l1_owner_id=selected)
        self.repository.update_distribution_state(pool=DistributionPool.L1, last_user_id=selected)
        self.repository.add_card_event(card_id=card.id, event_type=CardEventType.ENGINEER_ASSIGNED,
            actor_user_id=None, actor_type=ActorType.SYSTEM,
            old_values={"l1_owner_id": card.l1_owner_id}, new_values={"l1_owner_id": selected}, comment="l1_assigned")
        self.repository.add_audit_log(actor_user_id=None, actor_type=ActorType.SYSTEM,
            action=AuditAction.UPDATE, entity_type="l1_assignment", entity_id=card.id,
            old_values={"l1_owner_id": card.l1_owner_id}, new_values={"l1_owner_id": selected},
            ip_address=ip_address, user_agent=user_agent)
        return updated

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.cards.constants import ActorType, AuditAction, CardEventType
from app.notifications import NotificationService

if TYPE_CHECKING:
    from app.cards.repository import CardRecord


@dataclass(frozen=True)
class ManagerRecipient:
    user_id: int
    telegram_chat_id: str | None = None
    bitrix24_user_id: str | None = None


@dataclass(frozen=True)
class ManagerEscalationResult:
    managers: int
    channels: int
    created_intents: int
    deduplicated_intents: int


class ManagerEscalationRepository(Protocol):
    def list_active_manager_recipients(self) -> list[ManagerRecipient]: ...
    def add_audit_log(self, **kwargs) -> None: ...


EVENTS = {
    "no_available_l2_candidates",
    "all_l2_candidates_rejected",
    "repeated_unsuccessful_l2_cycle",
    "no_available_l1_candidates",
}


class ManagerEscalationService:
    def __init__(self, repository: ManagerEscalationRepository, notifications: NotificationService | None):
        self.repository = repository
        self.notifications = notifications

    def escalate(
        self,
        *,
        card: "CardRecord",
        source_event_id: int,
        source_event_type: CardEventType,
        reason: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> ManagerEscalationResult:
        if reason not in EVENTS:
            raise ValueError("unsupported manager escalation reason")
        list_recipients = getattr(self.repository, "list_active_manager_recipients", None)
        if list_recipients is None:
            return ManagerEscalationResult(0, 0, 0, 0)
        recipients = list(list_recipients()) if list_recipients is not None else []
        channels = sum(bool(r.telegram_chat_id) + bool(r.bitrix24_user_id) for r in recipients)
        if not recipients:
            outcome = "manager_escalation_no_recipients"
        elif not channels:
            outcome = "manager_escalation_no_deliverable_recipients"
        else:
            outcome = reason
        self.repository.add_audit_log(
            actor_user_id=None, actor_type=ActorType.SYSTEM, action=AuditAction.UPDATE,
            entity_type="manager_escalation", entity_id=card.id, old_values=None,
            new_values={"reason": outcome, "source_event_id": source_event_id},
            ip_address=ip_address, user_agent=user_agent,
        )
        if self.notifications is None or not channels:
            return ManagerEscalationResult(len(recipients), channels, 0, 0)
        before = getattr(self.notifications, "notifications", None)
        existing = len(before) if before is not None else 0
        for recipient in recipients:
            for channel, value in (("telegram", recipient.telegram_chat_id), ("bitrix24", recipient.bitrix24_user_id)):
                if value:
                    self.notifications.notify(
                        event="manager_escalation", card_id=card.id,
                        source_event_id=source_event_id, source_event_type=int(source_event_type),
                        recipient_user_id=recipient.user_id, channel=channel,
                        payload={"card_id": card.id, "assignment": "manager_escalation"},
                    )
        created = len(before) - existing if before is not None else channels
        return ManagerEscalationResult(len(recipients), channels, created, channels - created)

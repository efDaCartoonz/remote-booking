from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.cards.constants import (
    ALLOWED_STATUS_TRANSITIONS,
    AssignmentMethod,
    AuditAction,
    CardEventType,
    CardStatus,
)
from app.cards.repository import (
    CardRecord,
    CardRepository,
    CreateCardData,
    StatusUpdateData,
)
from app.cards.schemas import CardCreateRequest


class CardNotFoundError(Exception):
    pass


class InvalidCardTransitionError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class CardService:
    def __init__(self, repository: CardRepository) -> None:
        self.repository = repository

    def create_card(
        self,
        payload: CardCreateRequest,
        *,
        actor_user_id: int,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        status = CardStatus.ASSIGNED if payload.l2_engineer_id is not None else CardStatus.CREATED
        assignment_method_code = payload.assignment_method_code
        if assignment_method_code is None:
            assignment_method_code = (
                int(AssignmentMethod.MANAGER)
                if payload.l2_engineer_id is not None
                else int(AssignmentMethod.AUTO)
            )

        card = self.repository.create_card(
            CreateCardData(
                omnidesk_ticket_number=payload.omnidesk_ticket_number,
                planned_start_at=payload.planned_start_at,
                planned_duration_minutes=payload.planned_duration_minutes,
                created_by_id=actor_user_id,
                status=status,
                client_id=payload.client_id,
                criticality_code=payload.criticality_code,
                urgency_code=payload.urgency_code,
                client_timezone_at_creation=payload.client_timezone_at_creation,
                timezone_source_code=payload.timezone_source_code,
                l1_owner_id=payload.l1_owner_id,
                l2_engineer_id=payload.l2_engineer_id,
                assignment_method_code=assignment_method_code,
                client_contact_type_code=payload.client_contact_type_code,
                client_contact_value=payload.client_contact_value,
                description=payload.description,
                urgent_reason=payload.urgent_reason,
                out_of_hours_flag=payload.out_of_hours_flag,
                retroactive_flag=payload.retroactive_flag,
            )
        )
        snapshot = _card_snapshot(card)
        self.repository.add_card_event(
            card_id=card.id,
            event_type=CardEventType.CREATED,
            actor_user_id=actor_user_id,
            old_values=None,
            new_values=snapshot,
            comment=None,
        )
        self.repository.add_audit_log(
            actor_user_id=actor_user_id,
            action=AuditAction.CREATE,
            entity_id=card.id,
            old_values=None,
            new_values=snapshot,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return card

    def get_card(self, public_id: UUID) -> CardRecord:
        card = self.repository.get_card_by_public_id(public_id)
        if card is None:
            raise CardNotFoundError
        return card

    def assign_card(
        self,
        public_id: UUID,
        *,
        l2_engineer_id: int,
        actor_user_id: int,
        comment: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        return self._change_status(
            public_id,
            target_status=CardStatus.ASSIGNED,
            actor_user_id=actor_user_id,
            comment=comment,
            ip_address=ip_address,
            user_agent=user_agent,
            l2_engineer_id=l2_engineer_id,
        )

    def confirm_card(
        self,
        public_id: UUID,
        *,
        actor_user_id: int,
        comment: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        return self._change_status(
            public_id,
            target_status=CardStatus.CONFIRMED,
            actor_user_id=actor_user_id,
            comment=comment,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def reject_card(
        self,
        public_id: UUID,
        *,
        actor_user_id: int,
        comment: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        return self._change_status(
            public_id,
            target_status=CardStatus.REJECTED,
            actor_user_id=actor_user_id,
            comment=comment,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def start_card(
        self,
        public_id: UUID,
        *,
        actor_user_id: int,
        comment: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        return self._change_status(
            public_id,
            target_status=CardStatus.IN_PROGRESS,
            actor_user_id=actor_user_id,
            comment=comment,
            ip_address=ip_address,
            user_agent=user_agent,
            actual_start_at=datetime.now(UTC),
        )

    def complete_card(
        self,
        public_id: UUID,
        *,
        result_code: int,
        engineer_report: str,
        actor_user_id: int,
        comment: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        if not engineer_report.strip():
            raise InvalidCardTransitionError("engineer_report_required")
        return self._change_status(
            public_id,
            target_status=CardStatus.COMPLETED,
            actor_user_id=actor_user_id,
            comment=comment,
            ip_address=ip_address,
            user_agent=user_agent,
            actual_end_at=datetime.now(UTC),
            result_code=result_code,
            engineer_report=engineer_report,
        )

    def cancel_card(
        self,
        public_id: UUID,
        *,
        actor_user_id: int,
        comment: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> CardRecord:
        return self._change_status(
            public_id,
            target_status=CardStatus.CANCELLED,
            actor_user_id=actor_user_id,
            comment=comment,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    def _change_status(
        self,
        public_id: UUID,
        *,
        target_status: CardStatus,
        actor_user_id: int,
        comment: str | None,
        ip_address: str | None,
        user_agent: str | None,
        l2_engineer_id: int | None = None,
        actual_start_at: datetime | None = None,
        actual_end_at: datetime | None = None,
        result_code: int | None = None,
        engineer_report: str | None = None,
    ) -> CardRecord:
        card = self.repository.get_card_by_public_id_for_update(public_id)
        if card is None:
            raise CardNotFoundError

        current_status = CardStatus(card.status_code)
        _validate_transition(
            current_status=current_status,
            target_status=target_status,
            current_l2_engineer_id=card.l2_engineer_id,
            new_l2_engineer_id=l2_engineer_id,
        )

        old_snapshot = _card_snapshot(card)
        updated = self.repository.update_card_status(
            public_id,
            StatusUpdateData(
                status=target_status,
                actor_user_id=actor_user_id,
                l2_engineer_id=l2_engineer_id,
                update_l2_engineer_id=(
                    l2_engineer_id is not None or target_status == CardStatus.REJECTED
                ),
                actual_start_at=actual_start_at,
                actual_end_at=actual_end_at,
                result_code=result_code,
                engineer_report=engineer_report,
            ),
        )
        if updated is None:
            raise CardNotFoundError

        new_snapshot = _card_snapshot(updated)
        self.repository.add_card_event(
            card_id=updated.id,
            event_type=CardEventType.STATUS_CHANGED,
            actor_user_id=actor_user_id,
            old_values=old_snapshot,
            new_values=new_snapshot,
            comment=comment,
        )
        self.repository.add_audit_log(
            actor_user_id=actor_user_id,
            action=AuditAction.UPDATE,
            entity_id=updated.id,
            old_values=old_snapshot,
            new_values=new_snapshot,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return updated


def _validate_transition(
    *,
    current_status: CardStatus,
    target_status: CardStatus,
    current_l2_engineer_id: int | None,
    new_l2_engineer_id: int | None,
) -> None:
    if target_status not in ALLOWED_STATUS_TRANSITIONS[current_status]:
        raise InvalidCardTransitionError("status_transition_not_allowed")

    resulting_l2_engineer_id = new_l2_engineer_id or current_l2_engineer_id
    if target_status in {
        CardStatus.ASSIGNED,
        CardStatus.CONFIRMED,
        CardStatus.IN_PROGRESS,
    } and resulting_l2_engineer_id is None:
        raise InvalidCardTransitionError("l2_engineer_required")


def _card_snapshot(card: CardRecord) -> dict[str, Any]:
    return {
        "id": card.id,
        "public_id": str(card.public_id),
        "number": card.number,
        "omnidesk_ticket_number": card.omnidesk_ticket_number,
        "status_code": card.status_code,
        "planned_start_at": card.planned_start_at.isoformat(),
        "planned_duration_minutes": card.planned_duration_minutes,
        "l1_owner_id": card.l1_owner_id,
        "l2_engineer_id": card.l2_engineer_id,
        "result_code": card.result_code,
        "engineer_report": card.engineer_report,
    }

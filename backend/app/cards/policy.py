from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from typing import Protocol

from app.cards.constants import CardStatus, RoleId
from app.cards.create_policy import CreateScenario


class CardAction(StrEnum):
    CREATE = "create"
    ASSIGN = "assign"
    CONFIRM = "confirm"
    REJECT = "reject"
    START = "start"
    COMPLETE = "complete"
    END_PENDING_RESULT = "end_pending_result"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    MARK_CLIENT_INFORMED = "mark_client_informed"


class PolicyCard(Protocol):
    status_code: int
    l1_owner_id: int | None
    l2_engineer_id: int | None


class CardActionPolicyError(Exception):
    def __init__(self, *, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def role_ids(roles: Collection[object]) -> frozenset[int]:
    return frozenset(int(getattr(role, "id")) for role in roles)


def authorize_card_read(*, actor_role_ids: Collection[int]) -> None:
    if not frozenset(actor_role_ids).intersection(
        {int(RoleId.L1), int(RoleId.L2), int(RoleId.MANAGER)}
    ):
        _forbidden()


def authorize_create(
    *,
    actor_role_ids: Collection[int],
    scenario: CreateScenario | None = None,
) -> None:
    roles = frozenset(actor_role_ids)
    if scenario is None:
        if int(RoleId.MANAGER) in roles:
            return
        _forbidden()
    if scenario == CreateScenario.L1 and int(RoleId.L1) in roles:
        return
    if (
        scenario
        in {
            CreateScenario.L2_SELF,
            CreateScenario.L2_URGENT,
            CreateScenario.L2_RETROACTIVE,
        }
        and int(RoleId.L2) in roles
    ):
        return
    if scenario is not None:
        _forbidden()


def authorize_card_action(
    *,
    action: CardAction,
    card: PolicyCard,
    actor_user_id: int,
    actor_role_ids: Collection[int],
    comment: str | None = None,
    target_l2_engineer_id: int | None = None,
) -> None:
    roles = frozenset(actor_role_ids)
    status = CardStatus(card.status_code)

    if action == CardAction.ASSIGN:
        if int(RoleId.MANAGER) not in roles:
            if int(RoleId.L2) not in roles:
                _forbidden()
            if target_l2_engineer_id != actor_user_id:
                _forbidden("only_self_assignment_allowed")
            if not (comment or "").strip():
                raise CardActionPolicyError(
                    status_code=422, detail="assignment_reason_required"
                )
        _require_status(
            status,
            CardStatus.CREATED,
            CardStatus.ASSIGNED,
            CardStatus.CONFIRMED,
            CardStatus.REJECTED,
        )
        return

    if action in {CardAction.CONFIRM, CardAction.REJECT}:
        _require_manager_or_assigned_l2(
            roles=roles, card=card, actor_user_id=actor_user_id
        )
        _require_status(status, CardStatus.ASSIGNED)
        return

    if action == CardAction.START:
        _require_manager_or_assigned_l2(
            roles=roles, card=card, actor_user_id=actor_user_id
        )
        _require_status(status, CardStatus.ASSIGNED, CardStatus.CONFIRMED)
        return

    if action == CardAction.COMPLETE:
        if status == CardStatus.COMPLETED_PENDING_RESULT:
            if int(RoleId.L2) not in roles or card.l2_engineer_id != actor_user_id:
                _forbidden("only_assigned_l2_may_submit_missing_result")
        else:
            _require_manager_or_assigned_l2(
                roles=roles, card=card, actor_user_id=actor_user_id
            )
        _require_status(
            status, CardStatus.IN_PROGRESS, CardStatus.COMPLETED_PENDING_RESULT
        )
        return

    if action == CardAction.END_PENDING_RESULT:
        _require_manager_or_assigned_l2(
            roles=roles, card=card, actor_user_id=actor_user_id
        )
        _require_status(status, CardStatus.IN_PROGRESS)
        return

    if action == CardAction.CANCEL:
        authorize_card_read(actor_role_ids=roles)
        if int(RoleId.MANAGER) in roles:
            _require_status(
                status,
                CardStatus.ASSIGNED,
                CardStatus.CONFIRMED,
                CardStatus.REJECTED,
                CardStatus.IN_PROGRESS,
            )
        else:
            _require_status(
                status, CardStatus.ASSIGNED, CardStatus.CONFIRMED, CardStatus.REJECTED
            )
        if not (comment or "").strip():
            raise CardActionPolicyError(
                status_code=422, detail="cancellation_reason_required"
            )
        return

    if action == CardAction.RESCHEDULE:
        authorize_card_read(actor_role_ids=roles)
        _require_status(
            status, CardStatus.ASSIGNED, CardStatus.CONFIRMED, CardStatus.REJECTED
        )
        if not (comment or "").strip():
            raise CardActionPolicyError(
                status_code=422, detail="reschedule_reason_required"
            )
        return

    if action == CardAction.MARK_CLIENT_INFORMED:
        _require_assigned_l1(roles=roles, card=card, actor_user_id=actor_user_id)
        _require_status(status, CardStatus.REJECTED)
        return

    raise ValueError(f"unsupported_card_action:{action}")


def _require_manager_or_assigned_l2(
    *, roles: Collection[int], card: PolicyCard, actor_user_id: int
) -> None:
    if int(RoleId.MANAGER) in roles:
        return
    if int(RoleId.L2) not in roles:
        _forbidden()
    if card.l2_engineer_id != actor_user_id:
        _forbidden("assigned_l2_required")


def _require_assigned_l1(
    *, roles: Collection[int], card: PolicyCard, actor_user_id: int
) -> None:
    if int(RoleId.L1) not in roles:
        _forbidden()
    if card.l1_owner_id != actor_user_id:
        _forbidden("assigned_l1_required")


def _require_status(status: CardStatus, *allowed: CardStatus) -> None:
    if status not in allowed:
        raise CardActionPolicyError(
            status_code=409, detail="action_not_allowed_for_status"
        )


def _forbidden(detail: str = "action_forbidden") -> None:
    raise CardActionPolicyError(status_code=403, detail=detail)

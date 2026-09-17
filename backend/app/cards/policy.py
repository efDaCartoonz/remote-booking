from __future__ import annotations

from collections.abc import Collection
from enum import StrEnum
from typing import Protocol

from app.cards.constants import CardStatus, RoleId


class CardAction(StrEnum):
    CREATE = "create"
    ASSIGN = "assign"
    CONFIRM = "confirm"
    REJECT = "reject"
    START = "start"
    COMPLETE = "complete"
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


def authorize_create(*, actor_role_ids: Collection[int]) -> None:
    if int(RoleId.MANAGER) not in actor_role_ids:
        _forbidden()


def authorize_card_action(
    *,
    action: CardAction,
    card: PolicyCard,
    actor_user_id: int,
    actor_role_ids: Collection[int],
    comment: str | None = None,
) -> None:
    roles = frozenset(actor_role_ids)
    status = CardStatus(card.status_code)

    if action == CardAction.ASSIGN:
        _require_manager(roles)
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
        _require_manager_or_assigned_l2(
            roles=roles, card=card, actor_user_id=actor_user_id
        )
        _require_status(status, CardStatus.IN_PROGRESS)
        return

    if action == CardAction.CANCEL:
        _require_manager_or_owner(roles=roles, card=card, actor_user_id=actor_user_id)
        _require_status(
            status, CardStatus.ASSIGNED, CardStatus.CONFIRMED, CardStatus.REJECTED
        )
        if int(RoleId.MANAGER) not in roles and not (comment or "").strip():
            raise CardActionPolicyError(
                status_code=422, detail="cancellation_reason_required"
            )
        return

    if action == CardAction.RESCHEDULE:
        _require_manager_or_assigned_l1(
            roles=roles, card=card, actor_user_id=actor_user_id
        )
        _require_status(status, CardStatus.REJECTED)
        return

    if action == CardAction.MARK_CLIENT_INFORMED:
        _require_assigned_l1(roles=roles, card=card, actor_user_id=actor_user_id)
        _require_status(status, CardStatus.REJECTED)
        return

    raise ValueError(f"unsupported_card_action:{action}")


def _require_manager(roles: Collection[int]) -> None:
    if int(RoleId.MANAGER) not in roles:
        _forbidden()


def _require_manager_or_assigned_l2(
    *, roles: Collection[int], card: PolicyCard, actor_user_id: int
) -> None:
    if int(RoleId.MANAGER) in roles:
        return
    if int(RoleId.L2) not in roles:
        _forbidden()
    if card.l2_engineer_id != actor_user_id:
        _forbidden("assigned_l2_required")


def _require_manager_or_assigned_l1(
    *, roles: Collection[int], card: PolicyCard, actor_user_id: int
) -> None:
    if int(RoleId.MANAGER) in roles:
        return
    _require_assigned_l1(roles=roles, card=card, actor_user_id=actor_user_id)


def _require_manager_or_owner(
    *, roles: Collection[int], card: PolicyCard, actor_user_id: int
) -> None:
    if int(RoleId.MANAGER) in roles:
        return
    owns_l1 = int(RoleId.L1) in roles and card.l1_owner_id == actor_user_id
    owns_l2 = int(RoleId.L2) in roles and card.l2_engineer_id == actor_user_id
    if not owns_l1 and not owns_l2:
        _forbidden("card_owner_required")


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

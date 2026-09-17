from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.cards.constants import CardStatus, RoleId
from app.cards.policy import (
    CardAction,
    CardActionPolicyError,
    authorize_card_action,
    authorize_create,
)


@dataclass(frozen=True)
class PolicyCard:
    status_code: int
    l1_owner_id: int | None = 11
    l2_engineer_id: int | None = 22


MANAGER = frozenset({int(RoleId.MANAGER)})
L1 = frozenset({int(RoleId.L1)})
L2 = frozenset({int(RoleId.L2)})
ADMIN = frozenset({int(RoleId.ADMIN)})


@pytest.mark.parametrize(
    ("action", "roles", "actor_user_id", "status"),
    (
        (CardAction.ASSIGN, MANAGER, 10, CardStatus.CREATED),
        (CardAction.CONFIRM, MANAGER, 10, CardStatus.ASSIGNED),
        (CardAction.REJECT, MANAGER, 10, CardStatus.ASSIGNED),
        (CardAction.START, MANAGER, 10, CardStatus.CONFIRMED),
        (CardAction.COMPLETE, MANAGER, 10, CardStatus.IN_PROGRESS),
        (CardAction.CANCEL, MANAGER, 10, CardStatus.REJECTED),
        (CardAction.RESCHEDULE, MANAGER, 10, CardStatus.REJECTED),
        (CardAction.CONFIRM, L2, 22, CardStatus.ASSIGNED),
        (CardAction.REJECT, L2, 22, CardStatus.ASSIGNED),
        (CardAction.START, L2, 22, CardStatus.CONFIRMED),
        (CardAction.COMPLETE, L2, 22, CardStatus.IN_PROGRESS),
        (CardAction.CANCEL, L2, 22, CardStatus.ASSIGNED),
        (CardAction.CANCEL, L1, 11, CardStatus.REJECTED),
        (CardAction.RESCHEDULE, L1, 11, CardStatus.REJECTED),
        (CardAction.MARK_CLIENT_INFORMED, L1, 11, CardStatus.REJECTED),
    ),
)
def test_action_policy_allows_only_role_owner_and_state_combinations(
    action: CardAction,
    roles: frozenset[int],
    actor_user_id: int,
    status: CardStatus,
) -> None:
    authorize_card_action(
        action=action,
        card=PolicyCard(status_code=int(status)),
        actor_user_id=actor_user_id,
        actor_role_ids=roles,
        comment="client_requested" if action == CardAction.CANCEL else None,
    )


@pytest.mark.parametrize(
    ("action", "roles", "actor_user_id", "status", "detail"),
    (
        (CardAction.ASSIGN, L1, 11, CardStatus.CREATED, "action_forbidden"),
        (CardAction.CONFIRM, L2, 33, CardStatus.ASSIGNED, "assigned_l2_required"),
        (CardAction.REJECT, L1, 11, CardStatus.ASSIGNED, "action_forbidden"),
        (
            CardAction.START,
            L2,
            22,
            CardStatus.REJECTED,
            "action_not_allowed_for_status",
        ),
        (
            CardAction.COMPLETE,
            L2,
            22,
            CardStatus.CONFIRMED,
            "action_not_allowed_for_status",
        ),
        (CardAction.CANCEL, L1, 33, CardStatus.REJECTED, "card_owner_required"),
        (
            CardAction.RESCHEDULE,
            L1,
            11,
            CardStatus.CONFIRMED,
            "action_not_allowed_for_status",
        ),
        (CardAction.CANCEL, ADMIN, 10, CardStatus.REJECTED, "card_owner_required"),
    ),
)
def test_action_policy_rejects_role_owner_and_state_violations(
    action: CardAction,
    roles: frozenset[int],
    actor_user_id: int,
    status: CardStatus,
    detail: str,
) -> None:
    with pytest.raises(CardActionPolicyError) as error:
        authorize_card_action(
            action=action,
            card=PolicyCard(status_code=int(status)),
            actor_user_id=actor_user_id,
            actor_role_ids=roles,
            comment="client_requested",
        )

    assert error.value.detail == detail
    assert error.value.status_code == (409 if detail.endswith("status") else 403)


def test_create_requires_manager_business_role_not_admin_access_level() -> None:
    authorize_create(actor_role_ids=MANAGER)

    with pytest.raises(CardActionPolicyError) as error:
        authorize_create(actor_role_ids=ADMIN)

    assert error.value.status_code == 403
    assert error.value.detail == "action_forbidden"


def test_owner_cancellation_requires_recorded_basis() -> None:
    with pytest.raises(CardActionPolicyError) as error:
        authorize_card_action(
            action=CardAction.CANCEL,
            card=PolicyCard(status_code=int(CardStatus.REJECTED)),
            actor_user_id=11,
            actor_role_ids=L1,
        )

    assert error.value.status_code == 422
    assert error.value.detail == "cancellation_reason_required"

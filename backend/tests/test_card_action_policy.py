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
        (CardAction.CANCEL, MANAGER, 10, CardStatus.IN_PROGRESS),
        (CardAction.RESCHEDULE, MANAGER, 10, CardStatus.REJECTED),
        (CardAction.CONFIRM, L2, 22, CardStatus.ASSIGNED),
        (CardAction.REJECT, L2, 22, CardStatus.ASSIGNED),
        (CardAction.START, L2, 22, CardStatus.CONFIRMED),
        (CardAction.COMPLETE, L2, 22, CardStatus.IN_PROGRESS),
        (CardAction.CANCEL, L2, 22, CardStatus.ASSIGNED),
        (CardAction.CANCEL, L1, 11, CardStatus.REJECTED),
        (CardAction.RESCHEDULE, L1, 11, CardStatus.CONFIRMED),
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
        comment="client_requested"
        if action in {CardAction.CANCEL, CardAction.RESCHEDULE}
        else None,
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
        (CardAction.CANCEL, L2, 33, CardStatus.REJECTED, "assigned_l2_required"),
        (
            CardAction.CANCEL,
            L2,
            22,
            CardStatus.IN_PROGRESS,
            "action_not_allowed_for_status",
        ),
        (
            CardAction.CANCEL,
            L1,
            11,
            CardStatus.IN_PROGRESS,
            "action_not_allowed_for_status",
        ),
        (
            CardAction.RESCHEDULE,
            L1,
            11,
            CardStatus.CREATED,
            "action_not_allowed_for_status",
        ),
        (CardAction.CANCEL, ADMIN, 10, CardStatus.REJECTED, "assigned_l2_required"),
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


@pytest.mark.parametrize("status", tuple(CardStatus))
def test_reschedule_manager_matrix_is_limited_to_srs_time_change_states(
    status: CardStatus,
) -> None:
    card = PolicyCard(status_code=int(status))

    if status in {
        CardStatus.ASSIGNED,
        CardStatus.CONFIRMED,
        CardStatus.REJECTED,
    }:
        authorize_card_action(
            action=CardAction.RESCHEDULE,
            card=card,
            actor_user_id=10,
            actor_role_ids=MANAGER,
        )
        return

    with pytest.raises(CardActionPolicyError) as error:
        authorize_card_action(
            action=CardAction.RESCHEDULE,
            card=card,
            actor_user_id=10,
            actor_role_ids=MANAGER,
        )

    assert error.value.status_code == 409
    assert error.value.detail == "action_not_allowed_for_status"


@pytest.mark.parametrize("status", (CardStatus.ASSIGNED, CardStatus.CONFIRMED))
def test_reschedule_allows_assigned_l2_with_reason(status: CardStatus) -> None:
    authorize_card_action(
        action=CardAction.RESCHEDULE,
        card=PolicyCard(status_code=int(status)),
        actor_user_id=22,
        actor_role_ids=L2,
        comment="client_requested",
    )


def test_reschedule_allows_owning_l1_with_reason() -> None:
    authorize_card_action(
        action=CardAction.RESCHEDULE,
        card=PolicyCard(status_code=int(CardStatus.REJECTED)),
        actor_user_id=11,
        actor_role_ids=L1,
        comment="client_requested",
    )


@pytest.mark.parametrize(
    ("roles", "actor_user_id", "detail"),
    (
        (L1, 99, "assigned_l1_required"),
        (L2, 99, "assigned_l2_required"),
        (ADMIN, 10, "action_forbidden"),
    ),
)
def test_reschedule_rejects_non_owner_or_unassigned_actor(
    roles: frozenset[int], actor_user_id: int, detail: str
) -> None:
    with pytest.raises(CardActionPolicyError) as error:
        authorize_card_action(
            action=CardAction.RESCHEDULE,
            card=PolicyCard(status_code=int(CardStatus.CONFIRMED)),
            actor_user_id=actor_user_id,
            actor_role_ids=roles,
            comment="client_requested",
        )

    assert error.value.status_code == 403
    assert error.value.detail == detail


@pytest.mark.parametrize("roles", (L1, L2))
@pytest.mark.parametrize("comment", (None, "   "))
def test_reschedule_requires_reason_for_non_manager_actor(
    roles: frozenset[int], comment: str | None
) -> None:
    actor_user_id = 11 if roles == L1 else 22
    with pytest.raises(CardActionPolicyError) as error:
        authorize_card_action(
            action=CardAction.RESCHEDULE,
            card=PolicyCard(status_code=int(CardStatus.CONFIRMED)),
            actor_user_id=actor_user_id,
            actor_role_ids=roles,
            comment=comment,
        )

    assert error.value.status_code == 422
    assert error.value.detail == "reschedule_reason_required"

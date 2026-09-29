from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.cards.constants import AuditAction, CardEventType, CardStatus
from app.cards.policy import CardActionPolicyError
from app.cards.repository import CardRecord, L1FollowupUpdateData
from app.cards.service import CardService


def make_card(
    *,
    id: int = 10,
    public_id: UUID | None = None,
    status_code: int = int(CardStatus.REJECTED),
    l1_owner_id: int | None = 11,
    client_informed: bool = True,
) -> CardRecord:
    now = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)
    return CardRecord(
        id=id,
        public_id=public_id or uuid4(),
        number="CARD-001",
        omnidesk_ticket_number="100-000001",
        client_id=1,
        status_code=status_code,
        criticality_code=0,
        urgency_code=0,
        planned_start_at=now,
        planned_duration_minutes=60,
        client_timezone_at_creation="UTC",
        timezone_source_code=0,
        actual_start_at=None,
        actual_end_at=None,
        l1_owner_id=l1_owner_id,
        l2_engineer_id=22,
        assignment_method_code=0,
        unsuccessful_cycle_count=0,
        client_contact_type_code=None,
        client_contact_value=None,
        description=None,
        urgent_reason=None,
        out_of_hours_flag=False,
        retroactive_flag=False,
        overdue_flag=False,
        result_code=None,
        engineer_report=None,
        created_source_code=0,
        created_by_id=11,
        created_at=now,
        updated_at=now,
        client_informed=client_informed,
    )


class FakeCardRepository:
    def __init__(self, card: CardRecord | None = None) -> None:
        self.card = card
        self.active_interval: int | None = None
        self.update_interval_calls: list[dict[str, Any]] = []
        self.create_schedule_calls: list[dict[str, Any]] = []
        self.close_schedule_calls: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []

    def get_card_by_public_id(self, public_id: UUID) -> CardRecord | None:
        if self.card and self.card.public_id == public_id:
            return self.card
        return None

    def get_card_by_public_id_for_update(self, public_id: UUID) -> CardRecord | None:
        return self.get_card_by_public_id(public_id)

    def get_l1_reminder_interval(self, card_id: int) -> int | None:
        return self.active_interval

    def update_l1_reminder_interval(
        self,
        *,
        card_id: int,
        owner_id: int,
        interval_minutes: int,
        now: datetime,
    ) -> None:
        self.update_interval_calls.append(
            {
                "card_id": card_id,
                "owner_id": owner_id,
                "interval_minutes": interval_minutes,
                "now": now,
            }
        )
        self.active_interval = interval_minutes

    def add_card_event(self, **kwargs) -> int:
        self.events.append(kwargs)
        return len(self.events)

    def add_audit_log(self, **kwargs) -> None:
        self.audits.append(kwargs)

    def close_reminder_schedules(
        self, *, card_id: int, kind: str | None = None
    ) -> None:
        self.close_schedule_calls.append({"card_id": card_id, "kind": kind})

    def create_reminder_schedule(self, **kwargs) -> None:
        self.create_schedule_calls.append(kwargs)

    def update_l1_followup(
        self, public_id: UUID, data: L1FollowupUpdateData
    ) -> CardRecord | None:
        if self.card and self.card.public_id == public_id:
            self.card = make_card(
                id=self.card.id,
                public_id=self.card.public_id,
                status_code=self.card.status_code,
                l1_owner_id=self.card.l1_owner_id,
                client_informed=(
                    data.client_informed
                    if data.client_informed is not None
                    else self.card.client_informed
                ),
            )
            return self.card
        return None


def test_missing_schedule_recreation_on_post_10() -> None:
    fixed_now = datetime(2026, 9, 29, 14, 0, 0, tzinfo=UTC)
    card = make_card(
        status_code=int(CardStatus.REJECTED), l1_owner_id=11, client_informed=True
    )
    repo = FakeCardRepository(card)
    repo.active_interval = None  # Confirmed bug scenario: no active schedule

    service = CardService(repo, clock=lambda: fixed_now)

    # GET must return contract default 10 without calling update_l1_reminder_interval
    get_result = service.l1_reminder_interval(
        card.public_id, actor_user_id=11, actor_role_ids={1}
    )
    assert get_result == 10
    assert len(repo.update_interval_calls) == 0

    # POST 10 must create active schedule instead of no-op
    post_result = service.l1_reminder_interval(
        card.public_id,
        actor_user_id=11,
        actor_role_ids={1},
        interval_minutes=10,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )
    assert post_result == 10
    assert len(repo.update_interval_calls) == 1
    assert repo.update_interval_calls[0] == {
        "card_id": card.id,
        "owner_id": 11,
        "interval_minutes": 10,
        "now": fixed_now,
    }
    assert repo.events[-1]["event_type"] == CardEventType.DETAILS_UPDATED
    assert repo.events[-1]["old_values"] == {"l1_reminder_interval_minutes": None}
    assert repo.events[-1]["new_values"] == {"l1_reminder_interval_minutes": 10}
    assert repo.audits[-1]["action"] == AuditAction.UPDATE
    assert repo.audits[-1]["old_values"] == {"l1_reminder_interval_minutes": None}
    assert repo.audits[-1]["new_values"] == {"l1_reminder_interval_minutes": 10}

    # Subsequent POST 10 when schedule is already active and 10 no-ops
    post_again = service.l1_reminder_interval(
        card.public_id,
        actor_user_id=11,
        actor_role_ids={1},
        interval_minutes=10,
    )
    assert post_again == 10
    assert len(repo.update_interval_calls) == 1  # No additional call


def test_update_schedule_to_30() -> None:
    fixed_now = datetime(2026, 9, 29, 14, 0, 0, tzinfo=UTC)
    card = make_card(
        status_code=int(CardStatus.REJECTED), l1_owner_id=11, client_informed=True
    )
    repo = FakeCardRepository(card)
    repo.active_interval = 10

    service = CardService(repo, clock=lambda: fixed_now)
    post_result = service.l1_reminder_interval(
        card.public_id,
        actor_user_id=11,
        actor_role_ids={1},
        interval_minutes=30,
    )
    assert post_result == 30
    assert len(repo.update_interval_calls) == 1
    assert repo.update_interval_calls[0]["interval_minutes"] == 30
    assert repo.events[-1]["old_values"] == {"l1_reminder_interval_minutes": 10}
    assert repo.events[-1]["new_values"] == {"l1_reminder_interval_minutes": 30}


def test_forbidden_owner_and_state() -> None:
    card = make_card(
        status_code=int(CardStatus.REJECTED), l1_owner_id=11, client_informed=True
    )
    repo = FakeCardRepository(card)
    service = CardService(repo)

    # 1. Non-L1 role
    with pytest.raises(CardActionPolicyError) as exc:
        service.l1_reminder_interval(
            card.public_id, actor_user_id=11, actor_role_ids={2}, interval_minutes=10
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "assigned_l1_required"

    # 2. Non-owner L1
    with pytest.raises(CardActionPolicyError) as exc:
        service.l1_reminder_interval(
            card.public_id, actor_user_id=99, actor_role_ids={1}, interval_minutes=10
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "assigned_l1_required"

    # 3. Card not in REJECTED state (e.g. ASSIGNED)
    assigned_card = make_card(
        status_code=int(CardStatus.ASSIGNED), l1_owner_id=11, client_informed=True
    )
    repo.card = assigned_card
    with pytest.raises(CardActionPolicyError) as exc:
        service.l1_reminder_interval(
            assigned_card.public_id,
            actor_user_id=11,
            actor_role_ids={1},
            interval_minutes=10,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "l1_followup_not_informed"

    # 4. Card in REJECTED state but not informed
    uninformed_card = make_card(
        status_code=int(CardStatus.REJECTED), l1_owner_id=11, client_informed=False
    )
    repo.card = uninformed_card
    with pytest.raises(CardActionPolicyError) as exc:
        service.l1_reminder_interval(
            uninformed_card.public_id,
            actor_user_id=11,
            actor_role_ids={1},
            interval_minutes=10,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "l1_followup_not_informed"


def test_mark_client_informed_injected_clock() -> None:
    fixed_now = datetime(2026, 9, 29, 15, 30, 0, tzinfo=UTC)
    card = make_card(
        status_code=int(CardStatus.REJECTED), l1_owner_id=11, client_informed=False
    )
    repo = FakeCardRepository(card)
    service = CardService(repo, clock=lambda: fixed_now)

    updated = service.mark_client_informed(
        card.public_id,
        actor_user_id=11,
        actor_role_ids={1},
        ip_address=None,
        user_agent=None,
    )
    assert updated.client_informed is True
    assert len(repo.create_schedule_calls) == 1
    assert repo.create_schedule_calls[0]["anchor_at"] == fixed_now
    assert repo.create_schedule_calls[0]["kind"] == "l1_reminder"
    assert repo.create_schedule_calls[0]["owner_id"] == 11
    assert repo.create_schedule_calls[0]["informed"] is True
    assert repo.create_schedule_calls[0]["interval_minutes"] == 10

from datetime import timedelta

import pytest

from app.cards.constants import ActorType, CardStatus
from app.cards.service import CardService, InvalidCardTransitionError
from app.integrations.omnidesk_reschedule import ConfirmedOmnideskReschedule
from test_cards import (
    FakeCardRepository,
    create_payload,
    seed_l1_candidate,
    seed_l2_candidate,
)


def test_confirmed_omnidesk_reschedule_closes_l1_and_is_idempotent():
    repository = FakeCardRepository()
    seed_l1_candidate(repository, 10)
    service = CardService(repository)
    rejected = service.create_card(
        create_payload(), actor_user_id=1, ip_address=None, user_agent=None
    )
    command = ConfirmedOmnideskReschedule(
        card_id=rejected.id,
        source_event_id="omnidesk-event-1",
        planned_start_at=rejected.planned_start_at + timedelta(hours=1),
        planned_duration_minutes=90,
    )

    updated = service.apply_confirmed_omnidesk_reschedule(command)
    repeated = service.apply_confirmed_omnidesk_reschedule(command)

    assert updated.status_code == int(CardStatus.REJECTED)
    assert updated.l1_owner_id == 10
    assert repeated == updated
    reschedule_events = [
        event
        for event in repository.events
        if event["comment"] == "omnidesk_rescheduled:omnidesk-event-1"
    ]
    assert len(reschedule_events) == 1
    assert reschedule_events[0]["actor_type"] == ActorType.OMNIDESK
    assert any(audit["actor_type"] == ActorType.OMNIDESK for audit in repository.audit)
    assert sum(schedule["closed_at"] is None for schedule in repository.schedules) == 1


def test_stale_omnidesk_reschedule_has_no_side_effects():
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    card = CardService(repository).create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=1,
        ip_address=None,
        user_agent=None,
    )
    before_events = list(repository.events)

    with pytest.raises(
        InvalidCardTransitionError, match="stale_omnidesk_reschedule_event"
    ):
        CardService(repository).apply_confirmed_omnidesk_reschedule(
            ConfirmedOmnideskReschedule(
                card_id=card.id,
                source_event_id="stale-event",
                planned_start_at=card.planned_start_at + timedelta(hours=1),
                planned_duration_minutes=60,
            )
        )

    assert repository.events == before_events

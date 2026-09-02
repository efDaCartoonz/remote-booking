from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.cards import get_card_repository
from app.auth.dependencies import get_auth_store, get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import AuditAction, CardEventType, CardStatus
from app.cards.repository import (
    CardRecord,
    CreateCardData,
    StatusUpdateData,
)
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService, InvalidCardTransitionError
from app.main import create_app


class FakeCardRepository:
    def __init__(self) -> None:
        self.cards: dict[UUID, CardRecord] = {}
        self.events: list[dict[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.next_id = 1

    def create_card(self, data: CreateCardData) -> CardRecord:
        now = datetime.now(UTC)
        card = CardRecord(
            id=self.next_id,
            public_id=uuid4(),
            number=f"RDM-{self.next_id:06d}",
            omnidesk_ticket_number=data.omnidesk_ticket_number,
            client_id=data.client_id,
            status_code=int(data.status),
            criticality_code=data.criticality_code,
            urgency_code=data.urgency_code,
            planned_start_at=data.planned_start_at,
            planned_duration_minutes=data.planned_duration_minutes,
            client_timezone_at_creation=data.client_timezone_at_creation,
            timezone_source_code=data.timezone_source_code,
            actual_start_at=None,
            actual_end_at=None,
            l1_owner_id=data.l1_owner_id,
            l2_engineer_id=data.l2_engineer_id,
            assignment_method_code=data.assignment_method_code,
            unsuccessful_cycle_count=0,
            client_contact_type_code=data.client_contact_type_code,
            client_contact_value=data.client_contact_value,
            description=data.description,
            urgent_reason=data.urgent_reason,
            out_of_hours_flag=data.out_of_hours_flag,
            retroactive_flag=data.retroactive_flag,
            overdue_flag=False,
            result_code=None,
            engineer_report=None,
            created_source_code=0,
            created_by_id=data.created_by_id,
            created_at=now,
            updated_at=now,
        )
        self.next_id += 1
        self.cards[card.public_id] = card
        return card

    def get_card_by_public_id(self, public_id: UUID) -> CardRecord | None:
        return self.cards.get(public_id)

    def get_card_by_public_id_for_update(self, public_id: UUID) -> CardRecord | None:
        return self.cards.get(public_id)

    def update_card_status(
        self,
        public_id: UUID,
        data: StatusUpdateData,
    ) -> CardRecord | None:
        card = self.cards.get(public_id)
        if card is None:
            return None
        updated = replace(
            card,
            status_code=int(data.status),
            l2_engineer_id=(
                data.l2_engineer_id if data.update_l2_engineer_id else card.l2_engineer_id
            ),
            actual_start_at=data.actual_start_at or card.actual_start_at,
            actual_end_at=data.actual_end_at or card.actual_end_at,
            result_code=data.result_code if data.result_code is not None else card.result_code,
            engineer_report=data.engineer_report or card.engineer_report,
            updated_at=datetime.now(UTC),
        )
        self.cards[public_id] = updated
        return updated

    def add_card_event(
        self,
        *,
        card_id: int,
        event_type: CardEventType,
        actor_user_id: int,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        comment: str | None,
    ) -> None:
        self.events.append(
            {
                "card_id": card_id,
                "event_type": event_type,
                "actor_user_id": actor_user_id,
                "old_values": old_values,
                "new_values": new_values,
                "comment": comment,
            }
        )

    def add_audit_log(
        self,
        *,
        actor_user_id: int,
        action: AuditAction,
        entity_id: int,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self.audit.append(
            {
                "actor_user_id": actor_user_id,
                "action": action,
                "entity_id": entity_id,
                "old_values": old_values,
                "new_values": new_values,
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
        )


def create_payload(*, l2_engineer_id: int | None = None) -> CardCreateRequest:
    return CardCreateRequest(
        omnidesk_ticket_number="123-456789",
        planned_start_at=datetime.now(UTC) + timedelta(hours=3),
        planned_duration_minutes=60,
        l2_engineer_id=l2_engineer_id,
        description="Проверить удаленный доступ",
    )


def make_service(repository: FakeCardRepository) -> CardService:
    return CardService(repository)


def test_create_card_without_l2_starts_created_and_writes_history_and_audit() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)

    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert card.status_code == int(CardStatus.CREATED)
    assert card.l2_engineer_id is None
    assert [event["event_type"] for event in repository.events] == [CardEventType.CREATED]
    assert [event["action"] for event in repository.audit] == [AuditAction.CREATE]
    assert repository.events[0]["new_values"]["status_code"] == int(CardStatus.CREATED)


def test_create_card_with_l2_starts_assigned() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)

    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    assert card.status_code == int(CardStatus.ASSIGNED)
    assert card.l2_engineer_id == 20


def test_allowed_lifecycle_path_writes_status_history_and_audit() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    confirmed = service.confirm_card(
        card.public_id,
        actor_user_id=20,
        comment="Подтверждаю",
        ip_address=None,
        user_agent=None,
    )
    started = service.start_card(
        card.public_id,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )
    completed = service.complete_card(
        card.public_id,
        result_code=0,
        engineer_report="Подключение выполнено успешно",
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    assert [confirmed.status_code, started.status_code, completed.status_code] == [
        int(CardStatus.CONFIRMED),
        int(CardStatus.IN_PROGRESS),
        int(CardStatus.COMPLETED),
    ]
    assert [event["event_type"] for event in repository.events] == [
        CardEventType.CREATED,
        CardEventType.STATUS_CHANGED,
        CardEventType.STATUS_CHANGED,
        CardEventType.STATUS_CHANGED,
    ]
    assert [event["action"] for event in repository.audit] == [
        AuditAction.CREATE,
        AuditAction.UPDATE,
        AuditAction.UPDATE,
        AuditAction.UPDATE,
    ]


def test_created_card_cannot_be_cancelled_by_user_action() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(InvalidCardTransitionError, match="status_transition_not_allowed"):
        service.cancel_card(
            card.public_id,
            actor_user_id=10,
            comment=None,
            ip_address=None,
            user_agent=None,
        )

    assert len(repository.events) == 1
    assert len(repository.audit) == 1


def test_card_cannot_be_completed_without_in_progress_status() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(InvalidCardTransitionError, match="status_transition_not_allowed"):
        service.complete_card(
            card.public_id,
            result_code=0,
            engineer_report="Готово",
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )


def test_reject_assigned_card_clears_current_l2() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    rejected = service.reject_card(
        card.public_id,
        actor_user_id=10,
        comment="Все инженеры отказались",
        ip_address=None,
        user_agent=None,
    )

    assert rejected.status_code == int(CardStatus.REJECTED)
    assert rejected.l2_engineer_id is None
    assert repository.events[-1]["old_values"]["l2_engineer_id"] == 20
    assert repository.events[-1]["new_values"]["l2_engineer_id"] is None


def test_complete_requires_engineer_report() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    service.start_card(
        card.public_id,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(InvalidCardTransitionError, match="engineer_report_required"):
        service.complete_card(
            card.public_id,
            result_code=0,
            engineer_report="   ",
            actor_user_id=20,
            comment=None,
            ip_address=None,
            user_agent=None,
        )


def test_terminal_statuses_are_immutable_for_user_actions() -> None:
    repository = FakeCardRepository()
    service = make_service(repository)
    card = service.create_card(
        create_payload(l2_engineer_id=20),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )
    service.start_card(
        card.public_id,
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )
    completed = service.complete_card(
        card.public_id,
        result_code=0,
        engineer_report="Готово",
        actor_user_id=20,
        comment=None,
        ip_address=None,
        user_agent=None,
    )

    with pytest.raises(InvalidCardTransitionError, match="status_transition_not_allowed"):
        service.cancel_card(
            completed.public_id,
            actor_user_id=10,
            comment=None,
            ip_address=None,
            user_agent=None,
        )


def test_cards_api_requires_authorized_internal_user() -> None:
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: FakeCardRepository()
    app.dependency_overrides[get_auth_store] = lambda: object()
    client = TestClient(app, base_url="https://testserver")

    response = client.post(
        "/api/v1/cards",
        json={
            "omnidesk_ticket_number": "123-456789",
            "planned_start_at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "not_authenticated"


def test_cards_api_creates_and_reads_card() -> None:
    repository = FakeCardRepository()
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: repository
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        id=10,
        username="manager",
        password_hash="unused",
        full_name="Руководитель",
        email=None,
        roles=(RoleRecord(id=3, name="Руководитель"),),
    )
    client = TestClient(app, base_url="https://testserver")

    create_response = client.post(
        "/api/v1/cards",
        json={
            "omnidesk_ticket_number": "123-456789",
            "planned_start_at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
            "planned_duration_minutes": 60,
            "l2_engineer_id": 20,
        },
    )

    assert create_response.status_code == 201
    created_body = create_response.json()
    assert created_body["status"] == "assigned"
    assert created_body["status_label"] == "Назначено"
    assert created_body["l2_engineer_id"] == 20

    read_response = client.get(f"/api/v1/cards/{created_body['id']}")

    assert read_response.status_code == 200
    assert read_response.json()["id"] == created_body["id"]


def test_cards_api_returns_conflict_for_forbidden_transition() -> None:
    repository = FakeCardRepository()
    app = create_app()
    app.dependency_overrides[get_card_repository] = lambda: repository
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        id=10,
        username="l1",
        password_hash="unused",
        full_name="Специалист Л1",
        email=None,
        roles=(RoleRecord(id=1, name="Специалист Л1"),),
    )
    client = TestClient(app, base_url="https://testserver")
    card = CardService(repository).create_card(
        create_payload(),
        actor_user_id=10,
        ip_address=None,
        user_agent=None,
    )

    response = client.post(f"/api/v1/cards/{card.public_id}/cancel", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "status_transition_not_allowed"

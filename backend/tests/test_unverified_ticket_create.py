"""Card creation without Omnidesk verification (stand mode without integrations)."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.cards as cards_api
import app.api.manager as manager_api
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.repository import CardRecord
from app.frame.omnidesk import get_omnidesk_ticket_client
from app.main import create_app

NOW = datetime.now(UTC)
L1 = UserAuthRecord(11, "l1", "hash", "L1", None, (RoleRecord(1, "L1"),))
MANAGER = UserAuthRecord(14, "mgr", "hash", "Manager", None, (RoleRecord(3, "M"),))


def _payload(**overrides):
    payload = {
        "case_number": "416-108096",
        "planned_start_at": (NOW + timedelta(hours=3)).isoformat(),
        "planned_duration_minutes": 60,
    }
    payload.update(overrides)
    return payload


def _card(client_id):
    return CardRecord(
        id=1,
        public_id=uuid4(),
        number="RDM-000001",
        omnidesk_ticket_number="416-108096",
        client_id=client_id,
        status_code=0,
        criticality_code=0,
        urgency_code=0,
        planned_start_at=NOW + timedelta(hours=3),
        planned_duration_minutes=60,
        client_timezone_at_creation=None,
        timezone_source_code=None,
        actual_start_at=None,
        actual_end_at=None,
        l1_owner_id=11,
        l2_engineer_id=None,
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
        created_at=NOW,
        updated_at=NOW,
    )


class Connection:
    def rollback(self):
        pass


@contextmanager
def _connection():
    yield Connection()


def _forbid_omnidesk(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("Omnidesk must not be touched without verification")

    monkeypatch.setattr(cards_api, "resolve_ticket_by_case_number", fail)
    monkeypatch.setattr(manager_api, "resolve_ticket_by_case_number", fail)
    app = create_app()

    class Untouchable:
        def __getattr__(self, _name):
            fail()

    app.dependency_overrides[get_omnidesk_ticket_client] = lambda: Untouchable()
    return app


@pytest.fixture
def no_verification(monkeypatch):
    monkeypatch.setattr(
        cards_api.settings, "omnidesk_ticket_verification_enabled", False
    )
    monkeypatch.setattr(
        manager_api.settings, "omnidesk_ticket_verification_enabled", False
    )


def test_l1_create_accepts_the_typed_number_without_omnidesk(
    monkeypatch, no_verification
):
    captured = {}

    class Repository:
        def __init__(self, _connection):
            pass

        def has_active_card_for_ticket(self, number):
            captured["checked_number"] = number
            return False

        def get_or_create_client(self, _data):
            raise AssertionError("no client without verification")

    class Service:
        def __init__(self, *_args):
            pass

        def create_card(self, payload, **_kwargs):
            captured["payload"] = payload
            return _card(payload.client_id)

    monkeypatch.setattr(cards_api, "db_connection", _connection)
    monkeypatch.setattr(cards_api, "PostgresCardRepository", Repository)
    monkeypatch.setattr(cards_api, "CardService", Service)
    monkeypatch.setattr(cards_api, "PostgresNotificationService", lambda _: None)
    app = _forbid_omnidesk(monkeypatch)
    app.dependency_overrides[get_current_user] = lambda: L1
    response = TestClient(app).post("/api/v1/cards/l1", json=_payload())
    assert response.status_code == 201, response.text
    assert captured["checked_number"] == "416-108096"
    assert captured["payload"].omnidesk_ticket_number == "416-108096"
    assert captured["payload"].client_id is None
    assert "case_id" not in response.text


def test_active_card_for_the_ticket_still_blocks_without_verification(
    monkeypatch, no_verification
):
    class Repository:
        def __init__(self, _connection):
            pass

        def has_active_card_for_ticket(self, _number):
            return True

    monkeypatch.setattr(cards_api, "db_connection", _connection)
    monkeypatch.setattr(cards_api, "PostgresCardRepository", Repository)
    app = _forbid_omnidesk(monkeypatch)
    app.dependency_overrides[get_current_user] = lambda: L1
    response = TestClient(app).post("/api/v1/cards/l1", json=_payload())
    assert response.status_code == 409
    assert response.json()["detail"] == "active_card_exists_for_ticket"


def test_manager_preflight_and_create_work_without_omnidesk(
    monkeypatch, no_verification
):
    captured = {}

    class Repository:
        def __init__(self, _connection):
            pass

        def has_active_card_for_ticket(self, _number):
            return False

        def get_or_create_client(self, _data):
            raise AssertionError("no client without verification")

    class Service:
        def __init__(self, *_args):
            pass

        def create_card(self, payload, **_kwargs):
            captured["payload"] = payload
            return _card(payload.client_id)

    monkeypatch.setattr(manager_api, "db_connection", _connection)
    monkeypatch.setattr(manager_api, "PostgresCardRepository", Repository)
    monkeypatch.setattr(manager_api, "CardService", Service)
    monkeypatch.setattr(manager_api, "PostgresNotificationService", lambda _: None)
    app = _forbid_omnidesk(monkeypatch)
    app.dependency_overrides[get_current_user] = lambda: MANAGER
    client = TestClient(app)

    preflight = client.get("/api/v1/manager/tickets/416-108096/preflight")
    assert preflight.status_code == 200, preflight.text
    assert preflight.json() == {
        "case_number": "416-108096",
        "status": "unverified",
        "client_display_name": None,
        "can_create": True,
    }

    created = client.post("/api/v1/manager/cards", json=_payload())
    assert created.status_code == 201, created.text
    assert captured["payload"].client_id is None
    assert captured["payload"].omnidesk_ticket_number == "416-108096"

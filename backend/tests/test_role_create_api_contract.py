from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.cards as cards_api
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.repository import CardRecord, ClientRecord
from app.frame.omnidesk import OmnideskTicket
from app.main import create_app
from app.omnidesk_index.resolver import PublicTicketResolutionError


NOW = datetime.now(UTC)
L1 = UserAuthRecord(11, "l1", "hash", "L1", None, (RoleRecord(1, "L1"),))
L2 = UserAuthRecord(12, "l2", "hash", "L2", None, (RoleRecord(2, "L2"),))
ADMIN = UserAuthRecord(13, "admin", "hash", "Admin", None, (RoleRecord(4, "Admin"),))


def _payload(**overrides):
    payload = {
        "case_number": "123-456789",
        "planned_start_at": (NOW + timedelta(hours=3)).isoformat(),
        "planned_duration_minutes": 60,
    }
    payload.update(overrides)
    return payload


def test_role_create_requires_authentication() -> None:
    assert (
        TestClient(create_app()).post("/api/v1/cards/l1", json=_payload()).status_code
        == 401
    )


def test_role_create_rejects_wrong_business_role_before_database(monkeypatch) -> None:
    @contextmanager
    def fail_connection():
        raise AssertionError("database must not be reached")
        yield

    monkeypatch.setattr(cards_api, "db_connection", fail_connection)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    response = TestClient(app).post("/api/v1/cards/l2", json=_payload())
    assert response.status_code == 403
    assert response.json()["detail"] == "action_forbidden"


def test_role_create_payload_rejects_server_owned_fields() -> None:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: L2
    response = TestClient(app).post(
        "/api/v1/cards/l2",
        json=_payload(case_id="internal", l2_engineer_id=999, status_code=5),
    )
    assert response.status_code == 422
    assert "internal" not in response.text
    assert "case_id" not in response.text


def test_role_create_openapi_has_no_internal_case_id() -> None:
    contract = {
        path: operation
        for path, operation in create_app().openapi()["paths"].items()
        if path.startswith("/api/v1/cards/l")
    }
    assert "/api/v1/cards" not in create_app().openapi()["paths"]
    assert "case_id" not in json.dumps(contract)


def test_role_create_maps_public_ticket_failures_without_case_id(monkeypatch) -> None:
    class Connection:
        def rollback(self):
            pass

    @contextmanager
    def connection():
        yield Connection()

    def unavailable(*_args, **_kwargs):
        raise PublicTicketResolutionError(
            status_code=409, detail="omnidesk_ticket_ambiguous"
        )

    monkeypatch.setattr(cards_api, "db_connection", connection)
    monkeypatch.setattr(cards_api, "resolve_ticket_by_case_number", unavailable)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: L2
    response = TestClient(app).post("/api/v1/cards/l2", json=_payload())
    assert response.status_code == 409
    assert response.json()["detail"] == "omnidesk_ticket_ambiguous"
    assert "case_id" not in response.text


def test_l2_create_is_self_assigned_and_audited_as_actual_actor(monkeypatch) -> None:
    captured = {}

    class Repository:
        def has_active_card_for_ticket(self, _number):
            return False

        def get_or_create_client(self, _data):
            return ClientRecord(7, "client", None, "Client")

    class Service:
        def __init__(self, *_args):
            pass

        def create_card(self, _payload, **kwargs):
            captured.update(kwargs)
            return CardRecord(
                id=1,
                public_id=uuid4(),
                number="RDM-000001",
                omnidesk_ticket_number="123-456789",
                client_id=7,
                status_code=1,
                criticality_code=0,
                urgency_code=0,
                planned_start_at=NOW + timedelta(hours=3),
                planned_duration_minutes=60,
                client_timezone_at_creation=None,
                timezone_source_code=None,
                actual_start_at=None,
                actual_end_at=None,
                l1_owner_id=None,
                l2_engineer_id=L2.id,
                assignment_method_code=2,
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
                created_by_id=L2.id,
                created_at=NOW,
                updated_at=NOW,
            )

    class Connection:
        def rollback(self):
            pass

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(cards_api, "db_connection", connection)
    monkeypatch.setattr(cards_api, "PostgresCardRepository", lambda _: Repository())
    monkeypatch.setattr(cards_api, "CardService", Service)
    monkeypatch.setattr(cards_api, "PostgresNotificationService", lambda _: None)
    monkeypatch.setattr(
        cards_api,
        "resolve_ticket_by_case_number",
        lambda *_args: OmnideskTicket(
            case_id="internal-only",
            number="123-456789",
            user_id="client",
            status="open",
        ),
    )
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: L2
    response = TestClient(app).post("/api/v1/cards/l2", json=_payload())
    assert response.status_code == 201
    assert captured["actor_user_id"] == L2.id
    assert captured["role_create_plan"].l2_engineer_id is None
    assert "case_id" not in response.text

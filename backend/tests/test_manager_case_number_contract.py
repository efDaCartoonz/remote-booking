from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.api.manager as manager_api
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.frame.omnidesk import OmnideskTicket
from app.main import create_app
from app.omnidesk_index.repository import (
    CaseIndexRepository,
    CaseIndexTicketAmbiguous,
    CaseIndexTicketNotFound,
)


class Cursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params):
        self.calls.append((query, params))

    def fetchone(self):
        return next(self.rows)


class IndexConnection:
    def __init__(self, rows):
        self.cursor_stub = Cursor(rows)

    def cursor(self):
        return self.cursor_stub


class IndexStub:
    def __init__(self, connection):
        self.connection = connection

    def resolve_case_id(self, case_number):
        if case_number == "404-000001":
            raise CaseIndexTicketNotFound
        if case_number == "409-000001":
            raise CaseIndexTicketAmbiguous
        return "internal-only-id"


class OmnideskStub:
    def get_ticket_by_case_id(self, case_id):
        return OmnideskTicket(
            case_id=case_id,
            number="123-456789",
            user_id="client-1",
            status="open",
        )

    def reopen_ticket(self, case_id):
        raise AssertionError("open ticket must not be reopened")


MANAGER = UserAuthRecord(
    1, "manager", "hash", "Manager", None, (RoleRecord(3, "Manager"),)
)
L1 = UserAuthRecord(2, "l1", "hash", "L1", None, (RoleRecord(1, "L1"),))


def test_case_index_resolves_only_one_available_unconflicted_ticket():
    connection = IndexConnection(
        [None, ("internal-only-id", False, False, False, None)]
    )
    assert (
        CaseIndexRepository(connection).resolve_case_id(" 123-456789 ")
        == "internal-only-id"
    )
    assert [params for _, params in connection.cursor_stub.calls] == [
        ("123-456789",),
        ("123-456789",),
    ]


@pytest.mark.parametrize(
    "rows, error",
    [
        ([(1,)], CaseIndexTicketAmbiguous),
        ([None, None], CaseIndexTicketNotFound),
        (
            [None, ("id", False, False, False, "duplicate_case_number")],
            CaseIndexTicketAmbiguous,
        ),
        ([None, ("id", True, False, False, None)], CaseIndexTicketNotFound),
    ],
)
def test_case_index_refuses_ambiguous_or_unavailable_ticket(rows, error):
    with pytest.raises(error):
        CaseIndexRepository(IndexConnection(rows)).resolve_case_id("123-456789")


def test_case_index_resolver_failure_is_safe_and_never_leaks_internal_id(monkeypatch):
    monkeypatch.setattr(manager_api, "CaseIndexRepository", IndexStub)
    client = OmnideskStub()
    for number, status, detail in (
        ("404-000001", 404, "omnidesk_ticket_not_found"),
        ("409-000001", 409, "omnidesk_ticket_ambiguous"),
    ):
        with pytest.raises(HTTPException) as error:
            manager_api._manager_ticket_by_number(object(), client, number)
        assert error.value.status_code == status
        assert error.value.detail == detail
        assert "case_id" not in error.value.detail
        assert "internal-only-id" not in error.value.detail


def test_resolved_ticket_is_revalidated_against_public_number(monkeypatch):
    monkeypatch.setattr(manager_api, "CaseIndexRepository", IndexStub)
    with pytest.raises(HTTPException) as error:
        manager_api._manager_ticket_by_number(object(), OmnideskStub(), "999-000001")
    assert error.value.status_code == 404
    assert "internal-only-id" not in error.value.detail


def test_manager_create_rejects_case_id_and_checks_auth_before_index(monkeypatch):
    @contextmanager
    def fail_connection():
        raise AssertionError("index/database must not be reached")
        yield

    monkeypatch.setattr(manager_api, "db_connection", fail_connection)
    payload = {
        "case_number": "123-456789",
        "planned_start_at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
    }
    app = create_app()
    assert (
        TestClient(app).post("/api/v1/manager/cards", json=payload).status_code == 401
    )

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: L1
    assert (
        TestClient(app).post("/api/v1/manager/cards", json=payload).status_code == 403
    )

    app = create_app()
    app.dependency_overrides[manager_api.require_manager_role] = lambda: MANAGER
    response = TestClient(app).post(
        "/api/v1/manager/cards", json={**payload, "case_id": "123"}
    )
    assert response.status_code == 422
    assert "internal-only-id" not in response.text


def test_manager_openapi_contract_has_no_case_id():
    openapi = create_app().openapi()
    manager_contract = {
        path: operation
        for path, operation in openapi["paths"].items()
        if path.startswith("/api/v1/manager/")
    }
    serialized = json.dumps(manager_contract)
    assert "/api/v1/manager/tickets/{case_number}/preflight" in manager_contract
    assert "case_id" not in serialized

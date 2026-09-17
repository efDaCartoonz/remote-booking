from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

import app.api.manager as manager_api
from app.api.manager import get_manager_repository
from app.auth.dependencies import get_auth_store, get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import CardStatus
from app.cards.repository import CardRecord, PostgresCardRepository
from app.db import get_db
from app.main import create_app


def make_card(status: CardStatus, number: str, *, overdue: bool = False) -> CardRecord:
    now = datetime(2026, 9, 9, 10, tzinfo=UTC)
    return CardRecord(
        id=1,
        public_id=uuid4(),
        number=number,
        omnidesk_ticket_number="123-456789",
        client_id=7,
        status_code=int(status),
        criticality_code=0,
        urgency_code=1,
        planned_start_at=now,
        planned_duration_minutes=60,
        client_timezone_at_creation="Europe/Moscow",
        timezone_source_code=1,
        actual_start_at=None,
        actual_end_at=None,
        l1_owner_id=2,
        l2_engineer_id=3,
        assignment_method_code=0,
        unsuccessful_cycle_count=0,
        client_contact_type_code=1,
        client_contact_value="secret-contact",
        description="secret-description",
        urgent_reason="secret-reason",
        out_of_hours_flag=True,
        retroactive_flag=False,
        overdue_flag=overdue,
        result_code=None,
        engineer_report="secret-report",
        created_source_code=0,
        created_by_id=1,
        created_at=now,
        updated_at=now,
        l1_owner_name="L1",
        l2_engineer_name="L2",
    )


class ManagerRepository:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.cards = [
            make_card(CardStatus.ASSIGNED, "RDM-000001"),
            make_card(CardStatus.REJECTED, "RDM-000002", overdue=True),
        ]

    def list_manager_cards(self, **kwargs):
        self.calls.append(kwargs)
        return self.cards[: kwargs["limit"]], {
            "assigned": 1,
            "confirmed": 0,
            "rejected": 1,
            "overdue": 1,
        }


def client_for(
    user: UserAuthRecord | None, repository: ManagerRepository | None = None
) -> tuple[TestClient, ManagerRepository]:
    app = create_app()
    repo = repository or ManagerRepository()
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_manager_repository] = lambda: repo
    return TestClient(app), repo


MANAGER = UserAuthRecord(
    1, "manager", "hash", "Manager", None, (RoleRecord(3, "Руководитель"),)
)
L1 = UserAuthRecord(1, "l1", "hash", "L1", None, (RoleRecord(1, "L1"),))


def test_manager_requires_session_and_role(monkeypatch) -> None:
    app = create_app()
    app.dependency_overrides[get_auth_store] = lambda: (_ for _ in ()).throw(
        AssertionError("auth store called")
    )
    app.dependency_overrides[get_db] = lambda: (_ for _ in ()).throw(
        AssertionError("db called")
    )
    assert TestClient(app).get("/api/v1/manager/cards").status_code == 401

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: L1

    @contextmanager
    def fail_connection():
        raise AssertionError("manager repository connection called")
        yield

    monkeypatch.setattr(manager_api, "db_connection", fail_connection)
    assert TestClient(app).get("/api/v1/manager/cards").status_code == 403


def test_manager_validates_status_dates_and_limit() -> None:
    client, _ = client_for(MANAGER)
    for slug in (
        "created",
        "assigned",
        "confirmed",
        "in_progress",
        "rejected",
        "completed",
        "cancelled",
    ):
        assert client.get(f"/api/v1/manager/cards?status={slug}").status_code == 200
    assert client.get("/api/v1/manager/cards?status=unknown").status_code == 422
    assert (
        client.get("/api/v1/manager/cards?period_from=2026-09-09T10:00:00").status_code
        == 422
    )
    assert (
        client.get(
            "/api/v1/manager/cards?period_from=2026-09-09T10:00:00Z&period_to=2026-09-09T11:00:00Z"
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/v1/manager/cards?period_from=2026-09-09T11:00:00Z&period_to=2026-09-09T10:00:00Z"
        ).status_code
        == 422
    )
    assert client.get("/api/v1/manager/cards?limit=1").status_code == 200
    assert client.get("/api/v1/manager/cards?limit=200").status_code == 200
    assert client.get("/api/v1/manager/cards?limit=0").status_code == 422
    assert client.get("/api/v1/manager/cards?limit=201").status_code == 422


def test_invalid_filters_never_create_repository(monkeypatch) -> None:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: MANAGER

    @contextmanager
    def fail_connection():
        raise AssertionError("repository/db called")
        yield

    monkeypatch.setattr(manager_api, "db_connection", fail_connection)
    client = TestClient(app)
    invalid_queries = (
        "status=unknown",
        "period_from=2026-09-09T10:00:00",
        "period_to=2026-09-09T10:00:00",
        "period_from=not-a-date",
        "period_from=2026-09-09T11:00:00Z&period_to=2026-09-09T10:00:00Z",
        "period_from=2026-09-09T10:00:00Z&period_to=2026-09-09T10:00:00Z",
        "limit=0",
        "limit=201",
    )
    for query in invalid_queries:
        assert client.get(f"/api/v1/manager/cards?{query}").status_code == 422


def test_valid_manager_request_opens_repository_connection_once(monkeypatch) -> None:
    repository = ManagerRepository()
    opened = 0

    @contextmanager
    def connection():
        nonlocal opened
        opened += 1
        yield object()

    monkeypatch.setattr(manager_api, "db_connection", connection)
    monkeypatch.setattr(manager_api, "PostgresCardRepository", lambda _: repository)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: MANAGER
    response = TestClient(app).get("/api/v1/manager/cards?status=assigned")
    assert response.status_code == 200
    assert opened == 1


def test_manager_passes_filters_and_summary_is_not_limited() -> None:
    client, repository = client_for(MANAGER)
    response = client.get(
        "/api/v1/manager/cards?status=assigned&period_from=2026-09-09T09:00:00Z&period_to=2026-09-09T11:00:00Z&limit=1"
    )
    assert response.status_code == 200
    assert repository.calls == [
        {
            "status_code": 1,
            "period_from": datetime(2026, 9, 9, 9, tzinfo=UTC),
            "period_to": datetime(2026, 9, 9, 11, tzinfo=UTC),
            "limit": 1,
        }
    ]
    assert response.json()["summary"] == {
        "assigned": 1,
        "confirmed": 0,
        "rejected": 1,
        "overdue": 1,
    }
    assert len(response.json()["items"]) == 1


def test_manager_response_is_safe_projection() -> None:
    client, _ = client_for(MANAGER)
    payload = client.get("/api/v1/manager/cards").json()
    assert set(payload["items"][0]) == {
        "public_id",
        "number",
        "omnidesk_ticket_number",
        "status",
        "status_label",
        "planned_start_at",
        "planned_end_at",
        "planned_duration_minutes",
        "l1_owner_name",
        "l2_engineer_name",
        "urgent",
        "overdue",
        "out_of_hours",
    }
    assert "secret-contact" not in str(payload)
    assert "secret-description" not in str(payload)


class CursorStub:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []
        self.results = [
            [],
            {"assigned": 0, "confirmed": 0, "rejected": 0, "overdue": 0},
        ]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query: str, params: dict) -> None:
        self.queries.append((query, params))

    def fetchall(self):
        return self.results[0]

    def fetchone(self):
        return self.results.pop(1 if len(self.results) == 2 else 0)


class ConnectionStub:
    def __init__(self, cursor: CursorStub) -> None:
        self.cursor_stub = cursor

    def cursor(self):
        return self.cursor_stub


def test_repository_uses_checked_code_filters_and_deterministic_sorting() -> None:
    cursor = CursorStub()
    repository = PostgresCardRepository(ConnectionStub(cursor))
    repository.list_manager_cards(
        status_code=int(CardStatus.ASSIGNED),
        period_from=datetime(2026, 9, 9, 9, tzinfo=UTC),
        period_to=datetime(2026, 9, 9, 11, tzinfo=UTC),
        limit=1,
    )
    query, params = cursor.queries[0]
    assert "c.status_code = %(status)s" in query
    assert "c.planned_start_at >= %(period_from)s" in query
    assert "c.planned_start_at < %(period_to)s" in query
    assert "ORDER BY c.planned_start_at ASC, c.id ASC" in query
    assert params["status"] == int(CardStatus.ASSIGNED)

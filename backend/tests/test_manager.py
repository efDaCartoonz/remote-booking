from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.manager import get_manager_repository
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import CardStatus
from app.cards.repository import CardRecord
from app.main import create_app


def _card(status: CardStatus, number: str, *, overdue: bool = False) -> CardRecord:
    now = datetime(2026, 9, 9, 10, tzinfo=UTC)
    return CardRecord(1, uuid4(), number, "123-456789", None, int(status), 0, 1,
        now, 60, None, None, None, None, None, None, None, 0, 0, None, None,
        None, None, False, False, overdue, None, None, 0, None, now, now,
        l1_owner_name="L1", l2_engineer_name="L2")


class ManagerRepository:
    def list_manager_cards(self, **kwargs):
        cards = [_card(CardStatus.ASSIGNED, "RDM-000001"), _card(CardStatus.REJECTED, "RDM-000002", overdue=True)]
        return cards[: kwargs["limit"]], {"assigned": 1, "confirmed": 0, "rejected": 1, "overdue": 1}


def test_manager_endpoint_requires_manager_role_and_returns_safe_projection():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        1, "manager", "hash", "Manager", None, (RoleRecord(3, "Руководитель"),)
    )
    app.dependency_overrides[get_manager_repository] = ManagerRepository
    response = TestClient(app).get("/api/v1/manager/cards?status=assigned&limit=2")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {"assigned": 1, "confirmed": 0, "rejected": 1, "overdue": 1}
    assert set(payload["items"][0]) == {
        "public_id", "number", "omnidesk_ticket_number", "status", "status_label",
        "planned_start_at", "planned_end_at", "planned_duration_minutes",
        "l1_owner_name", "l2_engineer_name", "urgent", "overdue", "out_of_hours",
    }


def test_manager_endpoint_forbids_user_without_manager_role():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
        1, "l1", "hash", "L1", None, (RoleRecord(1, "L1"),)
    )
    assert TestClient(app).get("/api/v1/manager/cards").status_code == 403

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.cards import get_card_repository, get_card_service
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.policy import CardActionPolicyError
from app.main import create_app

CARD_ID = "00000000-0000-0000-0000-000000000001"


def user(user_id: int, role: int) -> UserAuthRecord:
    return UserAuthRecord(
        user_id, f"user{user_id}", "hash", "Employee", None, (RoleRecord(role, "role"),)
    )


class Repository:
    def __init__(self) -> None:
        self.mine_calls: list[dict] = []
        self.notifications_calls: list[dict] = []

    def list_mine_cards(self, **kwargs):
        self.mine_calls.append(kwargs)
        return []

    def list_card_notifications(self, **kwargs):
        self.notifications_calls.append(kwargs)
        return [
            {
                "event_type_code": 3,
                "channel_code": 0,
                "status_code": 1,
                "scheduled_at": None,
                "sent_at": None,
                "created_at": datetime(2026, 9, 29, tzinfo=UTC),
                "payload": {"case_id": "must-never-leak"},
                "error_message": "must-never-leak",
            }
        ]


class Service:
    def __init__(self, repo: Repository) -> None:
        self.repository = repo
        self.card = SimpleNamespace(id=9, l1_owner_id=11, l2_engineer_id=22)
        self.interval_calls: list[dict] = []

    def get_card_for_user(self, card_id: UUID, *, actor_role_ids):
        assert card_id == UUID(CARD_ID)
        if actor_role_ids == frozenset({4}):
            raise CardActionPolicyError(status_code=403, detail="action_forbidden")
        return self.card

    def l1_reminder_interval(self, card_id: UUID, **kwargs):
        self.interval_calls.append(kwargs)
        if kwargs["actor_user_id"] != 11:
            raise CardActionPolicyError(status_code=403, detail="assigned_l1_required")
        return kwargs.get("interval_minutes") or 10


def test_mine_requires_auth_and_requested_role():
    app = create_app()
    repo = Repository()
    app.dependency_overrides[get_card_repository] = lambda: repo
    with TestClient(app) as client:
        assert client.get("/api/v1/cards/mine?role=l1").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: user(11, 1)
        assert client.get("/api/v1/cards/mine?role=l2").status_code == 403
        response = client.get("/api/v1/cards/mine?role=l1&limit=20")
        assert response.status_code == 200
        assert response.json() == {"items": [], "limit": 20}
        assert repo.mine_calls == [{"user_id": 11, "role": "l1", "limit": 20}]
        assert client.get("/api/v1/cards/mine?role=l1&limit=201").status_code == 422
        app.dependency_overrides[get_current_user] = lambda: user(22, 2)
        response_l2 = client.get("/api/v1/cards/mine?role=l2&limit=50")
        assert response_l2.status_code == 200
        assert repo.mine_calls[-1] == {"user_id": 22, "role": "l2", "limit": 50}


def test_notifications_are_owner_scoped_and_safe():
    app = create_app()
    repo = Repository()
    service = Service(repo)
    app.dependency_overrides[get_card_service] = lambda: service
    with TestClient(app) as client:
        app.dependency_overrides[get_current_user] = lambda: user(33, 2)
        assert client.get(f"/api/v1/cards/{CARD_ID}/notifications").status_code == 403
        assert repo.notifications_calls == []
        app.dependency_overrides[get_current_user] = lambda: user(11, 1)
        response = client.get(f"/api/v1/cards/{CARD_ID}/notifications")
        assert response.status_code == 200
        assert response.json()["items"][0]["status"] == "sent"
        assert repo.notifications_calls[-1]["recipient_user_id"] == 11
        assert "case_id" not in response.text
        assert "must-never-leak" not in response.text
        app.dependency_overrides[get_current_user] = lambda: user(22, 2)
        response_l2 = client.get(f"/api/v1/cards/{CARD_ID}/notifications")
        assert response_l2.status_code == 200
        assert repo.notifications_calls[-1]["recipient_user_id"] == 22
        app.dependency_overrides[get_current_user] = lambda: user(99, 2)
        assert client.get(f"/api/v1/cards/{CARD_ID}/notifications").status_code == 403
        app.dependency_overrides[get_current_user] = lambda: user(44, 3)
        assert client.get(f"/api/v1/cards/{CARD_ID}/notifications").status_code == 200
        assert repo.notifications_calls[-1]["recipient_user_id"] is None


def test_l1_interval_contract_and_validation():
    app = create_app()
    repo = Repository()
    service = Service(repo)
    app.dependency_overrides[get_card_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: user(11, 1)
    with TestClient(app) as client:
        path = f"/api/v1/cards/{CARD_ID}/l1/reminder-interval"
        assert client.get(path).json() == {"interval_minutes": 10, "can_change": True}
        assert client.post(path, json={"interval_minutes": 30}).json() == {
            "interval_minutes": 30,
            "can_change": True,
        }
        assert service.interval_calls[-1]["interval_minutes"] == 30
        for allowed in (0, 15, 60, 120, 240, 480, 1440):
            assert (
                client.post(path, json={"interval_minutes": allowed}).status_code == 200
            )
        assert client.post(path, json={"interval_minutes": 20}).status_code == 422
        app.dependency_overrides[get_current_user] = lambda: user(22, 1)
        assert client.get(path).status_code == 403

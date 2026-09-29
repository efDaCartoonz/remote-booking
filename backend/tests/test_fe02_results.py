from fastapi.testclient import TestClient

from app.admin.repository import ConnectionResult
from app.api.results import get_active_results
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.main import create_app


def _user(role: int) -> UserAuthRecord:
    return UserAuthRecord(
        1, "employee", "hash", "Employee", None, (RoleRecord(role, "role"),)
    )


def test_results_requires_authentication():
    response = TestClient(create_app()).get("/api/v1/cards/results")
    assert response.status_code == 401


def test_results_are_active_and_role_scoped():
    app = create_app()
    app.dependency_overrides[get_active_results] = lambda: [
        ConnectionResult(code=2, name="Готово", is_active=True, sort_order=1)
    ]
    app.dependency_overrides[get_current_user] = lambda: _user(1)
    with TestClient(app) as client:
        assert client.get("/api/v1/cards/results").status_code == 403
        app.dependency_overrides[get_current_user] = lambda: _user(2)
        response = client.get("/api/v1/cards/results")
        assert response.status_code == 200
        assert response.json() == {"items": [{"code": 2, "name": "Готово"}]}
        app.dependency_overrides[get_current_user] = lambda: _user(3)
        assert client.get("/api/v1/cards/results").status_code == 200

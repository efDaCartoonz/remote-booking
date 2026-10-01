from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.admin.catalog import (
    InvalidTemplatePlaceholderError,
    build_integrations_status,
    extract_template_placeholders,
    validate_template_placeholders,
)
from app.api.admin_catalog import (
    ConnectionResultCreateRequest,
    IntegrationsStatusResponse,
    NotificationTemplateUpdateRequest,
    require_admin_role,
    router as admin_catalog_router,
)
from app.auth.dependencies import require_roles
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import RoleId
from app.core.config import settings
from app.db import get_db


def make_user(role_id: RoleId, user_id: int = 1) -> UserAuthRecord:
    return UserAuthRecord(
        id=user_id,
        username=f"user_{user_id}",
        password_hash="fake",
        full_name=f"User {user_id}",
        email=None,
        roles=(RoleRecord(id=int(role_id), name=role_id.name),),
    )


class FakeConnection:
    """Mock connection for unit testing AdministrativeRepository methods."""

    def __init__(self) -> None:
        self.results: dict[int, dict[str, Any]] = {
            1: {"code": 1, "name": "Успешно", "is_active": True, "sort_order": 10},
            2: {
                "code": 2,
                "name": "Отказ клиента",
                "is_active": True,
                "sort_order": 20,
            },
            3: {"code": 3, "name": "Устаревший", "is_active": False, "sort_order": 30},
        }
        self.templates: dict[str, dict[str, Any]] = {
            "card_created": {
                "id": 1,
                "code": "card_created",
                "channel_code": 0,
                "visible": True,
                "subject_template": "Карточка {card_number}",
                "body_template": "Назначена карточка {card_number}; тикет {ticket}; {url}",
            }
        }
        self.audit_log: list[dict[str, Any]] = []
        self.system_settings: dict[str, Any] = {
            "omnidesk_public_notification_enabled": True,
            "omnidesk_cancellation_public_notification_enabled": False,
        }

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.last_fetch: Any = None
        self.rowcount: int = 0

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args) -> None:
        pass

    def execute(self, query: str, params: tuple | None = None) -> None:
        query_str = " ".join(query.strip().split())
        if (
            "SELECT code, name, is_active, sort_order FROM connection_results ORDER BY sort_order, code"
            in query_str
        ):
            rows = sorted(
                self.conn.results.values(), key=lambda r: (r["sort_order"], r["code"])
            )
            self.last_fetch = [dict(r) for r in rows]
        elif (
            "SELECT code, name, is_active, sort_order FROM connection_results WHERE is_active ORDER BY sort_order, code"
            in query_str
        ):
            rows = [r for r in self.conn.results.values() if r["is_active"]]
            rows = sorted(rows, key=lambda r: (r["sort_order"], r["code"]))
            self.last_fetch = [dict(r) for r in rows]
        elif (
            "SELECT code, name, is_active, sort_order FROM connection_results WHERE code=%s FOR UPDATE"
            in query_str
            or "SELECT code, name, is_active, sort_order FROM connection_results WHERE code=%s"
            in query_str
        ):
            code = params[0]
            self.last_fetch = (
                dict(self.conn.results[code]) if code in self.conn.results else None
            )
        elif "SELECT code FROM connection_results WHERE code=%s" in query_str:
            code = params[0]
            self.last_fetch = {"code": code} if code in self.conn.results else None
        elif (
            "SELECT COUNT(*) AS count FROM connection_results WHERE is_active = true AND code != %s"
            in query_str
        ):
            exclude_code = params[0]
            active_count = sum(
                1
                for c, r in self.conn.results.items()
                if r["is_active"] and c != exclude_code
            )
            self.last_fetch = {"count": active_count}
        elif "INSERT INTO connection_results" in query_str:
            code, name, is_active, sort_order = params[:4]
            self.conn.results[code] = {
                "code": code,
                "name": name,
                "is_active": is_active,
                "sort_order": sort_order,
            }
            self.rowcount = 1
        elif "UPDATE connection_results" in query_str:
            name, is_active, sort_order, code = params
            if code in self.conn.results:
                self.conn.results[code].update(
                    {"name": name, "is_active": is_active, "sort_order": sort_order}
                )
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif (
            "SELECT id, code, channel_code, visible, subject_template, body_template FROM notification_templates ORDER BY code"
            in query_str
        ):
            rows = sorted(self.conn.templates.values(), key=lambda t: t["code"])
            self.last_fetch = [dict(t) for t in rows]
        elif (
            "SELECT id, code, channel_code, visible, subject_template, body_template FROM notification_templates WHERE code=%s"
            in query_str
        ):
            code = params[0]
            self.last_fetch = (
                dict(self.conn.templates[code]) if code in self.conn.templates else None
            )
        elif "UPDATE notification_templates" in query_str:
            subject, body, visible, code = params
            if code in self.conn.templates:
                self.conn.templates[code].update(
                    {
                        "subject_template": subject,
                        "body_template": body,
                        "visible": visible,
                    }
                )
                self.last_fetch = dict(self.conn.templates[code])
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif "INSERT INTO audit_log" in query_str:
            self.conn.audit_log.append({"query": query_str, "params": params})
            self.rowcount = 1
        elif "SELECT key, value FROM system_settings" in query_str:
            self.last_fetch = [
                {"key": k, "value": v} for k, v in self.conn.system_settings.items()
            ]

    def fetchone(self) -> Any:
        return self.last_fetch

    def fetchall(self) -> Any:
        return self.last_fetch or []


@pytest.fixture
def fake_connection() -> FakeConnection:
    return FakeConnection()


@pytest.fixture
def app(fake_connection: FakeConnection) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_catalog_router)
    app.dependency_overrides[get_db] = lambda: fake_connection
    return app


# ---------------------------------------------------------------------------
# Unit tests: Placeholders and Schema Validation
# ---------------------------------------------------------------------------


def test_template_placeholders_extraction_and_validation() -> None:
    valid_template = "Карточка {card_number}; тикет {ticket}{client_suffix}; {url}"
    placeholders = extract_template_placeholders(valid_template)
    assert placeholders == {"card_number", "ticket", "client_suffix", "url"}
    validate_template_placeholders(valid_template)

    # Empty template
    assert extract_template_placeholders(None) == set()
    validate_template_placeholders(None)
    assert extract_template_placeholders("") == set()
    validate_template_placeholders("")

    # Unknown placeholder
    unknown_template = "Уведомление с {unknown_placeholder} и {card_number}"
    with pytest.raises(
        InvalidTemplatePlaceholderError, match="unknown_template_placeholders"
    ):
        validate_template_placeholders(unknown_template)

    # Invalid syntax
    invalid_syntax = "Ошибка {незакрытая скобка"
    with pytest.raises(
        InvalidTemplatePlaceholderError, match="invalid_template_syntax"
    ):
        validate_template_placeholders(invalid_syntax)


def test_connection_result_request_validation() -> None:
    # Valid create request
    valid = ConnectionResultCreateRequest(
        code=10, name="Valid Name", is_active=True, sort_order=5
    )
    assert valid.code == 10
    assert valid.name == "Valid Name"

    # Blank name
    with pytest.raises(ValidationError):
        ConnectionResultCreateRequest(code=10, name="   ", is_active=True, sort_order=5)

    # Negative sort order
    with pytest.raises(ValidationError):
        ConnectionResultCreateRequest(
            code=10, name="Valid", is_active=True, sort_order=-1
        )

    # Negative code
    with pytest.raises(ValidationError):
        ConnectionResultCreateRequest(
            code=-1, name="Valid", is_active=True, sort_order=0
        )

    # Extra fields forbidden
    with pytest.raises(ValidationError):
        ConnectionResultCreateRequest.model_validate(
            {"code": 1, "name": "N", "extra": "forbidden"}
        )


def test_notification_template_request_validation() -> None:
    # Valid
    valid = NotificationTemplateUpdateRequest(
        subject_template="Заявка {card_number}",
        body_template="Начало в {timestamp}, длительность {duration} мин. {url}",
        visible=True,
    )
    assert valid.body_template.startswith("Начало")

    # Blank body
    with pytest.raises(ValidationError, match="body_template_cannot_be_blank"):
        NotificationTemplateUpdateRequest(body_template="   ")

    # Body with unknown placeholder
    with pytest.raises(ValidationError, match="unknown_template_placeholders"):
        NotificationTemplateUpdateRequest(body_template="Текст {secret_key}")

    # Body with invalid syntax
    with pytest.raises(ValidationError, match="invalid_template_syntax"):
        NotificationTemplateUpdateRequest(body_template="Текст {unclosed")


# ---------------------------------------------------------------------------
# Unit tests: Integration Status Builder and Secret Leak Protection
# ---------------------------------------------------------------------------


def test_integrations_status_builder_no_secrets_leaked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Set fake secret values in settings
    secret_token = "SUPER_SECRET_TELEGRAM_TOKEN_99999"
    secret_webhook = (
        "https://bitrix.example.com/rest/1/SECRET_WEBHOOK_KEY_ABC123/profile"
    )
    secret_api_key = "OMNIDESK_SUPER_SECRET_KEY_XYZ789"
    secret_app_key = "APP_SECRET_KEY_DO_NOT_EXPOSE"

    monkeypatch.setattr(settings, "telegram_bot_token", secret_token)
    monkeypatch.setattr(settings, "bitrix24_bot_webhook_url", secret_webhook)
    monkeypatch.setattr(settings, "bitrix24_bot_id", "bot_42")
    monkeypatch.setattr(settings, "bitrix24_bot_client_id", "client_42")
    monkeypatch.setattr(settings, "omnidesk_api_key", secret_api_key)
    monkeypatch.setattr(settings, "omnidesk_staff_email", "admin@iridi.com")
    monkeypatch.setattr(settings, "omnidesk_base_url", "https://iridi.omnidesk.ru")
    monkeypatch.setattr(settings, "omnidesk_case_index_enabled", True)
    monkeypatch.setattr(settings, "notification_delivery_enabled", True)
    monkeypatch.setattr(settings, "app_secret_key", secret_app_key)

    status_obj = build_integrations_status(
        public_notification_enabled=True,
        cancellation_public_notification_enabled=False,
    )

    assert status_obj.omnidesk.configured is True
    assert status_obj.omnidesk.enabled is True
    assert status_obj.omnidesk.base_url == "https://iridi.omnidesk.ru"
    assert status_obj.omnidesk.staff_email == "admin@iridi.com"
    assert status_obj.omnidesk.public_notification_enabled is True
    assert status_obj.omnidesk.cancellation_public_notification_enabled is False

    assert status_obj.telegram.configured is True
    assert status_obj.telegram.enabled is True

    assert status_obj.bitrix24.configured is True
    assert status_obj.bitrix24.enabled is True
    assert status_obj.bitrix24.bot_id == "bot_42"
    assert status_obj.bitrix24.bot_client_id == "client_42"

    # Test serialization and assert NO secrets in response payload
    resp = IntegrationsStatusResponse(
        omnidesk=status_obj.omnidesk.__dict__,
        telegram=status_obj.telegram.__dict__,
        bitrix24=status_obj.bitrix24.__dict__,
    )
    serialized_json = resp.model_dump_json()

    assert secret_token not in serialized_json
    assert secret_webhook not in serialized_json
    assert secret_api_key not in serialized_json
    assert secret_app_key not in serialized_json
    assert "SECRET" not in serialized_json
    assert "token" not in serialized_json.lower()
    assert "webhook" not in serialized_json.lower()
    assert "api_key" not in serialized_json.lower()


# ---------------------------------------------------------------------------
# Unit tests: FastAPI API Endpoints RBAC and Responses
# ---------------------------------------------------------------------------


def test_admin_catalog_endpoints_rbac(app: FastAPI) -> None:
    client = TestClient(app)

    # 1. Unauthenticated -> 401
    resp = client.get("/api/v1/admin/results")
    assert resp.status_code == 401

    resp = client.post("/api/v1/admin/results", json={"code": 99, "name": "Test"})
    assert resp.status_code == 401

    resp = client.put(
        "/api/v1/admin/results/1",
        json={"name": "Test", "is_active": True, "sort_order": 1},
    )
    assert resp.status_code == 401

    resp = client.get("/api/v1/admin/notification-templates")
    assert resp.status_code == 401

    resp = client.put(
        "/api/v1/admin/notification-templates/card_created",
        json={"body_template": "Text"},
    )
    assert resp.status_code == 401

    resp = client.get("/api/v1/admin/integrations/status")
    assert resp.status_code == 401

    # 2. Non-admin role (L2, MANAGER) -> 403
    for non_admin_role in (RoleId.L1, RoleId.L2, RoleId.MANAGER):
        app.dependency_overrides[require_roles(int(RoleId.ADMIN))] = lambda: make_user(
            non_admin_role
        )
        # Note: require_roles raises 403 when role is not present in user
        # In our route, require_admin_role is Depends(require_roles(int(RoleId.ADMIN)))
        # So testing with a user having only MANAGER role:
        app.dependency_overrides[require_admin_role] = lambda: (_ for _ in ()).throw(
            __import__("fastapi").HTTPException(status_code=403, detail="forbidden")
        )
        assert client.get("/api/v1/admin/results").status_code == 403
        assert (
            client.post(
                "/api/v1/admin/results", json={"code": 99, "name": "Test"}
            ).status_code
            == 403
        )
        assert (
            client.put(
                "/api/v1/admin/results/1",
                json={"name": "Test", "is_active": True, "sort_order": 1},
            ).status_code
            == 403
        )
        assert client.get("/api/v1/admin/notification-templates").status_code == 403
        assert (
            client.put(
                "/api/v1/admin/notification-templates/card_created",
                json={"body_template": "Text"},
            ).status_code
            == 403
        )
        assert client.get("/api/v1/admin/integrations/status").status_code == 403


def test_results_catalog_crud_and_conflicts(
    app: FastAPI, fake_connection: FakeConnection
) -> None:
    admin_user = make_user(RoleId.ADMIN, user_id=900)
    app.dependency_overrides[require_admin_role] = lambda: admin_user
    client = TestClient(app)

    # 1. GET /results -> returns all results including inactive
    resp = client.get("/api/v1/admin/results")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 3
    assert [r["code"] for r in items] == [1, 2, 3]
    assert items[2]["is_active"] is False

    # 2. POST /results -> creates new result
    resp = client.post(
        "/api/v1/admin/results",
        json={"code": 4, "name": "Перенесено", "is_active": True, "sort_order": 15},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data == {
        "code": 4,
        "name": "Перенесено",
        "is_active": True,
        "sort_order": 15,
    }
    assert 4 in fake_connection.results

    # 3. POST /results duplicate code -> 409
    resp = client.post(
        "/api/v1/admin/results",
        json={"code": 4, "name": "Дубликат", "is_active": True, "sort_order": 15},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "result_code_already_exists"

    # 4. PUT /results/{code} -> update existing
    resp = client.put(
        "/api/v1/admin/results/4",
        json={"name": "Перенесено клиентом", "is_active": False, "sort_order": 25},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Перенесено клиентом"
    assert resp.json()["is_active"] is False

    # 5. PUT /results/{code} not found -> 404
    resp = client.put(
        "/api/v1/admin/results/999",
        json={"name": "Unknown", "is_active": True, "sort_order": 0},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "result_not_found"

    # 6. Deactivate when only one active left -> 409
    # Currently active: 1 and 2
    # Deactivate 2:
    resp = client.put(
        "/api/v1/admin/results/2",
        json={"name": "Отказ", "is_active": False, "sort_order": 20},
    )
    assert resp.status_code == 200
    # Now only 1 is active. Try deactivating 1:
    resp = client.put(
        "/api/v1/admin/results/1",
        json={"name": "Успешно", "is_active": False, "sort_order": 10},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "cannot_deactivate_last_active_result"


def test_notification_templates_crud_and_placeholders(
    app: FastAPI, fake_connection: FakeConnection
) -> None:
    admin_user = make_user(RoleId.ADMIN, user_id=900)
    app.dependency_overrides[require_admin_role] = lambda: admin_user
    client = TestClient(app)

    # 1. GET /notification-templates
    resp = client.get("/api/v1/admin/notification-templates")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["code"] == "card_created"

    # 2. PUT /notification-templates/{code} valid update
    resp = client.put(
        "/api/v1/admin/notification-templates/card_created",
        json={
            "subject_template": "Обновлено: {card_number}",
            "body_template": "Новое тело {card_number}{client_suffix}; {url}",
            "visible": False,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == "card_created"
    assert data["subject_template"] == "Обновлено: {card_number}"
    assert data["visible"] is False

    # 3. PUT /notification-templates/{code} not found -> 404
    resp = client.put(
        "/api/v1/admin/notification-templates/non_existent_code",
        json={"body_template": "Текст {card_number}"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "notification_template_not_found"

    # 4. PUT /notification-templates/{code} unknown placeholder -> 422
    resp = client.put(
        "/api/v1/admin/notification-templates/card_created",
        json={"body_template": "Текст {invalid_placeholder_xyz}"},
    )
    assert resp.status_code == 422


def test_integrations_status_endpoint(app: FastAPI) -> None:
    admin_user = make_user(RoleId.ADMIN, user_id=900)
    app.dependency_overrides[require_admin_role] = lambda: admin_user
    client = TestClient(app)

    resp = client.get("/api/v1/admin/integrations/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "omnidesk" in data
    assert "telegram" in data
    assert "bitrix24" in data
    assert isinstance(data["omnidesk"]["configured"], bool)
    assert isinstance(data["telegram"]["configured"], bool)
    assert isinstance(data["bitrix24"]["configured"], bool)

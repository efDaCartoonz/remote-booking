from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin.users import (
    AdminRoleRecord,
    AdminUserRecord,
    InvalidRoleError,
    LastAdminDeactivationError,
    LastAdminRoleRemovalError,
    OmnideskStaffIdConflictError,
    SelfDeactivationError,
    UserNotFoundError,
    UsernameConflictError,
    VALID_ROLE_IDS,
)
from app.api.admin_users import get_admin_user_repository, router as admin_users_router
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import RoleId


class FakeAdminUserRepository:
    def __init__(self, users: list[AdminUserRecord] | None = None) -> None:
        self.users: dict[int, AdminUserRecord] = {}
        self.audit_log: list[dict[str, Any]] = []
        self.revoked_sessions_for_user: list[int] = []
        self.next_id = 1
        if users:
            for u in users:
                self.users[u.id] = u
                if u.id >= self.next_id:
                    self.next_id = u.id + 1

    def list_users(self) -> list[AdminUserRecord]:
        return list(self.users.values())

    def get_user_by_id(self, user_id: int) -> AdminUserRecord | None:
        return self.users.get(user_id)

    def create_user(
        self,
        *,
        username: str,
        password: str,
        full_name: str,
        email: str | None,
        phone: str | None,
        omnidesk_staff_id: str | None,
        roles: list[int],
        is_active: bool,
        actor_user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminUserRecord:
        role_set = set(roles)
        if not role_set or not role_set.issubset(VALID_ROLE_IDS):
            raise InvalidRoleError("invalid_role_id")

        for u in self.users.values():
            if u.username == username:
                raise UsernameConflictError("username_already_exists")
            if (
                omnidesk_staff_id is not None
                and u.omnidesk_staff_id == omnidesk_staff_id
            ):
                raise OmnideskStaffIdConflictError("omnidesk_staff_id_already_exists")

        role_names = {
            1: "Специалист Л1",
            2: "Инженер Л2",
            3: "Руководитель",
            4: "Администратор",
        }
        user_id = self.next_id
        self.next_id += 1

        record = AdminUserRecord(
            id=user_id,
            username=username,
            full_name=full_name,
            email=email,
            phone=phone,
            omnidesk_staff_id=omnidesk_staff_id,
            is_active=is_active,
            roles=tuple(
                AdminRoleRecord(id=r, name=role_names[r]) for r in sorted(role_set)
            ),
            timezone="Asia/Yekaterinburg",
            telegram_chat_id=None,
            bitrix24_user_id=None,
        )
        self.users[user_id] = record
        self.audit_log.append(
            {
                "actor_user_id": actor_user_id,
                "action": 0,
                "entity_type": "user",
                "entity_id": user_id,
                "old_values": None,
                "new_values": {
                    "username": username,
                    "full_name": full_name,
                    "email": email,
                    "phone": phone,
                    "omnidesk_staff_id": omnidesk_staff_id,
                    "is_active": is_active,
                    "roles": sorted(list(role_set)),
                },
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
        )
        return record

    def update_user(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        omnidesk_staff_id: str | None = None,
        is_active: bool | None = None,
        actor_user_id: int,
        fields_set: set[str] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminUserRecord:
        old_user = self.users.get(user_id)
        if old_user is None:
            raise UserNotFoundError("user_not_found")

        if fields_set is None:
            fields_set = set()
            if full_name is not None:
                fields_set.add("full_name")
            if email is not None:
                fields_set.add("email")
            if phone is not None:
                fields_set.add("phone")
            if omnidesk_staff_id is not None:
                fields_set.add("omnidesk_staff_id")
            if is_active is not None:
                fields_set.add("is_active")

        if "is_active" in fields_set and is_active is False and old_user.is_active:
            if actor_user_id == user_id:
                raise SelfDeactivationError("cannot_deactivate_self")

            admin_count = sum(
                1
                for u in self.users.values()
                if u.is_active and any(r.id == int(RoleId.ADMIN) for r in u.roles)
            )
            is_admin = any(r.id == int(RoleId.ADMIN) for r in old_user.roles)
            if is_admin and admin_count <= 1:
                raise LastAdminDeactivationError("cannot_deactivate_last_admin")

        if "omnidesk_staff_id" in fields_set and omnidesk_staff_id is not None:
            for u in self.users.values():
                if u.id != user_id and u.omnidesk_staff_id == omnidesk_staff_id:
                    raise OmnideskStaffIdConflictError(
                        "omnidesk_staff_id_already_exists"
                    )

        new_full_name = full_name if "full_name" in fields_set else old_user.full_name
        new_email = email if "email" in fields_set else old_user.email
        new_phone = phone if "phone" in fields_set else old_user.phone
        new_omnidesk = (
            omnidesk_staff_id
            if "omnidesk_staff_id" in fields_set
            else old_user.omnidesk_staff_id
        )
        new_active = is_active if "is_active" in fields_set else old_user.is_active

        updated = AdminUserRecord(
            id=old_user.id,
            username=old_user.username,
            full_name=new_full_name
            if new_full_name is not None
            else old_user.full_name,
            email=new_email,
            phone=new_phone,
            omnidesk_staff_id=new_omnidesk,
            is_active=new_active if new_active is not None else old_user.is_active,
            roles=old_user.roles,
            timezone=old_user.timezone,
            telegram_chat_id=old_user.telegram_chat_id,
            bitrix24_user_id=old_user.bitrix24_user_id,
        )
        self.users[user_id] = updated

        if "is_active" in fields_set and is_active is False and old_user.is_active:
            self.revoked_sessions_for_user.append(user_id)

        self.audit_log.append(
            {
                "actor_user_id": actor_user_id,
                "action": 1,
                "entity_type": "user",
                "entity_id": user_id,
                "old_values": {
                    "full_name": old_user.full_name,
                    "is_active": old_user.is_active,
                },
                "new_values": {
                    "full_name": updated.full_name,
                    "is_active": updated.is_active,
                },
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
        )
        return updated

    def update_user_roles(
        self,
        user_id: int,
        *,
        role_ids: list[int],
        actor_user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminUserRecord:
        old_user = self.users.get(user_id)
        if old_user is None:
            raise UserNotFoundError("user_not_found")

        new_role_set = set(role_ids)
        if not new_role_set or not new_role_set.issubset(VALID_ROLE_IDS):
            raise InvalidRoleError("invalid_role_id")

        admin_role_id = int(RoleId.ADMIN)
        old_role_ids = [r.id for r in old_user.roles]
        if (
            admin_role_id in old_role_ids
            and admin_role_id not in new_role_set
            and old_user.is_active
        ):
            admin_count = sum(
                1
                for u in self.users.values()
                if u.is_active and any(r.id == admin_role_id for r in u.roles)
            )
            if admin_count <= 1:
                raise LastAdminRoleRemovalError("cannot_remove_last_admin")

        role_names = {
            1: "Специалист Л1",
            2: "Инженер Л2",
            3: "Руководитель",
            4: "Администратор",
        }
        updated = AdminUserRecord(
            id=old_user.id,
            username=old_user.username,
            full_name=old_user.full_name,
            email=old_user.email,
            phone=old_user.phone,
            omnidesk_staff_id=old_user.omnidesk_staff_id,
            is_active=old_user.is_active,
            roles=tuple(
                AdminRoleRecord(id=r, name=role_names[r]) for r in sorted(new_role_set)
            ),
            timezone=old_user.timezone,
            telegram_chat_id=old_user.telegram_chat_id,
            bitrix24_user_id=old_user.bitrix24_user_id,
        )
        self.users[user_id] = updated
        self.audit_log.append(
            {
                "actor_user_id": actor_user_id,
                "action": 1,
                "entity_type": "user_roles",
                "entity_id": user_id,
                "old_values": {"roles": old_role_ids},
                "new_values": {"roles": sorted(list(new_role_set))},
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
        )
        return updated


def create_test_app(
    repo: FakeAdminUserRepository, actor_user: UserAuthRecord | None
) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_users_router)
    app.dependency_overrides[get_admin_user_repository] = lambda: repo
    if actor_user is not None:
        app.dependency_overrides[get_current_user] = lambda: actor_user
    return app


def make_admin_user(user_id: int = 1, username: str = "admin") -> UserAuthRecord:
    return UserAuthRecord(
        id=user_id,
        username=username,
        password_hash="hash",
        full_name="Admin User",
        email="admin@test.local",
        roles=(RoleRecord(id=int(RoleId.ADMIN), name="Администратор"),),
    )


def make_manager_user(user_id: int = 2, username: str = "manager") -> UserAuthRecord:
    return UserAuthRecord(
        id=user_id,
        username=username,
        password_hash="hash",
        full_name="Manager User",
        email="manager@test.local",
        roles=(RoleRecord(id=int(RoleId.MANAGER), name="Руководитель"),),
    )


def test_list_users_returns_all_users_without_password_hash() -> None:
    admin = make_admin_user(1)
    u1 = AdminUserRecord(
        id=1,
        username="admin",
        full_name="Admin User",
        email="admin@test.local",
        phone="+79991112233",
        omnidesk_staff_id="101",
        is_active=True,
        roles=(AdminRoleRecord(4, "Администратор"),),
        timezone="Asia/Yekaterinburg",
        telegram_chat_id="12345",
        bitrix24_user_id="67890",
    )
    u2 = AdminUserRecord(
        id=2,
        username="l1_user",
        full_name="L1 User",
        email="l1@test.local",
        phone=None,
        omnidesk_staff_id=None,
        is_active=True,
        roles=(AdminRoleRecord(1, "Специалист Л1"),),
        timezone="Europe/Moscow",
        telegram_chat_id=None,
        bitrix24_user_id=None,
    )
    repo = FakeAdminUserRepository([u1, u2])
    app = create_test_app(repo, admin)
    client = TestClient(app)

    response = client.get("/api/v1/admin/users")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["username"] == "admin"
    assert data[0]["omnidesk_staff_id"] == "101"
    assert data[0]["telegram_chat_id"] == "12345"
    assert data[0]["roles"] == [{"id": 4, "name": "Администратор"}]
    assert "password" not in response.text
    assert "password_hash" not in response.text
    assert "case_id" not in response.text


def test_create_user_success_and_audited() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository(
        [
            AdminUserRecord(
                id=1,
                username="admin",
                full_name="Admin User",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(4, "Администратор"),),
            )
        ]
    )
    app = create_test_app(repo, admin)
    client = TestClient(app)

    payload = {
        "username": "new_specialist",
        "password": "secret_password_123",
        "full_name": "Новый Специалист",
        "email": "spec@test.local",
        "phone": "+79001234567",
        "omnidesk_staff_id": "555",
        "roles": [1, 2],
        "is_active": True,
    }
    response = client.post("/api/v1/admin/users", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["username"] == "new_specialist"
    assert data["full_name"] == "Новый Специалист"
    assert data["omnidesk_staff_id"] == "555"
    assert len(data["roles"]) == 2
    assert "secret_password_123" not in response.text
    assert "password_hash" not in response.text

    assert len(repo.audit_log) == 1
    assert repo.audit_log[0]["action"] == 0
    assert repo.audit_log[0]["entity_type"] == "user"
    assert "secret_password_123" not in str(repo.audit_log[0])


def test_create_user_duplicate_username_conflict_409() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository(
        [
            AdminUserRecord(
                id=1,
                username="existing_user",
                full_name="Existing User",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(1, "Специалист Л1"),),
            )
        ]
    )
    app = create_test_app(repo, admin)
    client = TestClient(app)

    response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "existing_user",
            "password": "any",
            "full_name": "Another Name",
            "roles": [1],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "username_already_exists"


def test_create_user_duplicate_omnidesk_staff_id_conflict_409() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository(
        [
            AdminUserRecord(
                id=1,
                username="user1",
                full_name="User 1",
                email=None,
                phone=None,
                omnidesk_staff_id="999",
                is_active=True,
                roles=(AdminRoleRecord(1, "Специалист Л1"),),
            )
        ]
    )
    app = create_test_app(repo, admin)
    client = TestClient(app)

    response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "user2",
            "password": "any",
            "full_name": "User 2",
            "omnidesk_staff_id": "999",
            "roles": [1],
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "omnidesk_staff_id_already_exists"


def test_create_user_invalid_role_422() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository([])
    app = create_test_app(repo, admin)
    client = TestClient(app)

    response = client.post(
        "/api/v1/admin/users",
        json={
            "username": "user_invalid_role",
            "password": "any",
            "full_name": "Test User",
            "roles": [99],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_role_id"


def test_patch_user_self_deactivation_forbidden_409() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository(
        [
            AdminUserRecord(
                id=1,
                username="admin",
                full_name="Admin User",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(4, "Администратор"),),
            )
        ]
    )
    app = create_test_app(repo, admin)
    client = TestClient(app)

    response = client.patch("/api/v1/admin/users/1", json={"is_active": False})
    assert response.status_code == 409
    assert response.json()["detail"] == "cannot_deactivate_self"


def test_patch_user_last_admin_deactivation_forbidden_409() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository(
        [
            AdminUserRecord(
                id=1,
                username="admin1",
                full_name="Admin 1",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(4, "Администратор"),),
            ),
            AdminUserRecord(
                id=2,
                username="other_admin",
                full_name="Other Admin",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=False,  # Already inactive
                roles=(AdminRoleRecord(4, "Администратор"),),
            ),
            AdminUserRecord(
                id=3,
                username="target_admin",
                full_name="Target Admin",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(4, "Администратор"),),
            ),
        ]
    )
    # Now deactivate id=3 when there are 2 active admins (id=1 and id=3) -> allowed
    app = create_test_app(repo, admin)
    client = TestClient(app)

    resp1 = client.patch("/api/v1/admin/users/3", json={"is_active": False})
    assert resp1.status_code == 200
    assert resp1.json()["is_active"] is False

    # Now only id=1 is active admin. If actor tries to deactivate id=1 (self or if other actor) -> 409
    resp2 = client.patch("/api/v1/admin/users/1", json={"is_active": False})
    assert resp2.status_code == 409


def test_patch_user_deactivation_revokes_sessions() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository(
        [
            AdminUserRecord(
                id=1,
                username="admin",
                full_name="Admin User",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(4, "Администратор"),),
            ),
            AdminUserRecord(
                id=2,
                username="specialist",
                full_name="Specialist User",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(1, "Специалист Л1"),),
            ),
        ]
    )
    app = create_test_app(repo, admin)
    client = TestClient(app)

    response = client.patch("/api/v1/admin/users/2", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert 2 in repo.revoked_sessions_for_user


def test_put_user_roles_cannot_remove_last_admin_409() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository(
        [
            AdminUserRecord(
                id=1,
                username="admin",
                full_name="Admin User",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(4, "Администратор"),),
            ),
        ]
    )
    app = create_test_app(repo, admin)
    client = TestClient(app)

    response = client.put("/api/v1/admin/users/1/roles", json={"roles": [1, 2]})
    assert response.status_code == 409
    assert response.json()["detail"] == "cannot_remove_last_admin"


def test_put_user_roles_success_and_audited() -> None:
    admin = make_admin_user(1)
    repo = FakeAdminUserRepository(
        [
            AdminUserRecord(
                id=1,
                username="admin",
                full_name="Admin User",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(4, "Администратор"),),
            ),
            AdminUserRecord(
                id=2,
                username="spec",
                full_name="Spec User",
                email=None,
                phone=None,
                omnidesk_staff_id=None,
                is_active=True,
                roles=(AdminRoleRecord(1, "Специалист Л1"),),
            ),
        ]
    )
    app = create_test_app(repo, admin)
    client = TestClient(app)

    response = client.put("/api/v1/admin/users/2/roles", json={"roles": [2, 3]})
    assert response.status_code == 200
    data = response.json()
    role_ids = [r["id"] for r in data["roles"]]
    assert role_ids == [2, 3]

    assert len(repo.audit_log) == 1
    assert repo.audit_log[0]["entity_type"] == "user_roles"
    assert repo.audit_log[0]["old_values"] == {"roles": [1]}
    assert repo.audit_log[0]["new_values"] == {"roles": [2, 3]}


def test_non_admin_forbidden_403() -> None:
    manager = make_manager_user(2)
    repo = FakeAdminUserRepository([])
    app = create_test_app(repo, manager)
    client = TestClient(app)

    response = client.get("/api/v1/admin/users")
    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient_role"


def test_unauthenticated_401() -> None:
    repo = FakeAdminUserRepository([])
    app = create_test_app(repo, None)
    client = TestClient(app)

    response = client.get("/api/v1/admin/users")
    assert response.status_code == 401
    assert response.json()["detail"] == "not_authenticated"

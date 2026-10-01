from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb

from app.auth.security import hash_password
from app.cards.constants import ActorType, AuditAction, RoleId


@dataclass(frozen=True)
class AdminRoleRecord:
    id: int
    name: str


UNSET: Any = object()  # distinguishes "not provided" from an explicit null


@dataclass(frozen=True)
class AdminUserRecord:
    id: int
    username: str
    full_name: str
    email: str | None
    phone: str | None
    omnidesk_staff_id: str | None
    is_active: bool
    roles: tuple[AdminRoleRecord, ...]
    timezone: str = "Asia/Yekaterinburg"
    telegram_chat_id: str | None = None
    bitrix24_user_id: str | None = None
    notify_telegram: bool = True
    notify_bitrix24: bool = True


class AdminUserError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class UserNotFoundError(AdminUserError):
    def __init__(self, detail: str = "user_not_found") -> None:
        super().__init__(detail)


class UsernameConflictError(AdminUserError):
    def __init__(self, detail: str = "username_already_exists") -> None:
        super().__init__(detail)


class OmnideskStaffIdConflictError(AdminUserError):
    def __init__(self, detail: str = "omnidesk_staff_id_already_exists") -> None:
        super().__init__(detail)


class SelfDeactivationError(AdminUserError):
    def __init__(self, detail: str = "cannot_deactivate_self") -> None:
        super().__init__(detail)


class LastAdminDeactivationError(AdminUserError):
    def __init__(self, detail: str = "cannot_deactivate_last_admin") -> None:
        super().__init__(detail)


class LastAdminRoleRemovalError(AdminUserError):
    def __init__(self, detail: str = "cannot_remove_last_admin") -> None:
        super().__init__(detail)


class InvalidRoleError(AdminUserError):
    def __init__(self, detail: str = "invalid_role_id") -> None:
        super().__init__(detail)


VALID_ROLE_IDS = frozenset(
    {
        int(RoleId.L1),
        int(RoleId.L2),
        int(RoleId.MANAGER),
        int(RoleId.ADMIN),
    }
)


class AdminUserRepository(Protocol):
    def list_users(self) -> list[AdminUserRecord]: ...

    def get_user_by_id(self, user_id: int) -> AdminUserRecord | None: ...

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
        telegram_chat_id: str | None = None,
        bitrix24_user_id: str | None = None,
        notify_telegram: bool = True,
        notify_bitrix24: bool = True,
        actor_user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminUserRecord: ...

    def update_user(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        omnidesk_staff_id: str | None = None,
        is_active: bool | None = None,
        telegram_chat_id: str | None = UNSET,
        bitrix24_user_id: str | None = UNSET,
        notify_telegram: bool | None = None,
        notify_bitrix24: bool | None = None,
        actor_user_id: int,
        fields_set: set[str] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminUserRecord: ...

    def update_user_roles(
        self,
        user_id: int,
        *,
        role_ids: list[int],
        actor_user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminUserRecord: ...


class PostgresAdminUserRepository:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def list_users(self) -> list[AdminUserRecord]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.full_name,
                    u.email,
                    u.phone,
                    u.omnidesk_staff_id,
                    u.is_active,
                    COALESCE(us.timezone, 'Asia/Yekaterinburg') AS timezone,
                    us.telegram_chat_id,
                    us.bitrix24_user_id,
                    COALESCE(us.notify_telegram, true) AS notify_telegram,
                    COALESCE(us.notify_bitrix24, true) AS notify_bitrix24
                FROM users u
                LEFT JOIN user_settings us ON us.user_id = u.id
                ORDER BY u.id
                """
            )
            users_rows = cursor.fetchall()
            if not users_rows:
                return []

            cursor.execute(
                """
                SELECT ur.user_id, r.id AS role_id, r.name AS role_name
                FROM user_roles ur
                JOIN roles r ON r.id = ur.role_id
                WHERE r.visible = true
                ORDER BY ur.user_id, r.id
                """
            )
            roles_rows = cursor.fetchall()

        roles_by_user: dict[int, list[AdminRoleRecord]] = {}
        for row in roles_rows:
            roles_by_user.setdefault(row["user_id"], []).append(
                AdminRoleRecord(id=row["role_id"], name=row["role_name"])
            )

        return [
            AdminUserRecord(
                id=u["id"],
                username=u["username"],
                full_name=u["full_name"],
                email=u["email"],
                phone=u["phone"],
                omnidesk_staff_id=u["omnidesk_staff_id"],
                is_active=u["is_active"],
                roles=tuple(roles_by_user.get(u["id"], [])),
                timezone=u["timezone"],
                telegram_chat_id=u["telegram_chat_id"],
                bitrix24_user_id=u["bitrix24_user_id"],
                notify_telegram=u["notify_telegram"],
                notify_bitrix24=u["notify_bitrix24"],
            )
            for u in users_rows
        ]

    def get_user_by_id(self, user_id: int) -> AdminUserRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.full_name,
                    u.email,
                    u.phone,
                    u.omnidesk_staff_id,
                    u.is_active,
                    COALESCE(us.timezone, 'Asia/Yekaterinburg') AS timezone,
                    us.telegram_chat_id,
                    us.bitrix24_user_id,
                    COALESCE(us.notify_telegram, true) AS notify_telegram,
                    COALESCE(us.notify_bitrix24, true) AS notify_bitrix24
                FROM users u
                LEFT JOIN user_settings us ON us.user_id = u.id
                WHERE u.id = %(user_id)s
                """,
                {"user_id": user_id},
            )
            u = cursor.fetchone()
            if u is None:
                return None

            cursor.execute(
                """
                SELECT r.id, r.name
                FROM roles r
                JOIN user_roles ur ON ur.role_id = r.id
                WHERE ur.user_id = %(user_id)s AND r.visible = true
                ORDER BY r.id
                """,
                {"user_id": user_id},
            )
            roles_rows = cursor.fetchall()

        roles = tuple(AdminRoleRecord(id=r["id"], name=r["name"]) for r in roles_rows)
        return AdminUserRecord(
            id=u["id"],
            username=u["username"],
            full_name=u["full_name"],
            email=u["email"],
            phone=u["phone"],
            omnidesk_staff_id=u["omnidesk_staff_id"],
            is_active=u["is_active"],
            roles=roles,
            timezone=u["timezone"],
            telegram_chat_id=u["telegram_chat_id"],
            bitrix24_user_id=u["bitrix24_user_id"],
            notify_telegram=u["notify_telegram"],
            notify_bitrix24=u["notify_bitrix24"],
        )

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
        telegram_chat_id: str | None = None,
        bitrix24_user_id: str | None = None,
        notify_telegram: bool = True,
        notify_bitrix24: bool = True,
        actor_user_id: int,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminUserRecord:
        role_set = set(roles)
        if not role_set or not role_set.issubset(VALID_ROLE_IDS):
            raise InvalidRoleError("invalid_role_id")

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM users WHERE username = %(username)s",
                {"username": username},
            )
            if cursor.fetchone() is not None:
                raise UsernameConflictError("username_already_exists")

            if omnidesk_staff_id is not None:
                cursor.execute(
                    "SELECT id FROM users WHERE omnidesk_staff_id = %(omnidesk_staff_id)s",
                    {"omnidesk_staff_id": omnidesk_staff_id},
                )
                if cursor.fetchone() is not None:
                    raise OmnideskStaffIdConflictError(
                        "omnidesk_staff_id_already_exists"
                    )

            pwd_hash = hash_password(password)

            cursor.execute(
                """
                INSERT INTO users (username, password_hash, full_name, email, phone, omnidesk_staff_id, is_active)
                VALUES (%(username)s, %(password_hash)s, %(full_name)s, %(email)s, %(phone)s, %(omnidesk_staff_id)s, %(is_active)s)
                RETURNING id
                """,
                {
                    "username": username,
                    "password_hash": pwd_hash,
                    "full_name": full_name,
                    "email": email,
                    "phone": phone,
                    "omnidesk_staff_id": omnidesk_staff_id,
                    "is_active": is_active,
                },
            )
            user_id = cursor.fetchone()["id"]

            for r_id in sorted(role_set):
                cursor.execute(
                    """
                    INSERT INTO user_roles (user_id, role_id)
                    VALUES (%(user_id)s, %(role_id)s)
                    ON CONFLICT DO NOTHING
                    """,
                    {"user_id": user_id, "role_id": r_id},
                )

            if (
                telegram_chat_id is not None
                or bitrix24_user_id is not None
                or not notify_telegram
                or not notify_bitrix24
            ):
                cursor.execute(
                    """
                    INSERT INTO user_settings (
                        user_id,
                        timezone,
                        telegram_chat_id,
                        bitrix24_user_id,
                        notify_telegram,
                        notify_bitrix24
                    )
                    VALUES (
                        %(user_id)s,
                        'Asia/Yekaterinburg',
                        %(telegram_chat_id)s,
                        %(bitrix24_user_id)s,
                        %(notify_telegram)s,
                        %(notify_bitrix24)s
                    )
                    ON CONFLICT (user_id) DO UPDATE SET
                        telegram_chat_id = EXCLUDED.telegram_chat_id,
                        bitrix24_user_id = EXCLUDED.bitrix24_user_id,
                        notify_telegram = EXCLUDED.notify_telegram,
                        notify_bitrix24 = EXCLUDED.notify_bitrix24
                    """,
                    {
                        "user_id": user_id,
                        "telegram_chat_id": telegram_chat_id,
                        "bitrix24_user_id": bitrix24_user_id,
                        "notify_telegram": notify_telegram,
                        "notify_bitrix24": notify_bitrix24,
                    },
                )

            audit_new_values: dict[str, Any] = {
                "username": username,
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "omnidesk_staff_id": omnidesk_staff_id,
                "is_active": is_active,
                "roles": sorted(list(role_set)),
            }
            if telegram_chat_id is not None:
                audit_new_values["telegram_chat_id"] = telegram_chat_id
            if bitrix24_user_id is not None:
                audit_new_values["bitrix24_user_id"] = bitrix24_user_id
            if not notify_telegram:
                audit_new_values["notify_telegram"] = notify_telegram
            if not notify_bitrix24:
                audit_new_values["notify_bitrix24"] = notify_bitrix24

            self._audit(
                actor_user_id=actor_user_id,
                action=AuditAction.CREATE,
                entity_type="user",
                entity_id=user_id,
                old_values=None,
                new_values=audit_new_values,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        created = self.get_user_by_id(user_id)
        if created is None:
            raise UserNotFoundError("user_not_found")
        return created

    def update_user(
        self,
        user_id: int,
        *,
        full_name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        omnidesk_staff_id: str | None = None,
        is_active: bool | None = None,
        telegram_chat_id: str | None = UNSET,
        bitrix24_user_id: str | None = UNSET,
        notify_telegram: bool | None = None,
        notify_bitrix24: bool | None = None,
        actor_user_id: int,
        fields_set: set[str] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AdminUserRecord:
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
            if telegram_chat_id is not UNSET:
                fields_set.add("telegram_chat_id")
            if bitrix24_user_id is not UNSET:
                fields_set.add("bitrix24_user_id")
            if notify_telegram is not None:
                fields_set.add("notify_telegram")
            if notify_bitrix24 is not None:
                fields_set.add("notify_bitrix24")

        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    u.id,
                    u.username,
                    u.full_name,
                    u.email,
                    u.phone,
                    u.omnidesk_staff_id,
                    u.is_active,
                    COALESCE(us.timezone, 'Asia/Yekaterinburg') AS timezone,
                    us.telegram_chat_id,
                    us.bitrix24_user_id,
                    COALESCE(us.notify_telegram, true) AS notify_telegram,
                    COALESCE(us.notify_bitrix24, true) AS notify_bitrix24
                FROM users u
                LEFT JOIN user_settings us ON us.user_id = u.id
                WHERE u.id = %(user_id)s
                FOR UPDATE OF u
                """,
                {"user_id": user_id},
            )
            old_user = cursor.fetchone()
            if old_user is None:
                raise UserNotFoundError("user_not_found")

            # Self-deactivation check
            if (
                "is_active" in fields_set
                and is_active is False
                and old_user["is_active"]
            ):
                if actor_user_id == user_id:
                    raise SelfDeactivationError("cannot_deactivate_self")

                # Last active admin check
                cursor.execute(
                    """
                    SELECT count(DISTINCT u.id) AS admin_count
                    FROM users u
                    JOIN user_roles ur ON ur.user_id = u.id
                    WHERE u.is_active = true AND ur.role_id = %(admin_role)s
                    """,
                    {"admin_role": int(RoleId.ADMIN)},
                )
                admin_count = cursor.fetchone()["admin_count"]

                cursor.execute(
                    """
                    SELECT 1 FROM user_roles
                    WHERE user_id = %(user_id)s AND role_id = %(admin_role)s
                    """,
                    {"user_id": user_id, "admin_role": int(RoleId.ADMIN)},
                )
                is_admin = cursor.fetchone() is not None
                if is_admin and admin_count <= 1:
                    raise LastAdminDeactivationError("cannot_deactivate_last_admin")

            # Omnidesk staff ID uniqueness check
            if "omnidesk_staff_id" in fields_set and omnidesk_staff_id is not None:
                if omnidesk_staff_id != old_user["omnidesk_staff_id"]:
                    cursor.execute(
                        """
                        SELECT id FROM users
                        WHERE omnidesk_staff_id = %(omnidesk_staff_id)s AND id != %(user_id)s
                        """,
                        {"omnidesk_staff_id": omnidesk_staff_id, "user_id": user_id},
                    )
                    if cursor.fetchone() is not None:
                        raise OmnideskStaffIdConflictError(
                            "omnidesk_staff_id_already_exists"
                        )

            updates: list[str] = []
            params: dict[str, Any] = {"user_id": user_id}
            old_values: dict[str, Any] = {}
            new_values: dict[str, Any] = {}

            if "full_name" in fields_set:
                updates.append("full_name = %(full_name)s")
                params["full_name"] = full_name
                old_values["full_name"] = old_user["full_name"]
                new_values["full_name"] = full_name

            if "email" in fields_set:
                updates.append("email = %(email)s")
                params["email"] = email
                old_values["email"] = old_user["email"]
                new_values["email"] = email

            if "phone" in fields_set:
                updates.append("phone = %(phone)s")
                params["phone"] = phone
                old_values["phone"] = old_user["phone"]
                new_values["phone"] = phone

            if "omnidesk_staff_id" in fields_set:
                updates.append("omnidesk_staff_id = %(omnidesk_staff_id)s")
                params["omnidesk_staff_id"] = omnidesk_staff_id
                old_values["omnidesk_staff_id"] = old_user["omnidesk_staff_id"]
                new_values["omnidesk_staff_id"] = omnidesk_staff_id

            if "is_active" in fields_set:
                updates.append("is_active = %(is_active)s")
                params["is_active"] = is_active
                old_values["is_active"] = old_user["is_active"]
                new_values["is_active"] = is_active

            if "telegram_chat_id" in fields_set:
                old_values["telegram_chat_id"] = old_user["telegram_chat_id"]
                new_values["telegram_chat_id"] = telegram_chat_id

            if "bitrix24_user_id" in fields_set:
                old_values["bitrix24_user_id"] = old_user["bitrix24_user_id"]
                new_values["bitrix24_user_id"] = bitrix24_user_id

            if "notify_telegram" in fields_set:
                old_values["notify_telegram"] = old_user["notify_telegram"]
                new_values["notify_telegram"] = notify_telegram

            if "notify_bitrix24" in fields_set:
                old_values["notify_bitrix24"] = old_user["notify_bitrix24"]
                new_values["notify_bitrix24"] = notify_bitrix24

            if updates:
                cursor.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE id = %(user_id)s",
                    params,
                )

                if (
                    "is_active" in fields_set
                    and is_active is False
                    and old_user["is_active"]
                ):
                    cursor.execute(
                        """
                        UPDATE auth_sessions
                        SET revoked_at = now()
                        WHERE user_id = %(user_id)s AND revoked_at IS NULL
                        """,
                        {"user_id": user_id},
                    )

            user_settings_fields = {
                "telegram_chat_id",
                "bitrix24_user_id",
                "notify_telegram",
                "notify_bitrix24",
            }
            if user_settings_fields.intersection(fields_set):
                target_tg = (
                    telegram_chat_id
                    if "telegram_chat_id" in fields_set
                    else old_user["telegram_chat_id"]
                )
                target_b24 = (
                    bitrix24_user_id
                    if "bitrix24_user_id" in fields_set
                    else old_user["bitrix24_user_id"]
                )
                target_notify_tg = (
                    notify_telegram
                    if "notify_telegram" in fields_set
                    else old_user["notify_telegram"]
                )
                target_notify_b24 = (
                    notify_bitrix24
                    if "notify_bitrix24" in fields_set
                    else old_user["notify_bitrix24"]
                )
                cursor.execute(
                    """
                    INSERT INTO user_settings (
                        user_id,
                        timezone,
                        telegram_chat_id,
                        bitrix24_user_id,
                        notify_telegram,
                        notify_bitrix24
                    )
                    VALUES (
                        %(user_id)s,
                        %(timezone)s,
                        %(telegram_chat_id)s,
                        %(bitrix24_user_id)s,
                        %(notify_telegram)s,
                        %(notify_bitrix24)s
                    )
                    ON CONFLICT (user_id)
                    DO UPDATE SET
                        telegram_chat_id = CASE WHEN %(set_tg)s THEN %(telegram_chat_id)s ELSE user_settings.telegram_chat_id END,
                        bitrix24_user_id = CASE WHEN %(set_b24)s THEN %(bitrix24_user_id)s ELSE user_settings.bitrix24_user_id END,
                        notify_telegram = CASE WHEN %(set_notif_tg)s THEN %(notify_telegram)s ELSE user_settings.notify_telegram END,
                        notify_bitrix24 = CASE WHEN %(set_notif_b24)s THEN %(notify_bitrix24)s ELSE user_settings.notify_bitrix24 END
                    """,
                    {
                        "user_id": user_id,
                        "timezone": old_user["timezone"],
                        "telegram_chat_id": target_tg,
                        "bitrix24_user_id": target_b24,
                        "notify_telegram": target_notify_tg,
                        "notify_bitrix24": target_notify_b24,
                        "set_tg": "telegram_chat_id" in fields_set,
                        "set_b24": "bitrix24_user_id" in fields_set,
                        "set_notif_tg": "notify_telegram" in fields_set,
                        "set_notif_b24": "notify_bitrix24" in fields_set,
                    },
                )

            if old_values or new_values:
                self._audit(
                    actor_user_id=actor_user_id,
                    action=AuditAction.UPDATE,
                    entity_type="user",
                    entity_id=user_id,
                    old_values=old_values,
                    new_values=new_values,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )

        updated = self.get_user_by_id(user_id)
        if updated is None:
            raise UserNotFoundError("user_not_found")
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
        new_role_set = set(role_ids)
        if not new_role_set or not new_role_set.issubset(VALID_ROLE_IDS):
            raise InvalidRoleError("invalid_role_id")

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, is_active FROM users WHERE id = %(user_id)s FOR UPDATE",
                {"user_id": user_id},
            )
            user_row = cursor.fetchone()
            if user_row is None:
                raise UserNotFoundError("user_not_found")

            cursor.execute(
                "SELECT role_id FROM user_roles WHERE user_id = %(user_id)s ORDER BY role_id",
                {"user_id": user_id},
            )
            old_role_ids = [r["role_id"] for r in cursor.fetchall()]

            admin_role_id = int(RoleId.ADMIN)
            if (
                admin_role_id in old_role_ids
                and admin_role_id not in new_role_set
                and user_row["is_active"]
            ):
                cursor.execute(
                    """
                    SELECT count(DISTINCT u.id) AS admin_count
                    FROM users u
                    JOIN user_roles ur ON ur.user_id = u.id
                    WHERE u.is_active = true AND ur.role_id = %(admin_role)s
                    """,
                    {"admin_role": admin_role_id},
                )
                admin_count = cursor.fetchone()["admin_count"]
                if admin_count <= 1:
                    raise LastAdminRoleRemovalError("cannot_remove_last_admin")

            cursor.execute(
                "DELETE FROM user_roles WHERE user_id = %(user_id)s",
                {"user_id": user_id},
            )
            for r_id in sorted(new_role_set):
                cursor.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (%(user_id)s, %(role_id)s)",
                    {"user_id": user_id, "role_id": r_id},
                )

            self._audit(
                actor_user_id=actor_user_id,
                action=AuditAction.UPDATE,
                entity_type="user_roles",
                entity_id=user_id,
                old_values={"roles": old_role_ids},
                new_values={"roles": sorted(list(new_role_set))},
                ip_address=ip_address,
                user_agent=user_agent,
            )

        updated = self.get_user_by_id(user_id)
        if updated is None:
            raise UserNotFoundError("user_not_found")
        return updated

    def _audit(
        self,
        *,
        actor_user_id: int | None,
        action: AuditAction,
        entity_type: str,
        entity_id: int,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        actor_type: ActorType = ActorType.INTERNAL_USER,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_log (
                    actor_user_id,
                    actor_type_code,
                    action_code,
                    entity_type,
                    entity_id,
                    ip_address,
                    user_agent,
                    old_values,
                    new_values
                )
                VALUES (
                    %(actor_user_id)s,
                    %(actor_type_code)s,
                    %(action_code)s,
                    %(entity_type)s,
                    %(entity_id)s,
                    %(ip_address)s,
                    %(user_agent)s,
                    %(old_values)s,
                    %(new_values)s
                )
                """,
                {
                    "actor_user_id": actor_user_id,
                    "actor_type_code": int(actor_type),
                    "action_code": int(action),
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                    "old_values": (
                        Jsonb(dict(old_values)) if old_values is not None else None
                    ),
                    "new_values": (
                        Jsonb(dict(new_values)) if new_values is not None else None
                    ),
                },
            )

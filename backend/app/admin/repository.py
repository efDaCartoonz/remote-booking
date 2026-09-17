from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from app.cards.constants import ActorType, AuditAction, CardEventType


@dataclass(frozen=True)
class ConnectionResult:
    code: int
    name: str
    is_active: bool
    sort_order: int


@dataclass(frozen=True)
class WorkSchedule:
    weekday: int
    start_time: time
    end_time: time
    timezone: str
    valid_from: date | None = None
    valid_to: date | None = None


class AdministrativeRepository:
    """The single persistence boundary for DB-02 administrative policies.

    Callers own the transaction; every mutation records an audit row through
    this repository before the transaction can commit.
    """

    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def list_active_results(self) -> list[ConnectionResult]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT code, name, is_active, sort_order FROM connection_results "
                "WHERE is_active ORDER BY sort_order, code"
            )
            return [ConnectionResult(**row) for row in cursor.fetchall()]

    def upsert_result(self, *, result: ConnectionResult, actor_user_id: int) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT name, is_active, sort_order FROM connection_results WHERE code=%s FOR UPDATE",
                (result.code,),
            )
            previous = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO connection_results (code, name, is_active, sort_order)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name,
                    is_active=EXCLUDED.is_active, sort_order=EXCLUDED.sort_order,
                    updated_at=now()
                """,
                (result.code, result.name, result.is_active, result.sort_order),
            )
        self._audit(
            actor_user_id=actor_user_id,
            action=AuditAction.CREATE if previous is None else AuditAction.UPDATE,
            entity_type="connection_result",
            entity_id=result.code,
            old_values=previous,
            new_values={
                "name": result.name,
                "is_active": result.is_active,
                "sort_order": result.sort_order,
            },
        )

    def set_calendar_day(
        self,
        *,
        calendar_date: date,
        day_type_code: int,
        is_manual_override: bool,
        comment: str | None,
        actor_user_id: int,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT day_type_code, is_manual_override, comment FROM production_calendar_days "
                "WHERE date=%s FOR UPDATE",
                (calendar_date,),
            )
            previous = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO production_calendar_days
                    (date, day_type_code, is_manual_override, updated_by_id, comment)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (date) DO UPDATE SET day_type_code=EXCLUDED.day_type_code,
                    is_manual_override=EXCLUDED.is_manual_override,
                    updated_by_id=EXCLUDED.updated_by_id, updated_at=now(), comment=EXCLUDED.comment
                """,
                (
                    calendar_date,
                    day_type_code,
                    is_manual_override,
                    actor_user_id,
                    comment,
                ),
            )
        self._audit(
            actor_user_id=actor_user_id,
            action=AuditAction.CREATE if previous is None else AuditAction.UPDATE,
            entity_type="production_calendar_day",
            entity_id=calendar_date.toordinal(),
            old_values=previous,
            new_values={
                "day_type_code": day_type_code,
                "is_manual_override": is_manual_override,
                "comment": comment,
            },
        )

    def set_distribution_membership(
        self,
        *,
        user_id: int,
        pool_code: int,
        enabled: bool,
        actor_user_id: int,
        comment: str | None,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT is_enabled FROM distribution_members WHERE user_id=%s AND pool_code=%s FOR UPDATE",
                (user_id, pool_code),
            )
            previous = cursor.fetchone()
            cursor.execute(
                """
                INSERT INTO distribution_members (user_id, pool_code, is_enabled, enabled_by_id, enabled_at, comment)
                VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END, %s)
                ON CONFLICT (user_id, pool_code) DO UPDATE SET is_enabled=EXCLUDED.is_enabled,
                    enabled_by_id=CASE WHEN EXCLUDED.is_enabled THEN EXCLUDED.enabled_by_id ELSE distribution_members.enabled_by_id END,
                    enabled_at=CASE WHEN EXCLUDED.is_enabled THEN now() ELSE distribution_members.enabled_at END,
                    disabled_by_id=CASE WHEN NOT EXCLUDED.is_enabled THEN EXCLUDED.enabled_by_id ELSE distribution_members.disabled_by_id END,
                    disabled_at=CASE WHEN NOT EXCLUDED.is_enabled THEN now() ELSE distribution_members.disabled_at END,
                    comment=EXCLUDED.comment
                RETURNING id
                """,
                (user_id, pool_code, enabled, actor_user_id, enabled, comment),
            )
            membership_id = int(cursor.fetchone()["id"])
        self._audit(
            actor_user_id=actor_user_id,
            action=AuditAction.CREATE if previous is None else AuditAction.UPDATE,
            entity_type="distribution_membership",
            entity_id=membership_id,
            old_values=previous,
            new_values={"pool_code": pool_code, "enabled": enabled, "comment": comment},
        )

    def set_extension_interval(
        self, *, interval_seconds: int, actor_user_id: int
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT value FROM system_settings WHERE key=%s FOR UPDATE",
                ("session_extension_interval_seconds",),
            )
            previous = cursor.fetchone()
            cursor.execute(
                "UPDATE system_settings SET value=%s, updated_by_id=%s, updated_at=now() WHERE key=%s",
                (
                    Jsonb(interval_seconds),
                    actor_user_id,
                    "session_extension_interval_seconds",
                ),
            )
        self._audit(
            actor_user_id=actor_user_id,
            action=AuditAction.UPDATE,
            entity_type="system_setting",
            entity_id=0,
            old_values=previous,
            new_values={
                "key": "session_extension_interval_seconds",
                "value": interval_seconds,
            },
        )

    def add_absence(
        self,
        *,
        user_id: int,
        start_at: datetime,
        end_at: datetime,
        actor_user_id: int,
        reason: str | None,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO absences (user_id, start_at, end_at, created_by_id, reason) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (user_id, start_at, end_at, actor_user_id, reason),
            )
            absence_id = int(cursor.fetchone()["id"])
        self._audit(
            actor_user_id=actor_user_id,
            action=AuditAction.CREATE,
            entity_type="absence",
            entity_id=absence_id,
            old_values=None,
            new_values={
                "user_id": user_id,
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "reason": reason,
            },
        )
        return absence_id

    def replace_schedules(
        self, *, user_id: int, schedules: list[WorkSchedule], actor_user_id: int
    ) -> None:
        for schedule in schedules:
            if schedule.start_time >= schedule.end_time:
                raise ValueError("schedule_start_must_precede_end")
            if (
                schedule.valid_from
                and schedule.valid_to
                and schedule.valid_from > schedule.valid_to
            ):
                raise ValueError("schedule_validity_range_invalid")
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) AS count FROM schedules WHERE user_id=%s", (user_id,)
            )
            previous_count = int(cursor.fetchone()["count"])
            cursor.execute("DELETE FROM schedules WHERE user_id=%s", (user_id,))
            for schedule in schedules:
                cursor.execute(
                    """
                    INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone, valid_from, valid_to)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        schedule.weekday,
                        schedule.start_time,
                        schedule.end_time,
                        schedule.timezone,
                        schedule.valid_from,
                        schedule.valid_to,
                    ),
                )
        self._audit(
            actor_user_id=actor_user_id,
            action=AuditAction.UPDATE,
            entity_type="schedule",
            entity_id=user_id,
            old_values={"count": previous_count},
            new_values={"count": len(schedules)},
        )

    def is_out_of_hours(
        self, *, user_id: int, start_at: datetime, end_at: datetime
    ) -> bool:
        """Return whether an interval falls outside the stored calendar or schedule."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT weekday, start_time, end_time, timezone, valid_from, valid_to
                FROM schedules
                WHERE user_id=%s AND is_active
                ORDER BY start_time, id
                """,
                (user_id,),
            )
            schedules = cursor.fetchall()
        for schedule in schedules:
            zone = ZoneInfo(schedule["timezone"])
            local_start = start_at.astimezone(zone)
            local_end = end_at.astimezone(zone)
            if local_start.date() != local_end.date():
                continue
            if schedule["weekday"] != local_start.isoweekday():
                continue
            if schedule["valid_from"] and schedule["valid_from"] > local_start.date():
                continue
            if schedule["valid_to"] and schedule["valid_to"] < local_start.date():
                continue
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT day_type_code FROM production_calendar_days WHERE date=%s",
                    (local_start.date(),),
                )
                calendar = cursor.fetchone()
            if calendar is not None and calendar["day_type_code"] != 0:
                return True
            if (
                schedule["start_time"] <= local_start.time()
                and local_end.time() <= schedule["end_time"]
            ):
                return False
        return True

    def record_session_extension(
        self,
        *,
        card_id: int,
        previous_planned_end_at: datetime,
        new_planned_end_at: datetime,
        interval_seconds: int,
        collision_card_id: int | None,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT status_code FROM connection_cards WHERE id=%s FOR UPDATE",
                (card_id,),
            )
            card = cursor.fetchone()
            if card is None:
                raise ValueError("connection_card_not_found")
            if card["status_code"] != 3:
                raise ValueError("extension_requires_in_progress_card")
            cursor.execute(
                """
                INSERT INTO card_events (card_id, event_type_code, actor_type_code, old_values, new_values, comment)
                VALUES (%s, %s, %s, %s, %s, 'session_extended') RETURNING id
                """,
                (
                    card_id,
                    int(CardEventType.SESSION_EXTENDED),
                    int(ActorType.SYSTEM),
                    Jsonb({"planned_end_at": previous_planned_end_at.isoformat()}),
                    Jsonb(
                        {
                            "planned_end_at": new_planned_end_at.isoformat(),
                            "interval_seconds": interval_seconds,
                            "collision_card_id": collision_card_id,
                        }
                    ),
                ),
            )
            event_id = int(cursor.fetchone()["id"])
            cursor.execute(
                """
                INSERT INTO session_extensions (card_id, previous_planned_end_at, new_planned_end_at, interval_seconds, has_collision, collision_card_id, event_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (
                    card_id,
                    previous_planned_end_at,
                    new_planned_end_at,
                    interval_seconds,
                    collision_card_id is not None,
                    collision_card_id,
                    event_id,
                ),
            )
            extension_id = int(cursor.fetchone()["id"])
            cursor.execute(
                """
                UPDATE connection_cards SET extension_count=extension_count + 1,
                    extension_collision_at=CASE WHEN %s THEN now() ELSE extension_collision_at END,
                    extension_collision_flag=extension_collision_flag OR %s,
                    extension_collision_details=CASE WHEN %s THEN jsonb_build_object('extension_id', %s::bigint, 'collision_card_id', %s::bigint) ELSE extension_collision_details END,
                    updated_at=now()
                WHERE id=%s
                """,
                (
                    collision_card_id is not None,
                    collision_card_id is not None,
                    collision_card_id is not None,
                    extension_id,
                    collision_card_id,
                    card_id,
                ),
            )
        self._audit(
            actor_user_id=None,
            actor_type=ActorType.SYSTEM,
            action=AuditAction.UPDATE,
            entity_type="connection_card",
            entity_id=card_id,
            old_values={"planned_end_at": previous_planned_end_at.isoformat()},
            new_values={
                "planned_end_at": new_planned_end_at.isoformat(),
                "extension_id": extension_id,
                "collision_card_id": collision_card_id,
            },
        )
        return extension_id

    def _audit(
        self,
        *,
        actor_user_id: int | None,
        action: AuditAction,
        entity_type: str,
        entity_id: int,
        old_values,
        new_values,
        actor_type: ActorType = ActorType.INTERNAL_USER,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO audit_log (actor_user_id, actor_type_code, action_code, entity_type, entity_id, old_values, new_values)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    actor_user_id,
                    int(actor_type),
                    int(action),
                    entity_type,
                    entity_id,
                    Jsonb(dict(old_values)) if old_values else None,
                    Jsonb(new_values),
                ),
            )

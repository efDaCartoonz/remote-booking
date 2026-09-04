from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from app.assignments.types import (
    AssignmentAttemptRecord,
    AssignmentCycleRecord,
    L1DistributionCandidate,
    L2DistributionCandidate,
    ScheduleWindow,
    TimeInterval,
)
from app.cards.constants import (
    TERMINAL_STATUSES,
    ActorType,
    AssignmentAttemptStatus,
    AssignmentCycleStatus,
    AuditAction,
    CardEventType,
    CardStatus,
    CreatedSource,
    DistributionPool,
    RoleId,
)
from app.assignments.manager_escalation import ManagerRecipient


@dataclass(frozen=True)
class CardRecord:
    id: int
    public_id: UUID
    number: str
    omnidesk_ticket_number: str
    client_id: int | None
    status_code: int
    criticality_code: int
    urgency_code: int
    planned_start_at: datetime
    planned_duration_minutes: int
    client_timezone_at_creation: str | None
    timezone_source_code: int | None
    actual_start_at: datetime | None
    actual_end_at: datetime | None
    l1_owner_id: int | None
    l2_engineer_id: int | None
    assignment_method_code: int
    unsuccessful_cycle_count: int
    client_contact_type_code: int | None
    client_contact_value: str | None
    description: str | None
    urgent_reason: str | None
    out_of_hours_flag: bool
    retroactive_flag: bool
    overdue_flag: bool
    result_code: int | None
    engineer_report: str | None
    created_source_code: int
    created_by_id: int | None
    created_at: datetime
    updated_at: datetime
    client_informed: bool = False


@dataclass(frozen=True)
class ClientRecord:
    id: int
    omnidesk_user_id: str
    omnidesk_company_id: str | None
    display_name: str | None


@dataclass(frozen=True)
class ClientSyncData:
    omnidesk_user_id: str
    omnidesk_company_id: str | None = None
    display_name: str | None = None
    preferred_contact_type_code: int | None = None
    preferred_contact_value: str | None = None
    last_confirmed_timezone: str | None = None
    timezone_source_code: int | None = None


@dataclass(frozen=True)
class CreateCardData:
    omnidesk_ticket_number: str
    planned_start_at: datetime
    planned_duration_minutes: int
    created_by_id: int | None
    status: CardStatus
    client_id: int | None = None
    criticality_code: int = 0
    urgency_code: int = 0
    client_timezone_at_creation: str | None = None
    timezone_source_code: int | None = None
    l1_owner_id: int | None = None
    l2_engineer_id: int | None = None
    assignment_method_code: int = 0
    client_contact_type_code: int | None = None
    client_contact_value: str | None = None
    description: str | None = None
    urgent_reason: str | None = None
    out_of_hours_flag: bool = False
    retroactive_flag: bool = False
    created_source_code: int = int(CreatedSource.INTERNAL)


@dataclass(frozen=True)
class StatusUpdateData:
    status: CardStatus
    actor_user_id: int
    l2_engineer_id: int | None = None
    update_l2_engineer_id: bool = False
    actual_start_at: datetime | None = None
    actual_end_at: datetime | None = None
    result_code: int | None = None
    engineer_report: str | None = None


@dataclass(frozen=True)
class L1FollowupUpdateData:
    planned_start_at: datetime | None = None
    planned_duration_minutes: int | None = None
    description: str | None = None
    client_informed: bool | None = None
    reset_for_new_cycle: bool = False


class CardRepository(Protocol):
    def list_active_manager_recipients(self) -> list[ManagerRecipient]: ...
    def create_card(self, data: CreateCardData) -> CardRecord: ...

    def get_or_create_client(self, data: ClientSyncData) -> ClientRecord: ...

    def list_cards_by_ticket(self, omnidesk_ticket_number: str) -> list[CardRecord]: ...

    def has_active_card_for_ticket(self, omnidesk_ticket_number: str) -> bool: ...

    def get_card_by_public_id(self, public_id: UUID) -> CardRecord | None: ...

    def get_card_by_public_id_for_update(
        self, public_id: UUID
    ) -> CardRecord | None: ...

    def update_card_status(
        self, public_id: UUID, data: StatusUpdateData
    ) -> CardRecord | None: ...

    def update_l1_followup(
        self, public_id: UUID, data: L1FollowupUpdateData
    ) -> CardRecord | None: ...

    def add_card_event(
        self,
        *,
        card_id: int,
        event_type: CardEventType,
        actor_user_id: int | None,
        actor_type: ActorType,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        comment: str | None,
    ) -> int: ...

    def add_audit_log(
        self,
        *,
        actor_user_id: int | None,
        actor_type: ActorType,
        action: AuditAction,
        entity_id: int,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        ip_address: str | None,
        user_agent: str | None,
        entity_type: str = "connection_card",
    ) -> None: ...


class PostgresCardRepository:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def list_active_manager_recipients(self) -> list[ManagerRecipient]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, us.telegram_chat_id, us.bitrix24_user_id
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id AND ur.role_id = %(role)s
                LEFT JOIN user_settings us ON us.user_id = u.id
                WHERE u.is_active
                ORDER BY u.id
                """, {"role": int(RoleId.MANAGER)},
            )
            return [ManagerRecipient(r["id"], r["telegram_chat_id"], r["bitrix24_user_id"]) for r in cursor.fetchall()]

    def create_card(self, data: CreateCardData) -> CardRecord:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO connection_cards (
                    number,
                    omnidesk_ticket_number,
                    client_id,
                    status_code,
                    criticality_code,
                    urgency_code,
                    planned_start_at,
                    planned_duration_minutes,
                    client_timezone_at_creation,
                    timezone_source_code,
                    l1_owner_id,
                    l2_engineer_id,
                    assignment_method_code,
                    client_contact_type_code,
                    client_contact_value,
                    description,
                    urgent_reason,
                    out_of_hours_flag,
                    retroactive_flag,
                    created_source_code,
                    created_by_id
                )
                VALUES (
                    NULL,
                    %(omnidesk_ticket_number)s,
                    %(client_id)s,
                    %(status_code)s,
                    %(criticality_code)s,
                    %(urgency_code)s,
                    %(planned_start_at)s,
                    %(planned_duration_minutes)s,
                    %(client_timezone_at_creation)s,
                    %(timezone_source_code)s,
                    %(l1_owner_id)s,
                    %(l2_engineer_id)s,
                    %(assignment_method_code)s,
                    %(client_contact_type_code)s,
                    %(client_contact_value)s,
                    %(description)s,
                    %(urgent_reason)s,
                    %(out_of_hours_flag)s,
                    %(retroactive_flag)s,
                    %(created_source_code)s,
                    %(created_by_id)s
                )
                RETURNING *
                """,
                {
                    "omnidesk_ticket_number": data.omnidesk_ticket_number,
                    "client_id": data.client_id,
                    "status_code": int(data.status),
                    "criticality_code": data.criticality_code,
                    "urgency_code": data.urgency_code,
                    "planned_start_at": data.planned_start_at,
                    "planned_duration_minutes": data.planned_duration_minutes,
                    "client_timezone_at_creation": data.client_timezone_at_creation,
                    "timezone_source_code": data.timezone_source_code,
                    "l1_owner_id": data.l1_owner_id,
                    "l2_engineer_id": data.l2_engineer_id,
                    "assignment_method_code": data.assignment_method_code,
                    "client_contact_type_code": data.client_contact_type_code,
                    "client_contact_value": data.client_contact_value,
                    "description": data.description,
                    "urgent_reason": data.urgent_reason,
                    "out_of_hours_flag": data.out_of_hours_flag,
                    "retroactive_flag": data.retroactive_flag,
                    "created_source_code": data.created_source_code,
                    "created_by_id": data.created_by_id,
                },
            )
            row = cursor.fetchone()
        return _card_from_row(row)

    def update_l1_followup(
        self, public_id: UUID, data: L1FollowupUpdateData
    ) -> CardRecord | None:
        fields = ["updated_at = now()"]
        params: dict[str, Any] = {"public_id": public_id}
        if data.planned_start_at is not None:
            fields.append("planned_start_at = %(start)s")
            params["start"] = data.planned_start_at
        if data.planned_duration_minutes is not None:
            fields.append("planned_duration_minutes = %(duration)s")
            params["duration"] = data.planned_duration_minutes
        if data.description is not None:
            fields.append("description = %(description)s")
            params["description"] = data.description
        if data.client_informed is not None:
            fields.append("client_informed = %(client_informed)s")
            params["client_informed"] = data.client_informed
        if data.reset_for_new_cycle:
            fields.extend(
                ["status_code = 0", "l1_owner_id = NULL", "client_informed = false"]
            )
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE connection_cards SET {', '.join(fields)} WHERE public_id = %(public_id)s AND status_code = 4 AND l1_owner_id IS NOT NULL RETURNING *",
                params,
            )
            row = cursor.fetchone()
        return _card_from_row(row) if row is not None else None

    def list_l2_distribution_candidates(
        self, *, planned_start_at: datetime, planned_end_at: datetime
    ) -> list[L2DistributionCandidate]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN distribution_members dm ON dm.user_id = u.id
                WHERE u.is_active
                  AND ur.role_id = %(l2_role_id)s
                  AND dm.pool_code = %(l2_pool_code)s
                  AND dm.is_enabled
                ORDER BY u.id
                """,
                {
                    "l2_role_id": int(RoleId.L2),
                    "l2_pool_code": int(DistributionPool.L2),
                },
            )
            user_ids = [row["id"] for row in cursor.fetchall()]
        if not user_ids:
            return []

        return [
            L2DistributionCandidate(
                user_id=user_id,
                schedules=self._list_schedule_windows(user_id),
                absences=self._list_absence_intervals(
                    user_id,
                    planned_start_at=planned_start_at,
                    planned_end_at=planned_end_at,
                ),
                active_cards=self._list_active_card_intervals(
                    user_id,
                    planned_start_at=planned_start_at,
                    planned_end_at=planned_end_at,
                ),
            )
            for user_id in user_ids
        ]

    def list_l1_distribution_candidates(
        self, *, planned_start_at: datetime, planned_end_at: datetime
    ) -> list[L1DistributionCandidate]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN distribution_members dm ON dm.user_id = u.id
                WHERE u.is_active AND ur.role_id = %(role_id)s
                  AND dm.pool_code = %(pool_code)s AND dm.is_enabled
                ORDER BY u.id
                """,
                {"role_id": int(RoleId.L1), "pool_code": int(DistributionPool.L1)},
            )
            user_ids = [row["id"] for row in cursor.fetchall()]
        return [
            L1DistributionCandidate(
                user_id=user_id,
                schedules=tuple(self._list_schedule_windows(user_id)),
                absences=tuple(
                    self._list_absence_intervals(
                        user_id,
                        planned_start_at=planned_start_at,
                        planned_end_at=planned_end_at,
                    )
                ),
                active_cards=tuple(
                    self._list_active_card_intervals(
                        user_id,
                        planned_start_at=planned_start_at,
                        planned_end_at=planned_end_at,
                        owner_field="l1_owner_id",
                    )
                ),
            )
            for user_id in user_ids
        ]

    def update_l1_owner(self, *, card_id: int, l1_owner_id: int) -> CardRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE connection_cards
                SET l1_owner_id = %(owner)s, updated_at = now()
                WHERE id = %(id)s
                  AND status_code = %(rejected_status)s
                  AND l1_owner_id IS NULL
                RETURNING *
                """,
                {
                    "owner": l1_owner_id,
                    "id": card_id,
                    "rejected_status": int(CardStatus.REJECTED),
                },
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _card_from_row(row)

    def get_distribution_last_user_id_for_update(
        self, pool: DistributionPool
    ) -> int | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO distribution_state (pool_code, last_user_id)
                VALUES (%(pool_code)s, NULL)
                ON CONFLICT (pool_code) DO NOTHING
                """,
                {"pool_code": int(pool)},
            )
            cursor.execute(
                """
                SELECT last_user_id
                FROM distribution_state
                WHERE pool_code = %(pool_code)s
                FOR UPDATE
                """,
                {"pool_code": int(pool)},
            )
            row = cursor.fetchone()
        return row["last_user_id"] if row is not None else None

    def update_distribution_state(
        self, *, pool: DistributionPool, last_user_id: int
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO distribution_state (pool_code, last_user_id, updated_at)
                VALUES (%(pool_code)s, %(last_user_id)s, now())
                ON CONFLICT (pool_code) DO UPDATE
                SET last_user_id = EXCLUDED.last_user_id,
                    updated_at = now()
                """,
                {"pool_code": int(pool), "last_user_id": last_user_id},
            )

    def get_next_assignment_cycle_number(self, card_id: int) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(MAX(cycle_number), 0) + 1 AS next_cycle_number
                FROM assignment_cycles
                WHERE card_id = %(card_id)s
                """,
                {"card_id": card_id},
            )
            row = cursor.fetchone()
        return row["next_cycle_number"]

    def create_assignment_cycle(
        self,
        *,
        card_id: int,
        cycle_number: int,
        status: AssignmentCycleStatus,
    ) -> AssignmentCycleRecord:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO assignment_cycles (card_id, cycle_number, status_code)
                VALUES (%(card_id)s, %(cycle_number)s, %(status_code)s)
                RETURNING id, card_id, cycle_number, status_code
                """,
                {
                    "card_id": card_id,
                    "cycle_number": cycle_number,
                    "status_code": int(status),
                },
            )
            row = cursor.fetchone()
        return AssignmentCycleRecord(
            id=row["id"],
            card_id=row["card_id"],
            cycle_number=row["cycle_number"],
            status_code=row["status_code"],
        )

    def update_assignment_cycle_status(
        self, *, cycle_id: int, status: AssignmentCycleStatus
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE assignment_cycles
                SET status_code = %(status_code)s,
                    completed_at = now()
                WHERE id = %(cycle_id)s
                """,
                {"cycle_id": cycle_id, "status_code": int(status)},
            )

    def get_current_assignment_cycle_for_update(
        self, card_id: int
    ) -> AssignmentCycleRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, card_id, cycle_number, status_code
                FROM assignment_cycles
                WHERE card_id = %(card_id)s
                  AND status_code IN (%(in_progress_status)s, %(assigned_status)s)
                ORDER BY cycle_number DESC, id DESC
                LIMIT 1
                FOR UPDATE
                """,
                {
                    "card_id": card_id,
                    "in_progress_status": int(AssignmentCycleStatus.IN_PROGRESS),
                    "assigned_status": int(AssignmentCycleStatus.ASSIGNED),
                },
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return AssignmentCycleRecord(
            id=row["id"],
            card_id=row["card_id"],
            cycle_number=row["cycle_number"],
            status_code=row["status_code"],
        )

    def create_assignment_attempt(
        self,
        *,
        cycle_id: int,
        card_id: int,
        l2_engineer_id: int,
        status: AssignmentAttemptStatus,
    ) -> AssignmentAttemptRecord:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO assignment_attempts (
                    cycle_id,
                    card_id,
                    l2_engineer_id,
                    status_code
                )
                VALUES (
                    %(cycle_id)s,
                    %(card_id)s,
                    %(l2_engineer_id)s,
                    %(status_code)s
                )
                RETURNING id, cycle_id, card_id, l2_engineer_id, status_code
                """,
                {
                    "cycle_id": cycle_id,
                    "card_id": card_id,
                    "l2_engineer_id": l2_engineer_id,
                    "status_code": int(status),
                },
            )
            row = cursor.fetchone()
        return AssignmentAttemptRecord(
            id=row["id"],
            cycle_id=row["cycle_id"],
            card_id=row["card_id"],
            l2_engineer_id=row["l2_engineer_id"],
            status_code=row["status_code"],
        )

    def get_pending_assignment_attempt_for_update(
        self, *, card_id: int, l2_engineer_id: int
    ) -> AssignmentAttemptRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    cycle_id,
                    card_id,
                    l2_engineer_id,
                    status_code,
                    responded_at,
                    actor_user_id,
                    rejection_reason
                FROM assignment_attempts
                WHERE card_id = %(card_id)s
                  AND l2_engineer_id = %(l2_engineer_id)s
                  AND status_code = %(pending_status)s
                ORDER BY id DESC
                LIMIT 1
                FOR UPDATE
                """,
                {
                    "card_id": card_id,
                    "l2_engineer_id": l2_engineer_id,
                    "pending_status": int(AssignmentAttemptStatus.PENDING),
                },
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _assignment_attempt_from_row(row)

    def list_attempted_l2_engineer_ids(self, cycle_id: int) -> set[int]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT l2_engineer_id
                FROM assignment_attempts
                WHERE cycle_id = %(cycle_id)s
                """,
                {"cycle_id": cycle_id},
            )
            rows = cursor.fetchall()
        return {row["l2_engineer_id"] for row in rows}

    def update_assignment_attempt_response(
        self,
        *,
        attempt_id: int,
        status: AssignmentAttemptStatus,
        actor_user_id: int,
        rejection_reason: str | None,
    ) -> AssignmentAttemptRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE assignment_attempts
                SET status_code = %(status_code)s,
                    responded_at = now(),
                    actor_user_id = %(actor_user_id)s,
                    rejection_reason = %(rejection_reason)s
                WHERE id = %(attempt_id)s
                  AND status_code = %(pending_status)s
                RETURNING
                    id,
                    cycle_id,
                    card_id,
                    l2_engineer_id,
                    status_code,
                    responded_at,
                    actor_user_id,
                    rejection_reason
                """,
                {
                    "attempt_id": attempt_id,
                    "status_code": int(status),
                    "actor_user_id": actor_user_id,
                    "rejection_reason": rejection_reason,
                    "pending_status": int(AssignmentAttemptStatus.PENDING),
                },
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _assignment_attempt_from_row(row)

    def update_card_distribution_result(
        self,
        *,
        card_id: int,
        status: CardStatus,
        l2_engineer_id: int | None,
        increment_unsuccessful_cycle_count: bool,
    ) -> CardRecord:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE connection_cards
                SET status_code = %(status_code)s,
                    l2_engineer_id = %(l2_engineer_id)s,
                    unsuccessful_cycle_count = unsuccessful_cycle_count + CASE
                        WHEN %(increment_unsuccessful_cycle_count)s THEN 1
                        ELSE 0
                    END,
                    updated_at = now()
                WHERE id = %(card_id)s
                RETURNING *
                """,
                {
                    "card_id": card_id,
                    "status_code": int(status),
                    "l2_engineer_id": l2_engineer_id,
                    "increment_unsuccessful_cycle_count": (
                        increment_unsuccessful_cycle_count
                    ),
                },
            )
            row = cursor.fetchone()
        return _card_from_row(row)

    def get_or_create_client(self, data: ClientSyncData) -> ClientRecord:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO clients (
                    omnidesk_user_id,
                    omnidesk_company_id,
                    display_name,
                    preferred_contact_type_code,
                    preferred_contact_value,
                    last_confirmed_timezone,
                    timezone_source_code,
                    last_synced_at
                )
                VALUES (
                    %(omnidesk_user_id)s,
                    %(omnidesk_company_id)s,
                    %(display_name)s,
                    %(preferred_contact_type_code)s,
                    %(preferred_contact_value)s,
                    %(last_confirmed_timezone)s,
                    %(timezone_source_code)s,
                    now()
                )
                ON CONFLICT (omnidesk_user_id) DO UPDATE
                SET
                    omnidesk_company_id = COALESCE(
                        EXCLUDED.omnidesk_company_id,
                        clients.omnidesk_company_id
                    ),
                    display_name = COALESCE(EXCLUDED.display_name, clients.display_name),
                    preferred_contact_type_code = COALESCE(
                        EXCLUDED.preferred_contact_type_code,
                        clients.preferred_contact_type_code
                    ),
                    preferred_contact_value = COALESCE(
                        EXCLUDED.preferred_contact_value,
                        clients.preferred_contact_value
                    ),
                    last_confirmed_timezone = COALESCE(
                        EXCLUDED.last_confirmed_timezone,
                        clients.last_confirmed_timezone
                    ),
                    timezone_source_code = COALESCE(
                        EXCLUDED.timezone_source_code,
                        clients.timezone_source_code
                    ),
                    last_synced_at = now()
                RETURNING id, omnidesk_user_id, omnidesk_company_id, display_name
                """,
                {
                    "omnidesk_user_id": data.omnidesk_user_id,
                    "omnidesk_company_id": data.omnidesk_company_id,
                    "display_name": data.display_name,
                    "preferred_contact_type_code": data.preferred_contact_type_code,
                    "preferred_contact_value": data.preferred_contact_value,
                    "last_confirmed_timezone": data.last_confirmed_timezone,
                    "timezone_source_code": data.timezone_source_code,
                },
            )
            row = cursor.fetchone()
        return ClientRecord(
            id=row["id"],
            omnidesk_user_id=row["omnidesk_user_id"],
            omnidesk_company_id=row["omnidesk_company_id"],
            display_name=row["display_name"],
        )

    def list_cards_by_ticket(self, omnidesk_ticket_number: str) -> list[CardRecord]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM connection_cards
                WHERE omnidesk_ticket_number = %(omnidesk_ticket_number)s
                ORDER BY planned_start_at DESC, id DESC
                """,
                {"omnidesk_ticket_number": omnidesk_ticket_number},
            )
            rows = cursor.fetchall()
        return [_card_from_row(row) for row in rows]

    def has_active_card_for_ticket(self, omnidesk_ticket_number: str) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM connection_cards
                WHERE omnidesk_ticket_number = %(omnidesk_ticket_number)s
                  AND status_code <> ALL(%(terminal_statuses)s)
                LIMIT 1
                """,
                {
                    "omnidesk_ticket_number": omnidesk_ticket_number,
                    "terminal_statuses": [int(status) for status in TERMINAL_STATUSES],
                },
            )
            row = cursor.fetchone()
        return row is not None

    def get_card_by_public_id(self, public_id: UUID) -> CardRecord | None:
        return self._get_card(public_id, lock=False)

    def get_card_by_public_id_for_update(self, public_id: UUID) -> CardRecord | None:
        return self._get_card(public_id, lock=True)

    def update_card_status(
        self, public_id: UUID, data: StatusUpdateData
    ) -> CardRecord | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE connection_cards
                SET
                    status_code = %(status_code)s,
                    l2_engineer_id = CASE
                        WHEN %(update_l2_engineer_id)s THEN %(l2_engineer_id)s
                        ELSE l2_engineer_id
                    END,
                    actual_start_at = COALESCE(%(actual_start_at)s, actual_start_at),
                    actual_end_at = COALESCE(%(actual_end_at)s, actual_end_at),
                    result_code = COALESCE(%(result_code)s, result_code),
                    engineer_report = COALESCE(%(engineer_report)s, engineer_report),
                    updated_at = now()
                WHERE public_id = %(public_id)s
                RETURNING *
                """,
                {
                    "public_id": public_id,
                    "status_code": int(data.status),
                    "l2_engineer_id": data.l2_engineer_id,
                    "update_l2_engineer_id": data.update_l2_engineer_id,
                    "actual_start_at": data.actual_start_at,
                    "actual_end_at": data.actual_end_at,
                    "result_code": data.result_code,
                    "engineer_report": data.engineer_report,
                },
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _card_from_row(row)

    def add_card_event(
        self,
        *,
        card_id: int,
        event_type: CardEventType,
        actor_user_id: int | None,
        actor_type: ActorType,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        comment: str | None,
    ) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO card_events (
                    card_id,
                    event_type_code,
                    actor_user_id,
                    actor_type_code,
                    old_values,
                    new_values,
                    comment
                )
                VALUES (
                    %(card_id)s,
                    %(event_type_code)s,
                    %(actor_user_id)s,
                    %(actor_type_code)s,
                    %(old_values)s,
                    %(new_values)s,
                    %(comment)s
                )
                RETURNING id
                """,
                {
                    "card_id": card_id,
                    "event_type_code": int(event_type),
                    "actor_user_id": actor_user_id,
                    "actor_type_code": int(actor_type),
                    "old_values": Jsonb(old_values) if old_values is not None else None,
                    "new_values": Jsonb(new_values) if new_values is not None else None,
                    "comment": comment,
                },
            )
            return int(cursor.fetchone()["id"])

    def add_audit_log(
        self,
        *,
        actor_user_id: int | None,
        actor_type: ActorType,
        action: AuditAction,
        entity_id: int,
        old_values: dict[str, Any] | None,
        new_values: dict[str, Any] | None,
        ip_address: str | None,
        user_agent: str | None,
        entity_type: str = "connection_card",
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
                    "old_values": Jsonb(old_values) if old_values is not None else None,
                    "new_values": Jsonb(new_values) if new_values is not None else None,
                },
            )

    def _list_schedule_windows(self, user_id: int) -> tuple[ScheduleWindow, ...]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT weekday, start_time, end_time, timezone, valid_from, valid_to
                FROM schedules
                WHERE user_id = %(user_id)s
                  AND is_active
                ORDER BY weekday, start_time, id
                """,
                {"user_id": user_id},
            )
            rows = cursor.fetchall()
        return tuple(
            ScheduleWindow(
                weekday=row["weekday"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                timezone=row["timezone"],
                valid_from=row["valid_from"],
                valid_to=row["valid_to"],
            )
            for row in rows
        )

    def _list_absence_intervals(
        self, user_id: int, *, planned_start_at: datetime, planned_end_at: datetime
    ) -> tuple[TimeInterval, ...]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT start_at, end_at
                FROM absences
                WHERE user_id = %(user_id)s
                  AND start_at < %(planned_end_at)s
                  AND %(planned_start_at)s < end_at
                ORDER BY start_at, id
                """,
                {
                    "user_id": user_id,
                    "planned_start_at": planned_start_at,
                    "planned_end_at": planned_end_at,
                },
            )
            rows = cursor.fetchall()
        return tuple(
            TimeInterval(start_at=row["start_at"], end_at=row["end_at"]) for row in rows
        )

    def _list_active_card_intervals(
        self,
        user_id: int,
        *,
        planned_start_at: datetime,
        planned_end_at: datetime,
        owner_field: str = "l2_engineer_id",
    ) -> tuple[TimeInterval, ...]:
        with self.connection.cursor() as cursor:
            if owner_field not in {"l1_owner_id", "l2_engineer_id"}:
                raise ValueError("invalid assignment owner field")
            cursor.execute(
                f"""
                SELECT planned_start_at,
                       planned_start_at
                         + planned_duration_minutes * interval '1 minute' AS end_at
                FROM connection_cards
                WHERE {owner_field} = %(user_id)s
                  AND status_code IN (1, 2, 3)
                  AND planned_start_at < %(planned_end_at)s
                  AND %(planned_start_at)s < (
                    planned_start_at + planned_duration_minutes * interval '1 minute'
                  )
                ORDER BY planned_start_at, id
                """,
                {
                    "user_id": user_id,
                    "planned_start_at": planned_start_at,
                    "planned_end_at": planned_end_at,
                },
            )
            rows = cursor.fetchall()
        return tuple(
            TimeInterval(start_at=row["planned_start_at"], end_at=row["end_at"])
            for row in rows
        )

    def _get_card(self, public_id: UUID, *, lock: bool) -> CardRecord | None:
        suffix = " FOR UPDATE" if lock else ""
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM connection_cards
                WHERE public_id = %(public_id)s
                {suffix}
                """,
                {"public_id": public_id},
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return _card_from_row(row)


def _card_from_row(row: dict[str, Any]) -> CardRecord:
    return CardRecord(
        id=row["id"],
        public_id=row["public_id"],
        number=row["number"],
        omnidesk_ticket_number=row["omnidesk_ticket_number"],
        client_id=row["client_id"],
        status_code=row["status_code"],
        criticality_code=row["criticality_code"],
        urgency_code=row["urgency_code"],
        planned_start_at=row["planned_start_at"],
        planned_duration_minutes=row["planned_duration_minutes"],
        client_timezone_at_creation=row["client_timezone_at_creation"],
        timezone_source_code=row["timezone_source_code"],
        actual_start_at=row["actual_start_at"],
        actual_end_at=row["actual_end_at"],
        l1_owner_id=row["l1_owner_id"],
        l2_engineer_id=row["l2_engineer_id"],
        assignment_method_code=row["assignment_method_code"],
        unsuccessful_cycle_count=row["unsuccessful_cycle_count"],
        client_contact_type_code=row["client_contact_type_code"],
        client_contact_value=row["client_contact_value"],
        description=row["description"],
        urgent_reason=row["urgent_reason"],
        out_of_hours_flag=row["out_of_hours_flag"],
        retroactive_flag=row["retroactive_flag"],
        overdue_flag=row["overdue_flag"],
        result_code=row["result_code"],
        engineer_report=row["engineer_report"],
        created_source_code=row["created_source_code"],
        created_by_id=row["created_by_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        client_informed=row["client_informed"],
    )


def _assignment_attempt_from_row(row: dict[str, Any]) -> AssignmentAttemptRecord:
    return AssignmentAttemptRecord(
        id=row["id"],
        cycle_id=row["cycle_id"],
        card_id=row["card_id"],
        l2_engineer_id=row["l2_engineer_id"],
        status_code=row["status_code"],
        responded_at=row["responded_at"],
        actor_user_id=row["actor_user_id"],
        rejection_reason=row["rejection_reason"],
    )

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import psycopg

from app.cards.constants import CardEventType, CardStatus, status_label, status_slug


@dataclass(frozen=True)
class ShareReport:
    numerator: int
    denominator: int
    value: float | None


@dataclass(frozen=True)
class CountReport:
    count: int


@dataclass(frozen=True)
class SummaryReport:
    created: int
    completed: int
    rejected_share: ShareReport
    repeat_rejected_share: ShareReport
    overdue: CountReport
    urgent: CountReport
    urgent_collisions: CountReport


@dataclass(frozen=True)
class OverdueCardItem:
    public_id: str
    number: str
    status: str
    status_label: str
    planned_start_at: datetime


@dataclass(frozen=True)
class L2LoadItem:
    user_id: int
    full_name: str
    assigned: int
    completed: int
    planned_minutes: int


def calculate_share(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


class ReportsRepository(Protocol):
    def get_summary(
        self, period_from: datetime, period_to: datetime
    ) -> SummaryReport: ...

    def get_overdue_cards(
        self,
        period_from: datetime,
        period_to: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[OverdueCardItem], int]: ...

    def get_l2_load(
        self, period_from: datetime, period_to: datetime
    ) -> list[L2LoadItem]: ...


class PostgresReportsRepository:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def get_summary(self, period_from: datetime, period_to: datetime) -> SummaryReport:
        params = {"from": period_from, "to": period_to}
        with self.connection.cursor() as cursor:
            # 1. Created cards in period
            cursor.execute(
                """
                SELECT count(DISTINCT id) AS cnt
                FROM connection_cards
                WHERE created_at >= %(from)s AND created_at < %(to)s
                """,
                params,
            )
            created_count = int(cursor.fetchone()["cnt"])

            # 2. Completed cards in period (status-change event to COMPLETED,
            # or a card created directly in COMPLETED by retroactive self-create)
            cursor.execute(
                """
                SELECT count(DISTINCT card_id) AS cnt
                FROM card_events
                WHERE created_at >= %(from)s AND created_at < %(to)s
                  AND event_type_code IN (%(status_changed_event)s, %(created_event)s)
                  AND (new_values->>'status_code')::int = %(completed_status)s
                """,
                {
                    **params,
                    "status_changed_event": int(CardEventType.STATUS_CHANGED),
                    "created_event": int(CardEventType.CREATED),
                    "completed_status": int(CardStatus.COMPLETED),
                },
            )
            completed_count = int(cursor.fetchone()["cnt"])

            # 3. Rejected cards: distinct cards moved to REJECTED in period
            cursor.execute(
                """
                SELECT count(*) AS cnt FROM (
                    SELECT DISTINCT card_id
                    FROM card_events
                    WHERE created_at >= %(from)s AND created_at < %(to)s
                      AND event_type_code = %(status_changed_event)s
                      AND (new_values->>'status_code')::int = %(rejected_status)s
                ) rejected_events
                """,
                {
                    **params,
                    "status_changed_event": int(CardEventType.STATUS_CHANGED),
                    "rejected_status": int(CardStatus.REJECTED),
                },
            )
            rejected_count = int(cursor.fetchone()["cnt"])

            # 4. Repeat rejected: of those, cards with >=2 unsuccessful cycles
            cursor.execute(
                """
                SELECT count(*) AS cnt FROM (
                    SELECT DISTINCT card_id
                    FROM card_events
                    WHERE created_at >= %(from)s AND created_at < %(to)s
                      AND event_type_code = %(status_changed_event)s
                      AND (new_values->>'status_code')::int = %(rejected_status)s
                ) r
                JOIN connection_cards c ON c.id = r.card_id
                WHERE c.unsuccessful_cycle_count >= 2
                """,
                {
                    **params,
                    "status_changed_event": int(CardEventType.STATUS_CHANGED),
                    "rejected_status": int(CardStatus.REJECTED),
                },
            )
            repeat_rejected_count = int(cursor.fetchone()["cnt"])

            # 5. Overdue cards in period
            cursor.execute(
                """
                SELECT count(DISTINCT card_id) AS cnt
                FROM card_events
                WHERE created_at >= %(from)s AND created_at < %(to)s
                  AND event_type_code = 1
                  AND (new_values->>'overdue')::boolean IS TRUE
                """,
                params,
            )
            overdue_count = int(cursor.fetchone()["cnt"])

            # 6. Urgent cards with urgent_reason created in period
            cursor.execute(
                """
                SELECT count(DISTINCT id) AS cnt
                FROM connection_cards
                WHERE created_at >= %(from)s AND created_at < %(to)s
                  AND (
                    (urgent_reason IS NOT NULL AND trim(urgent_reason) <> '')
                    OR urgency_code > 0
                  )
                """,
                params,
            )
            urgent_count = int(cursor.fetchone()["cnt"])

            # 7. Urgent collisions events in period
            cursor.execute(
                """
                SELECT count(*) AS cnt
                FROM card_events
                WHERE created_at >= %(from)s AND created_at < %(to)s
                  AND event_type_code = %(urgent_collision_event)s
                """,
                {
                    **params,
                    "urgent_collision_event": int(CardEventType.URGENT_COLLISION),
                },
            )
            urgent_collisions_count = int(cursor.fetchone()["cnt"])

        return SummaryReport(
            created=created_count,
            completed=completed_count,
            rejected_share=ShareReport(
                numerator=rejected_count,
                denominator=created_count,
                value=calculate_share(rejected_count, created_count),
            ),
            repeat_rejected_share=ShareReport(
                numerator=repeat_rejected_count,
                denominator=rejected_count,
                value=calculate_share(repeat_rejected_count, rejected_count),
            ),
            overdue=CountReport(count=overdue_count),
            urgent=CountReport(count=urgent_count),
            urgent_collisions=CountReport(count=urgent_collisions_count),
        )

    def get_overdue_cards(
        self,
        period_from: datetime,
        period_to: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[OverdueCardItem], int]:
        params = {
            "from": period_from,
            "to": period_to,
            "limit": limit,
            "offset": offset,
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(DISTINCT card_id) AS total
                FROM card_events
                WHERE created_at >= %(from)s AND created_at < %(to)s
                  AND event_type_code = 1
                  AND (new_values->>'overdue')::boolean IS TRUE
                """,
                params,
            )
            total = int(cursor.fetchone()["total"])

            cursor.execute(
                """
                WITH overdue_events AS (
                    SELECT DISTINCT card_id
                    FROM card_events
                    WHERE created_at >= %(from)s AND created_at < %(to)s
                      AND event_type_code = 1
                      AND (new_values->>'overdue')::boolean IS TRUE
                )
                SELECT
                    c.public_id,
                    c.number,
                    c.status_code,
                    c.planned_start_at
                FROM overdue_events oe
                JOIN connection_cards c ON c.id = oe.card_id
                ORDER BY c.planned_start_at ASC, c.id ASC
                LIMIT %(limit)s OFFSET %(offset)s
                """,
                params,
            )
            rows = cursor.fetchall()

        items = [
            OverdueCardItem(
                public_id=str(row["public_id"]),
                number=row["number"],
                status=status_slug(CardStatus(row["status_code"])).value,
                status_label=status_label(CardStatus(row["status_code"])),
                planned_start_at=row["planned_start_at"],
            )
            for row in rows
        ]
        return items, total

    def get_l2_load(
        self, period_from: datetime, period_to: datetime
    ) -> list[L2LoadItem]:
        params = {"from": period_from, "to": period_to}
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                WITH l2_assignments AS (
                    SELECT
                        l2_user_id,
                        count(*) AS assigned_count,
                        COALESCE(SUM(planned_duration_minutes), 0) AS planned_minutes
                    FROM (
                        SELECT DISTINCT
                            aa.l2_engineer_id AS l2_user_id,
                            aa.card_id,
                            c.planned_duration_minutes
                        FROM assignment_attempts aa
                        JOIN connection_cards c ON c.id = aa.card_id
                        WHERE aa.assigned_at >= %(from)s AND aa.assigned_at < %(to)s
                          AND aa.status_code <> 3
                    ) raw_assignments
                    GROUP BY l2_user_id
                ),
                l2_completions AS (
                    SELECT
                        COALESCE((ce.new_values->>'l2_engineer_id')::bigint, c.l2_engineer_id) AS l2_user_id,
                        count(DISTINCT ce.card_id) AS completed_count
                    FROM card_events ce
                    JOIN connection_cards c ON c.id = ce.card_id
                    WHERE ce.created_at >= %(from)s AND ce.created_at < %(to)s
                      AND ce.event_type_code IN (%(status_changed_event)s, %(created_event)s)
                      AND (ce.new_values->>'status_code')::int = %(completed_status)s
                    GROUP BY 1
                )
                SELECT
                    u.id AS user_id,
                    u.full_name,
                    COALESCE(a.assigned_count, 0) AS assigned,
                    COALESCE(c.completed_count, 0) AS completed,
                    COALESCE(a.planned_minutes, 0) AS planned_minutes
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id AND ur.role_id = 2
                LEFT JOIN l2_assignments a ON a.l2_user_id = u.id
                LEFT JOIN l2_completions c ON c.l2_user_id = u.id
                WHERE u.is_active
                ORDER BY u.full_name ASC, u.id ASC
                """,
                {
                    **params,
                    "status_changed_event": int(CardEventType.STATUS_CHANGED),
                    "created_event": int(CardEventType.CREATED),
                    "completed_status": int(CardStatus.COMPLETED),
                },
            )
            rows = cursor.fetchall()

        return [
            L2LoadItem(
                user_id=row["user_id"],
                full_name=row["full_name"],
                assigned=int(row["assigned"]),
                completed=int(row["completed"]),
                planned_minutes=int(row["planned_minutes"]),
            )
            for row in rows
        ]


class ReportsService:
    def __init__(self, repository: ReportsRepository) -> None:
        self.repository = repository

    def get_summary(self, period_from: datetime, period_to: datetime) -> SummaryReport:
        return self.repository.get_summary(period_from, period_to)

    def get_overdue_cards(
        self,
        period_from: datetime,
        period_to: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[OverdueCardItem], int]:
        return self.repository.get_overdue_cards(
            period_from, period_to, limit=limit, offset=offset
        )

    def get_l2_load(
        self, period_from: datetime, period_to: datetime
    ) -> list[L2LoadItem]:
        return self.repository.get_l2_load(period_from, period_to)

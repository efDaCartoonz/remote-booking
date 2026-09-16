from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

CaseIndexUpsertResult = Literal["upserted", "conflicts"]


@dataclass(frozen=True)
class CaseIndexItem:
    case_id: str
    case_number: str
    user_id: str | None
    status: str
    deleted: bool
    spam: bool
    created_at: datetime
    updated_at: datetime


class CaseIndexConflict(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CaseIndexValidationError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CaseIndexRepository:
    def __init__(self, connection: Any):
        self.connection = connection

    def upsert(self, item: CaseIndexItem) -> CaseIndexUpsertResult:
        validate_item(item)
        number = item.case_number.strip()
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT case_id, case_number FROM omnidesk_case_index WHERE case_id = %s OR case_number = %s FOR UPDATE",
                (item.case_id, number),
            )
            rows = cursor.fetchall()
            for row in rows:
                if row[0] == item.case_id and row[1] != number:
                    self._conflict(cursor, "case_id_number_changed", item)
                    return "conflicts"
                if row[1] == number and row[0] != item.case_id:
                    self._conflict(cursor, "duplicate_case_number", item)
                    return "conflicts"
            cursor.execute(
                """
                    INSERT INTO omnidesk_case_index
                    (case_id, case_number, omnidesk_user_id, status, deleted, spam,
                     omnidesk_created_at, omnidesk_updated_at, synced_at)
                    VALUES (%(case_id)s, %(case_number)s, %(user_id)s, %(status)s,
                            %(deleted)s, %(spam)s, %(created_at)s, %(updated_at)s, now())
                    ON CONFLICT (case_id) DO UPDATE SET
                      omnidesk_user_id = EXCLUDED.omnidesk_user_id,
                      status = EXCLUDED.status, deleted = EXCLUDED.deleted,
                      spam = EXCLUDED.spam, omnidesk_created_at = EXCLUDED.omnidesk_created_at,
                      omnidesk_updated_at = EXCLUDED.omnidesk_updated_at, synced_at = now()
                    WHERE omnidesk_case_index.omnidesk_updated_at <= EXCLUDED.omnidesk_updated_at
                """,
                {**item.__dict__, "case_number": number},
            )
        return "upserted"

    def record_error(self, code: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO omnidesk_case_index_conflicts (conflict_code)
                SELECT %s WHERE NOT EXISTS (
                    SELECT 1 FROM omnidesk_case_index_conflicts
                    WHERE conflict_code = %s AND case_id IS NULL
                      AND case_number IS NULL AND resolved_at IS NULL
                )
                """,
                (code, code),
            )

    def _conflict(self, cursor: Any, code: str, item: CaseIndexItem) -> None:
        cursor.execute(
            "UPDATE omnidesk_case_index SET conflict_code = %s WHERE case_id = %s OR case_number = %s",
            (code, item.case_id, item.case_number),
        )
        cursor.execute(
            """
            INSERT INTO omnidesk_case_index_conflicts (conflict_code, case_id, case_number)
            SELECT %s, %s, %s
            WHERE NOT EXISTS (
                SELECT 1 FROM omnidesk_case_index_conflicts
                WHERE conflict_code = %s AND case_id IS NOT DISTINCT FROM %s
                  AND case_number IS NOT DISTINCT FROM %s AND resolved_at IS NULL
            )
            """,
            (
                code,
                item.case_id,
                item.case_number,
                code,
                item.case_id,
                item.case_number,
            ),
        )

    def save_checkpoint(
        self,
        *,
        name: str,
        window_from: datetime,
        window_to: datetime,
        page: int,
        pages: int,
        total: int | None,
        error: str | None = None,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO omnidesk_case_index_sync_state
                (sync_name, window_from, window_to, page, pages_processed, total_count, last_success_at, last_error_code)
                VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s::text IS NULL THEN now() ELSE NULL END, %s)
                ON CONFLICT (sync_name) DO UPDATE SET window_from=EXCLUDED.window_from,
                  window_to=EXCLUDED.window_to, page=EXCLUDED.page,
                  pages_processed=EXCLUDED.pages_processed, total_count=EXCLUDED.total_count,
                  last_success_at=EXCLUDED.last_success_at, last_error_code=EXCLUDED.last_error_code,
                  updated_at=now()
            """,
                (name, window_from, window_to, page, pages, total, error, error),
            )

    def get_checkpoint(self, name: str) -> tuple[datetime, datetime, int] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT window_from, window_to, page FROM omnidesk_case_index_sync_state WHERE sync_name = %s",
                (name,),
            )
            row = cursor.fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return row[0], row[1], row[2]


def validate_item(item: Any) -> None:
    if not isinstance(item, CaseIndexItem):
        raise CaseIndexValidationError("invalid_case_record")
    for value, code in (
        (item.case_id, "missing_case_id"),
        (item.case_number, "missing_case_number"),
        (item.status, "missing_status"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise CaseIndexValidationError(code)
    for value, code in (
        (item.created_at, "invalid_created_at"),
        (item.updated_at, "invalid_updated_at"),
    ):
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise CaseIndexValidationError(code)

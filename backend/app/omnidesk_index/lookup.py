from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from psycopg.errors import CheckViolation, NotNullViolation, UniqueViolation

from app.omnidesk_index.repository import (
    CaseIndexConflict,
    CaseIndexRepository,
    CaseIndexValidationError,
)

logger = logging.getLogger(__name__)

MISS_TTL_SECONDS = 60.0
_recent_misses: dict[str, float] = {}


def reset_miss_cache() -> None:
    _recent_misses.clear()


def refresh_recent_cases(
    connection,
    client,
    *,
    case_number: str,
    days: int,
    page_size: int,
    max_pages: int,
) -> bool:
    """Index recently updated Omnidesk cases to find one public number.

    Omnidesk cannot filter cases by number, so a miss scans a bounded window of
    recently updated cases and stores what it sees. Returns True when the number
    was seen. A repeated miss within MISS_TTL_SECONDS is answered without
    calling Omnidesk again, so typos cannot drain the API.
    """
    now = time.monotonic()
    for key, seen_at in list(_recent_misses.items()):
        if now - seen_at >= MISS_TTL_SECONDS:
            del _recent_misses[key]
    if case_number in _recent_misses:
        return False

    since = datetime.now(UTC) - timedelta(days=days)
    repo = CaseIndexRepository(connection)
    found = False
    for page in range(1, max_pages + 1):
        payload = client.list_cases(
            page=page,
            limit=page_size,
            sort="updated_at",
            from_updated_time=since,
        )
        for index, item in enumerate(payload.items):
            connection.execute(f"SAVEPOINT lookup_item_{index}")
            try:
                repo.upsert(item)
            except (
                CaseIndexConflict,
                CaseIndexValidationError,
                CheckViolation,
                NotNullViolation,
                UniqueViolation,
            ) as exc:
                connection.execute(f"ROLLBACK TO SAVEPOINT lookup_item_{index}")
                repo.record_error(getattr(exc, "code", "constraint_conflict"))
            connection.execute(f"RELEASE SAVEPOINT lookup_item_{index}")
            if item.case_number.strip() == case_number:
                found = True
        connection.commit()
        if found or page * page_size >= payload.total_count or not payload.items:
            break
    if not found:
        _recent_misses[case_number] = now
    logger.info(
        "Omnidesk lazy case lookup finished: found=%s window_days=%d", found, days
    )
    return found

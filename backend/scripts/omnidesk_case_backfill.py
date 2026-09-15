from __future__ import annotations

import argparse
from datetime import UTC, datetime

from app.core.config import settings
from app.db import db_connection
from app.frame.omnidesk import get_omnidesk_ticket_client
from app.omnidesk_index.backfill import BackfillOptions, run_backfill


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually backfill the Omnidesk case index"
    )
    parser.add_argument(
        "--from", dest="start", default=settings.omnidesk_case_backfill_from
    )
    parser.add_argument("--to", dest="end", default=settings.omnidesk_case_backfill_to)
    parser.add_argument(
        "--window-days", type=int, default=settings.omnidesk_case_backfill_window_days
    )
    parser.add_argument(
        "--page-size", type=int, default=settings.omnidesk_case_backfill_page_size
    )
    parser.add_argument(
        "--max-pages", type=int, default=settings.omnidesk_case_backfill_max_pages
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.start or not args.end:
        raise SystemExit("backfill_date_range_required")
    options = BackfillOptions(
        start=datetime.fromisoformat(args.start).astimezone(UTC),
        end=datetime.fromisoformat(args.end).astimezone(UTC),
        window_days=args.window_days,
        page_size=args.page_size,
        max_pages=args.max_pages,
        dry_run=args.dry_run,
    )
    with db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(hashtextextended('omnidesk_case_backfill', 0))"
            )
            if not cursor.fetchone()[0]:
                raise SystemExit("backfill_already_running")
        try:
            result = run_backfill(connection, get_omnidesk_ticket_client(), options)
            print(" ".join(f"{key}={value}" for key, value in result.items()))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(hashtextextended('omnidesk_case_backfill', 0))"
                )


if __name__ == "__main__":
    main()

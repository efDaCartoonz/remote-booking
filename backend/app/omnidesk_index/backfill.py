from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.omnidesk_index.repository import CaseIndexItem, CaseIndexRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillOptions:
    start: datetime
    end: datetime
    window_days: int
    page_size: int
    max_pages: int
    dry_run: bool = False


def run_backfill(connection, client, options: BackfillOptions) -> dict[str, int]:
    if options.page_size > 100:
        raise ValueError("page_size_must_be_at_most_100")
    repo = CaseIndexRepository(connection)
    stats = {"windows": 0, "pages": 0, "records": 0, "upserted": 0, "conflicts": 0}
    current = options.start
    checkpoint = None if options.dry_run else repo.get_checkpoint("manual_backfill")
    resume_page = 1
    if checkpoint and options.start <= checkpoint[0] < options.end:
        current, checkpoint_to, resume_page = checkpoint
    while current < options.end:
        window_to = min(current + timedelta(days=options.window_days), options.end)
        page = resume_page if checkpoint and current < checkpoint_to else 1
        pages = 0
        while page <= options.max_pages:
            payload = client.list_cases(page=page, limit=options.page_size, sort="updated_at", from_time=current, to_time=window_to)
            total = payload.total_count
            pages += 1
            stats["pages"] += 1
            for item in payload.items:
                stats["records"] += 1
                if options.dry_run:
                    continue
                result = repo.upsert(item)
                stats[result] += 1
            if not options.dry_run:
                repo.save_checkpoint(name="manual_backfill", window_from=current, window_to=window_to, page=page + 1, pages=pages, total=total)
                connection.commit()
            if page * options.page_size >= total or not payload.items:
                break
            page += 1
        stats["windows"] += 1
        current = window_to
        resume_page = 1
        checkpoint = None
    logger.info("Omnidesk case index backfill completed: windows=%d pages=%d records=%d upserted=%d conflicts=%d", stats["windows"], stats["pages"], stats["records"], stats["upserted"], stats["conflicts"])
    return stats

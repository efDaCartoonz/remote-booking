from celery import Celery

from app.core.config import settings
from app.db import get_db
from app.notifications import (
    Bitrix24Adapter,
    PostgresNotificationRuntimeRepository,
    TelegramAdapter,
    deliver_pending_notifications,
)

celery_app = Celery(
    "rdm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.timezone = "Europe/Moscow"
celery_app.conf.enable_utc = True


@celery_app.task(name="app.worker.deliver_notifications", queue="notifications")
def deliver_notifications() -> int:
    with next(get_db()) as connection:
        return deliver_pending_notifications(
            PostgresNotificationRuntimeRepository(connection),
            {0: TelegramAdapter(), 1: Bitrix24Adapter()},
        )


@celery_app.task(name="app.worker.scan_reminders", queue="notifications")
def scan_reminders() -> int:
    return 0


celery_app.conf.beat_schedule = {
    "deliver-notifications": {
        "task": "app.worker.deliver_notifications",
        "schedule": 10.0,
        "options": {"queue": "notifications"},
    },
    "scan-reminders": {
        "task": "app.worker.scan_reminders",
        "schedule": settings.reminder_scan_interval_seconds,
        "options": {"queue": "notifications"},
    },
}

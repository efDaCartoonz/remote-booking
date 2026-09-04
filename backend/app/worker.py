from celery import Celery

from app.core.config import settings
from app.db import db_connection
from app.notifications import (
    Bitrix24Adapter,
    PostgresNotificationRuntimeRepository,
    PostgresNotificationService,
    TelegramAdapter,
    deliver_pending_notifications,
)
from app.reminders import PostgresReminderRepository, ReminderService

celery_app = Celery(
    "rdm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.timezone = "Europe/Moscow"
celery_app.conf.enable_utc = True


@celery_app.task(name="app.worker.deliver_notifications", queue="notifications")
def deliver_notifications() -> int:
    if not settings.notification_delivery_enabled:
        return 0
    with db_connection() as connection:
        return deliver_pending_notifications(
            PostgresNotificationRuntimeRepository(connection),
            {0: TelegramAdapter(), 1: Bitrix24Adapter()},
        )


@celery_app.task(name="app.worker.scan_reminders", queue="notifications")
def scan_reminders() -> int:
    if not settings.reminder_scanner_enabled:
        return 0
    with db_connection() as connection:
        service = ReminderService(
            PostgresReminderRepository(connection),
            PostgresNotificationService(connection),
        )
        return service.scan(
            now=__import__("datetime").datetime.now(__import__("datetime").UTC),
            batch_size=settings.reminder_batch_limit,
        )


celery_app.conf.beat_schedule = {}

if settings.notification_delivery_enabled:
    celery_app.conf.beat_schedule["deliver-notifications"] = {
        "task": "app.worker.deliver_notifications",
        "schedule": 10.0,
        "options": {"queue": "notifications"},
    }

if settings.reminder_scanner_enabled:
    celery_app.conf.beat_schedule["scan-reminders"] = {
        "task": "app.worker.scan_reminders",
        "schedule": settings.reminder_scan_interval_seconds,
        "options": {"queue": "notifications"},
    }

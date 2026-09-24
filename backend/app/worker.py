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
from app.integrations.omnidesk_outbox import (
    PostgresOmnideskOutboxRepository,
    deliver_pending_omnidesk_outbox,
)
from app.frame.omnidesk import get_omnidesk_ticket_client

celery_app = Celery(
    "rdm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.timezone = "Europe/Moscow"
celery_app.conf.enable_utc = True
celery_app.conf.task_routes = {
    "app.worker.deliver_omnidesk_outbox": {"queue": "notifications"},
    "app.worker.deliver_notifications": {"queue": "notifications"},
    "app.worker.scan_reminders": {"queue": "notifications"},
    "app.worker.extend_sessions": {"queue": "scheduler"},
}


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


@celery_app.task(name="app.worker.extend_sessions", queue="scheduler")
def extend_sessions() -> int:
    from app.cards.extension import process_due_in_progress_sessions

    with db_connection() as connection:
        return process_due_in_progress_sessions(connection)


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

celery_app.conf.beat_schedule["extend-sessions"] = {
    "task": "app.worker.extend_sessions",
    "schedule": 60.0,  # Run every minute
    "options": {"queue": "scheduler"},
}


@celery_app.task(name="app.worker.deliver_omnidesk_outbox", queue="notifications")
def deliver_omnidesk_outbox() -> int:
    with db_connection() as connection:
        return deliver_pending_omnidesk_outbox(
            PostgresOmnideskOutboxRepository(connection),
            get_omnidesk_ticket_client(),
        )


celery_app.conf.beat_schedule["deliver-omnidesk-outbox"] = {
    "task": "app.worker.deliver_omnidesk_outbox",
    "schedule": 10.0,
    "options": {"queue": "notifications"},
}

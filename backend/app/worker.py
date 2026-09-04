from celery import Celery

from app.core.config import settings
from app.db import get_db
from app.notifications import deliver_notification_intent, TelegramAdapter, Bitrix24Adapter

celery_app = Celery(
    "rdm",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.timezone = "Europe/Moscow"
celery_app.conf.enable_utc = True

@celery_app.task
def deliver_notifications() -> bool:
    with next(get_db()) as connection:
        return deliver_notification_intent(connection, {0: TelegramAdapter(), 1: Bitrix24Adapter()})

celery_app.conf.beat_schedule = {"deliver-notifications": {"task": "app.worker.deliver_notifications", "schedule": 10.0}}

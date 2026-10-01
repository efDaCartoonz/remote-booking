from __future__ import annotations

import logging
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app.admin.catalog import (
    ALLOWED_NOTIFICATION_PLACEHOLDERS,
    extract_template_placeholders,
)
from app.core.config import settings
from app.cards.constants import CARD_STATUS_LABELS, CardStatus

logger = logging.getLogger(__name__)

PENDING = 0
SENT = 1
FAILED = 2
CANCELLED = 3

NOTIFICATION_EVENT_CODES = {
    "l1_followup": 3,
    "manager_escalation": 2,
    "l2_reminder": 4,
    "l1_reminder": 5,
    "urgent_collision": 6,
    "card_ended_automatically": 7,
    "omnidesk_staff_mapping_missing": 8,
    "card_cancelled": 9,
}
NOTIFICATION_CHANNEL_CODES = {"telegram": 0, "bitrix24": 1}
NOTIFICATION_EVENT_NAMES = {v: k for k, v in NOTIFICATION_EVENT_CODES.items()}
NOTIFICATION_CHANNEL_NAMES = {v: k for k, v in NOTIFICATION_CHANNEL_CODES.items()}
SAFE_NOTIFICATION_PAYLOAD_KEYS = frozenset({"card_id", "assignment"})
RETRYABLE_BITRIX24_ERRORS = frozenset(
    {"QUERY_LIMIT_EXCEEDED", "RATE_LIMIT_EXCEEDED", "SERVICE_UNAVAILABLE"}
)

DEFAULT_NOTIFICATION_TEMPLATES: dict[str, str] = {
    "omnidesk_staff_mapping_missing": (
        "Не настроена связь исполнителя RDM с сотрудником Omnidesk. "
        "Проверьте назначение в карточке {card_number}{client_suffix}; тикет {ticket}; "
        "{timestamp}; {duration} мин. {url}"
    ),
    "card_cancelled": (
        "Карточка {card_number} отменена{client_suffix}; тикет {ticket}; "
        "{timestamp}; {duration} мин. {url}"
    ),
    "manager_escalation": (
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; "
        "{duration} мин; причина: {reason}; "
        "текущий статус: {status}; действие: {action}. {url}"
    ),
    "l1_followup": (
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; "
        "{duration} мин. {url}"
    ),
    "l2_reminder": (
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; "
        "{duration} мин. {url}"
    ),
    "l1_reminder": (
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; "
        "{duration} мин. {url}"
    ),
    "urgent_collision": (
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; "
        "{duration} мин. {url}"
    ),
    "card_ended_automatically": (
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; "
        "{duration} мин. {url}"
    ),
}


@dataclass(frozen=True)
class Notification:
    event: str
    card_id: int
    recipient_user_id: int
    channel: str
    source_event_id: int = 0
    source_event_type: int = 0
    payload: dict | None = None
    dedupe_key: str = ""


class NotificationService(Protocol):
    def notify(
        self,
        *,
        event: str,
        card_id: int,
        source_event_id: int,
        source_event_type: int,
        recipient_user_id: int,
        channel: str,
        payload: dict,
    ) -> bool: ...


class RecordingNotificationService:
    """MVP adapter: records intent, never performs external delivery."""

    def __init__(self) -> None:
        self.notifications: list[Notification] = []
        self._keys: set[str] = set()

    def notify(
        self,
        *,
        event: str,
        card_id: int,
        recipient_user_id: int,
        channel: str,
        source_event_id: int,
        source_event_type: int,
        payload: dict,
    ) -> bool:
        key = _notification_dedupe_key(
            event=event,
            source_event_id=source_event_id,
            recipient_user_id=recipient_user_id,
            channel=channel,
        )
        safe_payload = _safe_notification_payload(payload, card_id=card_id)
        if key not in self._keys:
            self._keys.add(key)
            self.notifications.append(
                Notification(
                    event,
                    card_id,
                    recipient_user_id,
                    channel,
                    source_event_id,
                    source_event_type,
                    safe_payload,
                    key,
                )
            )
            return True
        return False


class PostgresNotificationService:
    """Persists idempotent delivery intents for the runtime worker."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def notify(
        self,
        *,
        event: str,
        card_id: int,
        source_event_id: int,
        source_event_type: int,
        recipient_user_id: int,
        channel: str,
        payload: dict,
    ) -> bool:
        event_code = NOTIFICATION_EVENT_CODES[event]
        channel_code = NOTIFICATION_CHANNEL_CODES[channel]
        dedupe_key = _notification_dedupe_key(
            event=event,
            source_event_id=source_event_id,
            recipient_user_id=recipient_user_id,
            channel=channel,
        )
        safe_payload = _safe_notification_payload(payload, card_id=card_id)
        setting_column = {
            "telegram": "notify_telegram",
            "bitrix24": "notify_bitrix24",
        }[channel]
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO notifications (
                    card_id,
                    recipient_user_id,
                    channel_code,
                    event_type_code,
                    source_event_id,
                    source_event_type_code,
                    payload,
                    dedupe_key
                )
                SELECT %(card_id)s, %(recipient_user_id)s, %(channel_code)s, %(event_code)s,
                       %(source_event_id)s, %(source_event_type)s, %(payload)s, %(dedupe_key)s
                WHERE COALESCE(
                    (
                        SELECT {setting_column}
                        FROM user_settings
                        WHERE user_id = %(recipient_user_id)s
                    ),
                    true
                )
                ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL
                DO NOTHING
                """,
                {
                    "card_id": card_id,
                    "recipient_user_id": recipient_user_id,
                    "channel_code": channel_code,
                    "event_code": event_code,
                    "source_event_id": source_event_id,
                    "source_event_type": source_event_type,
                    "payload": Jsonb(safe_payload),
                    "dedupe_key": dedupe_key,
                },
            )
            return cursor.rowcount == 1


def _notification_dedupe_key(
    *,
    event: str,
    source_event_id: int,
    recipient_user_id: int,
    channel: str,
) -> str:
    if source_event_id <= 0:
        raise ValueError("source_event_id must be a persisted card event")
    if recipient_user_id <= 0:
        raise ValueError("recipient_user_id must be positive")
    if event not in NOTIFICATION_EVENT_CODES:
        raise ValueError("unsupported notification event")
    if channel not in NOTIFICATION_CHANNEL_CODES:
        raise ValueError("unsupported notification channel")
    return f"notification:{event}:{source_event_id}:{recipient_user_id}:{channel}"


def _safe_notification_payload(payload: dict, *, card_id: int) -> dict:
    if set(payload) - SAFE_NOTIFICATION_PAYLOAD_KEYS:
        raise ValueError("notification payload contains unsupported fields")
    if payload.get("card_id") != card_id:
        raise ValueError("notification payload card_id must match the card")
    assignment = payload.get("assignment")
    if assignment not in {"l1", "l2", "manager_escalation", "urgent_collision"}:
        raise ValueError("notification payload assignment is invalid")
    return {"card_id": card_id, "assignment": assignment}


class TemporaryDeliveryError(Exception):
    """A safe retryable delivery reason, never an external response body."""


class PermanentDeliveryError(Exception):
    """A safe terminal delivery reason, never an external response body."""


class ChannelAdapter(Protocol):
    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None: ...


class TelegramAdapter:
    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        if not settings.telegram_bot_token or not recipient:
            raise PermanentDeliveryError("telegram_not_configured")
        try:
            response = httpx.post(
                f"{settings.telegram_api_url.rstrip('/')}/bot{settings.telegram_bot_token}/sendMessage",
                json={"chat_id": recipient, "text": text},
                timeout=settings.notification_http_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise TemporaryDeliveryError("telegram_unavailable") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise TemporaryDeliveryError("telegram_temporary_error")
        if response.status_code >= 400:
            raise PermanentDeliveryError("telegram_rejected")


class Bitrix24Adapter:
    def send(self, *, recipient: str, text: str, idempotency_key: str) -> None:
        if (
            not settings.bitrix24_bot_webhook_url
            or not settings.bitrix24_bot_id
            or not settings.bitrix24_bot_client_id
            or not recipient
        ):
            raise PermanentDeliveryError("bitrix24_not_configured")
        try:
            response = httpx.post(
                settings.bitrix24_bot_webhook_url,
                data={
                    "BOT_ID": settings.bitrix24_bot_id,
                    "CLIENT_ID": settings.bitrix24_bot_client_id,
                    "DIALOG_ID": recipient,
                    "MESSAGE": text,
                },
                timeout=settings.notification_http_timeout_seconds,
            )
        except httpx.RequestError as exc:
            raise TemporaryDeliveryError("bitrix24_unavailable") from exc
        if response.status_code == 429 or response.status_code >= 500:
            logger.warning(
                "bitrix24 delivery deferred: status_class=%s",
                response.status_code // 100,
            )
            raise TemporaryDeliveryError("bitrix24_temporary_error")
        if response.status_code >= 400:
            logger.warning(
                "bitrix24 delivery rejected: status_class=%s",
                response.status_code // 100,
            )
            raise PermanentDeliveryError("bitrix24_rejected")
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("bitrix24 delivery rejected: response_class=invalid_json")
            raise PermanentDeliveryError("bitrix24_invalid_response") from exc
        if not isinstance(payload, dict):
            logger.warning("bitrix24 delivery rejected: response_class=invalid_shape")
            raise PermanentDeliveryError("bitrix24_invalid_response")

        error = payload.get("error")
        if error:
            if isinstance(error, str) and error in RETRYABLE_BITRIX24_ERRORS:
                logger.warning(
                    "bitrix24 delivery deferred: response_class=application_retryable"
                )
                raise TemporaryDeliveryError("bitrix24_temporary_error")
            logger.warning(
                "bitrix24 delivery rejected: response_class=application_rejected"
            )
            raise PermanentDeliveryError("bitrix24_rejected")

        result = payload.get("result")
        if isinstance(result, bool) or not isinstance(result, int) or result <= 0:
            logger.warning("bitrix24 delivery rejected: response_class=invalid_result")
            raise PermanentDeliveryError("bitrix24_invalid_response")


@dataclass(frozen=True)
class NotificationIntent:
    id: int
    card_id: int | None
    recipient_user_id: int | None
    channel_code: int
    event_type_code: int
    attempts: int
    locked_at: datetime
    recipient: str | None
    card_number: str | None
    card_public_id: str | None
    omnidesk_ticket_number: str | None
    client_display_name: str | None
    planned_start_at: datetime | None
    planned_duration_minutes: int | None
    recipient_timezone: str | None
    card_status_code: int | None = None
    source_event_comment: str | None = None


class NotificationRuntimeRepository(Protocol):
    def recover_stale_locks(self, *, now: datetime, max_attempts: int) -> int: ...

    def claim_one(
        self, *, now: datetime, max_attempts: int
    ) -> NotificationIntent | None: ...

    def mark_sent(self, intent: NotificationIntent) -> None: ...

    def mark_retry(
        self, intent: NotificationIntent, *, reason: str, next_attempt_at: datetime
    ) -> None: ...

    def mark_failed(self, intent: NotificationIntent, *, reason: str) -> None: ...

    def get_notification_template(self, code: str) -> str | None: ...


class PostgresNotificationRuntimeRepository:
    """Owns short PostgreSQL transactions; adapters run after each commit."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def get_notification_template(self, code: str) -> str | None:
        try:
            # A savepoint keeps a failed read from aborting the delivery
            # transaction that records the attempt result.
            with self.connection.transaction(), self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT body_template
                    FROM notification_templates
                    WHERE code = %s AND visible = true
                    """,
                    (code,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                if isinstance(row, dict):
                    return row.get("body_template")
                return row[0]
        except Exception as exc:
            logger.warning("failed to fetch notification template %s: %s", code, exc)
            return None

    def recover_stale_locks(self, *, now: datetime, max_attempts: int) -> int:
        stale_before = now - timedelta(seconds=settings.notification_lock_seconds)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE notifications
                SET status_code = %(failed)s,
                    locked_at = NULL,
                    error_message = 'delivery_retry_exhausted'
                WHERE status_code = %(pending)s
                  AND locked_at < %(stale_before)s
                  AND attempts >= %(max_attempts)s
                """,
                {
                    "failed": FAILED,
                    "pending": PENDING,
                    "stale_before": stale_before,
                    "max_attempts": max_attempts,
                },
            )
            exhausted = cursor.rowcount
            cursor.execute(
                """
                UPDATE notifications
                SET locked_at = NULL,
                    next_attempt_at = %(now)s,
                    error_message = 'delivery_lock_recovered'
                WHERE status_code = %(pending)s
                  AND locked_at < %(stale_before)s
                  AND attempts < %(max_attempts)s
                """,
                {
                    "pending": PENDING,
                    "now": now,
                    "stale_before": stale_before,
                    "max_attempts": max_attempts,
                },
            )
            recovered = cursor.rowcount
        self.connection.commit()
        return exhausted + recovered

    def claim_one(
        self, *, now: datetime, max_attempts: int
    ) -> NotificationIntent | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE notifications
                SET locked_at = %(now)s,
                    attempts = attempts + 1
                WHERE id = (
                    SELECT id
                    FROM notifications
                    WHERE status_code = %(pending)s
                      AND (scheduled_at IS NULL OR scheduled_at <= %(now)s)
                      AND (next_attempt_at IS NULL OR next_attempt_at <= %(now)s)
                      AND attempts < %(max_attempts)s
                      AND locked_at IS NULL
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, card_id, recipient_user_id, channel_code, event_type_code, attempts, locked_at
                """,
                {"now": now, "pending": PENDING, "max_attempts": max_attempts},
            )
            row = cursor.fetchone()
            if row is None:
                self.connection.commit()
                return None
            cursor.execute(
                """
                SELECT
                    us.telegram_chat_id,
                    us.bitrix24_user_id,
                    us.timezone,
                    c.number,
                    c.public_id::text AS public_id,
                    c.omnidesk_ticket_number,
                    clients.display_name AS client_display_name,
                    c.planned_start_at,
                    c.planned_duration_minutes,
                    c.status_code AS card_status_code,
                    source_event.comment AS source_event_comment
                FROM notifications n
                LEFT JOIN user_settings us ON us.user_id = n.recipient_user_id
                LEFT JOIN connection_cards c ON c.id = n.card_id
                LEFT JOIN clients ON clients.id = c.client_id
                LEFT JOIN card_events source_event
                  ON source_event.id = n.source_event_id
                 AND source_event.card_id = n.card_id
                WHERE n.id = %(intent_id)s
                """,
                {"intent_id": row["id"]},
            )
            details = cursor.fetchone()
        self.connection.commit()
        recipient = None
        if details is not None:
            recipient = (
                details["telegram_chat_id"]
                if row["channel_code"] == NOTIFICATION_CHANNEL_CODES["telegram"]
                else details["bitrix24_user_id"]
                if row["channel_code"] == NOTIFICATION_CHANNEL_CODES["bitrix24"]
                else None
            )
        return NotificationIntent(
            id=row["id"],
            card_id=row["card_id"],
            recipient_user_id=row["recipient_user_id"],
            channel_code=row["channel_code"],
            event_type_code=row["event_type_code"],
            attempts=row["attempts"],
            locked_at=row["locked_at"],
            recipient=recipient,
            card_number=details["number"] if details else None,
            card_public_id=details["public_id"] if details else None,
            omnidesk_ticket_number=(
                details["omnidesk_ticket_number"] if details else None
            ),
            client_display_name=(details["client_display_name"] if details else None),
            planned_start_at=details["planned_start_at"] if details else None,
            planned_duration_minutes=(
                details["planned_duration_minutes"] if details else None
            ),
            recipient_timezone=details["timezone"] if details else None,
            card_status_code=details["card_status_code"] if details else None,
            source_event_comment=(details["source_event_comment"] if details else None),
        )

    def mark_sent(self, intent: NotificationIntent) -> None:
        self._finish(intent, status=SENT, reason=None, next_attempt_at=None)

    def mark_retry(
        self, intent: NotificationIntent, *, reason: str, next_attempt_at: datetime
    ) -> None:
        self._finish(
            intent,
            status=PENDING,
            reason=reason,
            next_attempt_at=next_attempt_at,
        )

    def mark_failed(self, intent: NotificationIntent, *, reason: str) -> None:
        self._finish(intent, status=FAILED, reason=reason, next_attempt_at=None)

    def _finish(
        self,
        intent: NotificationIntent,
        *,
        status: int,
        reason: str | None,
        next_attempt_at: datetime | None,
    ) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE notifications
                SET status_code = %(status)s,
                    sent_at = CASE WHEN %(status)s = %(sent)s THEN now() ELSE sent_at END,
                    locked_at = NULL,
                    next_attempt_at = %(next_attempt_at)s,
                    error_message = %(reason)s
                WHERE id = %(intent_id)s
                  AND status_code = %(pending)s
                  AND attempts = %(attempts)s
                  AND locked_at = %(locked_at)s
                """,
                {
                    "status": status,
                    "sent": SENT,
                    "next_attempt_at": next_attempt_at,
                    "reason": reason,
                    "intent_id": intent.id,
                    "pending": PENDING,
                    "attempts": intent.attempts,
                    "locked_at": intent.locked_at,
                },
            )
            if cursor.rowcount:
                cursor.execute(
                    """
                    INSERT INTO audit_log (
                        actor_type_code,
                        action_code,
                        entity_type,
                        entity_id,
                        new_values
                    )
                    VALUES (%(actor_type)s, %(action)s, 'notification', %(intent_id)s, %(values)s)
                    """,
                    {
                        "actor_type": 2,
                        "action": 1,
                        "intent_id": intent.id,
                        "values": Jsonb(
                            {
                                "status": _status_name(status),
                                "attempts": intent.attempts,
                                "reason": reason,
                            }
                        ),
                    },
                )
        self.connection.commit()


def deliver_pending_notifications(
    repository: NotificationRuntimeRepository,
    adapters: dict[int, ChannelAdapter],
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> int:
    """Delivers a bounded batch; no database transaction spans adapter I/O."""

    current_time = now or datetime.now(UTC)
    max_attempts = settings.notification_max_attempts
    repository.recover_stale_locks(now=current_time, max_attempts=max_attempts)
    delivered = 0
    for _ in range(limit):
        intent = repository.claim_one(now=current_time, max_attempts=max_attempts)
        if intent is None:
            break
        try:
            adapter = adapters.get(intent.channel_code)
            if adapter is None:
                raise PermanentDeliveryError("unsupported_notification_channel")
            event_name = NOTIFICATION_EVENT_NAMES.get(intent.event_type_code, "unknown")
            channel_name = NOTIFICATION_CHANNEL_NAMES.get(
                intent.channel_code, "unknown"
            )
            template_code = f"{event_name}.{channel_name}"
            custom_template = None
            if hasattr(repository, "get_notification_template"):
                try:
                    custom_template = repository.get_notification_template(
                        template_code
                    )
                except Exception as exc:
                    logger.warning("failed to get template %s: %s", template_code, exc)
                    custom_template = None
            text = _render_message(intent, custom_template=custom_template)
            if not intent.recipient:
                raise PermanentDeliveryError("notification_recipient_not_configured")
            adapter.send(
                recipient=intent.recipient,
                text=text,
                idempotency_key=f"rdm-notification:{intent.id}",
            )
        except TemporaryDeliveryError as exc:
            if intent.attempts >= max_attempts:
                repository.mark_failed(intent, reason=str(exc))
            else:
                retry_at = current_time + timedelta(
                    seconds=settings.notification_retry_seconds * intent.attempts
                )
                repository.mark_retry(intent, reason=str(exc), next_attempt_at=retry_at)
        except PermanentDeliveryError as exc:
            repository.mark_failed(intent, reason=str(exc))
        else:
            repository.mark_sent(intent)
            delivered += 1
    return delivered


class SafeNotificationFormatter(string.Formatter):
    """Restricts formatting to simple keyword lookups without attribute/item traversal."""

    def get_field(
        self, field_name: str, args: tuple, kwargs: dict
    ) -> tuple[object, str]:
        if field_name not in kwargs:
            raise KeyError(field_name)
        return kwargs[field_name], field_name


_SAFE_FORMATTER = SafeNotificationFormatter()


def _render_template_string(template_str: str, context: dict[str, str]) -> str:
    if (
        not template_str
        or not isinstance(template_str, str)
        or not template_str.strip()
    ):
        raise ValueError("empty_template")
    placeholders = extract_template_placeholders(template_str)
    if not placeholders.issubset(ALLOWED_NOTIFICATION_PLACEHOLDERS):
        raise ValueError(
            f"unknown_placeholders: {placeholders - ALLOWED_NOTIFICATION_PLACEHOLDERS}"
        )
    return _SAFE_FORMATTER.vformat(template_str, (), context)


def _render_message(
    intent: NotificationIntent,
    templates: dict[str, str] | None = None,
    custom_template: str | None = None,
) -> str:
    if not settings.notification_card_base_url or not intent.card_public_id:
        raise PermanentDeliveryError("notification_card_url_not_configured")
    timestamp = "не указано"
    if intent.planned_start_at is not None:
        try:
            timezone = ZoneInfo(intent.recipient_timezone or "UTC")
        except ZoneInfoNotFoundError:
            timezone = ZoneInfo("UTC")
        timestamp = intent.planned_start_at.astimezone(timezone).strftime(
            "%d.%m.%Y %H:%M"
        )
    duration = intent.planned_duration_minutes or 0
    ticket = intent.omnidesk_ticket_number or "не указан"
    card_number = intent.card_number or f"RDM-{intent.id}"
    url = f"{settings.notification_card_base_url.rstrip('/')}/cards/{intent.card_public_id}"
    client_suffix = (
        f"; клиент {intent.client_display_name}" if intent.client_display_name else ""
    )
    event_name = NOTIFICATION_EVENT_NAMES.get(intent.event_type_code, "unknown")
    channel_name = NOTIFICATION_CHANNEL_NAMES.get(intent.channel_code, "unknown")
    template_code = f"{event_name}.{channel_name}"

    reason = ""
    action = ""
    status = ""
    if intent.event_type_code == NOTIFICATION_EVENT_CODES["manager_escalation"]:
        reason, action = _manager_escalation_context(intent.source_event_comment)
        try:
            status = CARD_STATUS_LABELS[CardStatus(intent.card_status_code)]
        except (ValueError, KeyError, TypeError):
            status = "неизвестен"

    context = {
        "card_number": card_number,
        "ticket": ticket,
        "timestamp": timestamp,
        "duration": str(duration),
        "client_suffix": client_suffix,
        "url": url,
        "status": status,
        "reason": reason,
        "action": action,
    }

    candidate: str | None = None
    if custom_template is not None:
        candidate = custom_template
    elif templates is not None:
        candidate = templates.get(template_code) or templates.get(event_name)

    if candidate is not None:
        try:
            return _render_template_string(candidate, context)
        except Exception as exc:
            logger.warning(
                "failed to render custom template for %s, falling back to default: %s",
                template_code,
                exc,
            )

    default_template = DEFAULT_NOTIFICATION_TEMPLATES.get(
        event_name, DEFAULT_NOTIFICATION_TEMPLATES["l1_followup"]
    )
    return _render_template_string(default_template, context)


def _manager_escalation_context(comment: str | None) -> tuple[str, str]:
    """Use persisted event context without exposing arbitrary event text."""
    if comment and comment.startswith("all_l2_candidates_rejected:"):
        rejection = " ".join(comment.partition(":")[2].split())[:300]
        reason = (
            f"все кандидаты L2 отказались ({rejection})"
            if rejection
            else "все кандидаты L2 отказались"
        )
        return reason, "проверьте отказ и назначьте L2 или согласуйте новое время"
    if comment == "no_available_l2_candidates":
        return "нет доступного L2", "назначьте L2 или согласуйте новое время"
    if comment == "no_available_l1_candidates":
        return "нет доступного L1", "назначьте ответственного L1"
    if comment and comment.startswith("urgent_collision"):
        return "срочная коллизия", "проверьте пересечение и распределите карточки"
    return "требуется проверка карточки", "откройте карточку и определите следующий шаг"


def _status_name(status: int) -> str:
    return {PENDING: "pending", SENT: "sent", FAILED: "failed", CANCELLED: "cancelled"}[
        status
    ]

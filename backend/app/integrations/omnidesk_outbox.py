from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import psycopg


from app.frame.omnidesk import (
    OmnideskTicketClient,
    OmnideskUnavailableError,
)
from app.omnidesk_index.resolver import (
    PublicTicketResolutionError,
    resolve_ticket_by_case_number,
)
from app.cards.constants import RoleId
from app.notifications import PostgresNotificationService

logger = logging.getLogger(__name__)

PENDING = 0
SENT = 1
FAILED = 2


@dataclass
class OutboxIntent:
    id: int
    card_id: int
    omnidesk_ticket_number: str
    action_type: str
    payload: dict[str, Any]
    attempts: int


class OmnideskOutboxRepository(Protocol):
    def fetch_pending_batch(
        self, now: datetime, batch_size: int, lock_timeout: timedelta
    ) -> list[OutboxIntent]: ...

    def mark_sent(self, intent_id: int, now: datetime) -> None: ...

    def mark_failed(
        self, intent_id: int, error_message: str, next_attempt_at: datetime | None
    ) -> None: ...

    def get_user_omnidesk_staff_id(self, user_id: int) -> int | None: ...

    def notify_admin_and_manager(self, intent_id: int, error_message: str) -> None: ...

    def resolve_ticket(
        self, client: OmnideskTicketClient, ticket_number: str
    ) -> Any: ...

    def is_card_confirmed(self, card_id: int) -> bool: ...

    def get_card_info(self, card_id: int) -> dict[str, Any] | None: ...


class PostgresOmnideskOutboxRepository:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    def fetch_pending_batch(
        self, now: datetime, batch_size: int, lock_timeout: timedelta
    ) -> list[OutboxIntent]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE omnidesk_outbox
                SET locked_at = %(now)s
                WHERE id IN (
                    SELECT id FROM omnidesk_outbox
                    WHERE status_code = %(pending)s
                      AND (next_attempt_at IS NULL OR next_attempt_at <= %(now)s)
                      AND (locked_at IS NULL OR locked_at < %(lock_expiry)s)
                    ORDER BY next_attempt_at NULLS FIRST, id ASC
                    LIMIT %(limit)s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, card_id, omnidesk_ticket_number, action_type, payload, attempts
                """,
                {
                    "now": now,
                    "pending": PENDING,
                    "lock_expiry": now - lock_timeout,
                    "limit": batch_size,
                },
            )
            return [
                OutboxIntent(
                    id=row["id"],
                    card_id=row["card_id"],
                    omnidesk_ticket_number=row["omnidesk_ticket_number"],
                    action_type=row["action_type"] or "internal_note",
                    payload=row["payload"],
                    attempts=row["attempts"],
                )
                for row in cursor.fetchall()
            ]

    def mark_sent(self, intent_id: int, now: datetime) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE omnidesk_outbox
                SET status_code = %(sent)s, sent_at = %(now)s, locked_at = NULL, error_message = NULL
                WHERE id = %(id)s
                """,
                {"sent": SENT, "now": now, "id": intent_id},
            )

    def mark_failed(
        self, intent_id: int, error_message: str, next_attempt_at: datetime | None
    ) -> None:
        status = PENDING if next_attempt_at is not None else FAILED
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE omnidesk_outbox
                SET status_code = %(status)s,
                    error_message = %(error_message)s,
                    next_attempt_at = %(next_attempt_at)s,
                    attempts = attempts + 1,
                    locked_at = NULL
                WHERE id = %(id)s
                """,
                {
                    "status": status,
                    "error_message": error_message,
                    "next_attempt_at": next_attempt_at,
                    "id": intent_id,
                },
            )

    def get_user_omnidesk_staff_id(self, user_id: int) -> int | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT omnidesk_staff_id FROM users WHERE id = %(id)s", {"id": user_id}
            )
            row = cursor.fetchone()
            if row and row["omnidesk_staff_id"] and row["omnidesk_staff_id"].isdigit():
                return int(row["omnidesk_staff_id"])
            return None

    def notify_admin_and_manager(self, intent_id: int, error_message: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT o.card_id, o.source_event_id, e.event_type_code
                FROM omnidesk_outbox o
                JOIN card_events e ON e.id = o.source_event_id
                WHERE o.id = %(intent_id)s
                """,
                {"intent_id": intent_id},
            )
            source = cursor.fetchone()
            if source is None:
                logger.error(
                    "Unable to notify about Omnidesk failure: source event missing"
                )
                return
            cursor.execute(
                """
                SELECT DISTINCT u.id, us.telegram_chat_id, us.bitrix24_user_id
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN user_settings us ON us.user_id = u.id
                WHERE u.is_active AND ur.role_id IN (%(admin)s, %(manager)s)
                ORDER BY u.id
                """,
                {"admin": int(RoleId.ADMIN), "manager": int(RoleId.MANAGER)},
            )
            recipients = cursor.fetchall()
        notification_service = PostgresNotificationService(self.connection)
        created = 0
        for recipient in recipients:
            for channel, address in (
                ("telegram", recipient["telegram_chat_id"]),
                ("bitrix24", recipient["bitrix24_user_id"]),
            ):
                if address and notification_service.notify(
                    event="omnidesk_staff_mapping_missing",
                    card_id=source["card_id"],
                    source_event_id=source["source_event_id"],
                    source_event_type=source["event_type_code"],
                    recipient_user_id=recipient["id"],
                    channel=channel,
                    payload={
                        "card_id": source["card_id"],
                        "assignment": "manager_escalation",
                    },
                ):
                    created += 1
        logger.error(
            "Permanent Omnidesk staff mapping failure for intent %s; queued %s administrator/manager notifications (%s)",
            intent_id,
            created,
            error_message,
        )

    def resolve_ticket(self, client: OmnideskTicketClient, ticket_number: str) -> Any:
        return resolve_ticket_by_case_number(self.connection, client, ticket_number)

    def get_card_info(self, card_id: int) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.status_code, c.planned_start_at,
                       COALESCE((
                           SELECT value #>> '{}' = 'true'
                           FROM system_settings
                           WHERE key = 'omnidesk_public_notification_enabled'
                       ), true) AS public_notification_enabled
                FROM connection_cards c WHERE c.id = %(id)s
                """,
                {"id": card_id},
            )
            return cursor.fetchone()


def deliver_pending_omnidesk_outbox(
    repository: OmnideskOutboxRepository,
    client: OmnideskTicketClient,
    batch_size: int = 50,
) -> int:
    now = datetime.now(UTC)
    intents = repository.fetch_pending_batch(
        now=now,
        batch_size=batch_size,
        lock_timeout=timedelta(minutes=5),
    )
    processed = 0

    for intent in intents:
        try:
            _process_intent(repository, client, intent)
            repository.mark_sent(intent.id, now)
        except Exception as exc:
            sanitized_err = (
                str(exc) if getattr(exc, "is_safe_error", False) else type(exc).__name__
            )
            logger.error(
                "Failed to process omnidesk outbox intent %d: %s",
                intent.id,
                sanitized_err,
            )
            retry = _is_retryable(exc, intent.attempts)
            next_attempt = (
                now + timedelta(minutes=2**intent.attempts) if retry else None
            )
            repository.mark_failed(intent.id, sanitized_err, next_attempt)
            if not retry and getattr(exc, "notify_admin", False):
                repository.notify_admin_and_manager(intent.id, sanitized_err)
        processed += 1

    return processed


def _process_intent(
    repository: OmnideskOutboxRepository,
    client: OmnideskTicketClient,
    intent: OutboxIntent,
) -> None:
    if intent.action_type in ("assignment", "l1_assignment", "l2_assignment"):
        user_id = intent.payload.get("user_id")
        if not user_id:
            return  # Nothing to assign
        staff_id = repository.get_user_omnidesk_staff_id(user_id)
        if not staff_id:
            exc = ValueError(f"Missing omnidesk_staff_id for user_id={user_id}")
            setattr(exc, "notify_admin", True)
            setattr(exc, "is_safe_error", True)
            raise exc

    try:
        ticket = repository.resolve_ticket(client, intent.omnidesk_ticket_number)
    except PublicTicketResolutionError as exc:
        raise exc

    case_id = ticket.case_id

    if intent.action_type in ("assignment", "l1_assignment", "l2_assignment"):
        client.assign_staff(case_id, staff_id)

    elif intent.action_type in ("internal_note", "internal_note_completion"):
        content = intent.payload.get("content")
        if not content:
            # Fallback for old completion notes which might use different payload
            content = intent.payload.get("summary", "Status updated")
        staff_id = None
        actor_id = intent.payload.get("actor_user_id")
        if actor_id:
            staff_id = repository.get_user_omnidesk_staff_id(actor_id)
        client.add_internal_note(case_id, content, staff_id)

    elif intent.action_type == "public_notification":
        card_info = repository.get_card_info(intent.card_id)
        if not card_info or not card_info.get("public_notification_enabled", True):
            raise SuppressedIntent("public_notification_disabled")
        if not card_info or card_info["status_code"] != 1:
            raise SuppressedIntent("public_notification_card_not_confirmed")

        # Verify schedule identity to revoke stale intents on reschedule
        expected_planned_start_at = intent.payload.get("planned_start_at")
        actual_planned_start_at = card_info["planned_start_at"]
        if expected_planned_start_at is not None:
            if isinstance(actual_planned_start_at, str):
                actual_planned_start_at = datetime.fromisoformat(
                    actual_planned_start_at.replace("Z", "+00:00")
                )
            if isinstance(expected_planned_start_at, str):
                expected_planned_start_at = datetime.fromisoformat(
                    expected_planned_start_at.replace("Z", "+00:00")
                )
            if actual_planned_start_at is None or actual_planned_start_at.astimezone(
                UTC
            ) != expected_planned_start_at.astimezone(UTC):
                raise SuppressedIntent("public_notification_superseded")

        content = intent.payload.get("content")
        if not content:
            raise SuppressedIntent("public_notification_content_missing")
        client.send_public_message(case_id, content, staff_id=None)


class SuppressedIntent(Exception):
    """Terminal intent suppression; this is not a delivery failure to retry."""

    is_safe_error = True


def _is_retryable(exc: Exception, attempts: int) -> bool:
    if attempts >= 10:
        return False
    if isinstance(exc, OmnideskUnavailableError):
        return True
    if isinstance(exc, PublicTicketResolutionError) and exc.status_code in {
        429,
        500,
        502,
        503,
        504,
    }:
        return True
    return False

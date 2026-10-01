from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from app.cards.constants import CardStatus, status_label, status_slug
from app.cards.repository import CardRepository
from app.cards.service import CardService
from app.core.config import settings
from app.frame.sessions import FrameSession
from app.cancellation.schemas import (
    CancellationConfirmResponse,
    CancellationLinkResponse,
    CancellationVerificationResponse,
)


class CancellationError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class CancellationConfigurationError(CancellationError):
    pass


class CancellationNotFoundError(CancellationError):
    pass


class CancellationExpiredError(CancellationError):
    pass


class CancellationThrottledError(CancellationError):
    pass


class CancellationConflictError(CancellationError):
    pass


class CancellationAccessError(CancellationError):
    pass


def derive_cancellation_token(nonce: str, secret_key: str | None = None) -> str:
    key = (secret_key or settings.app_secret_key).encode("utf-8")
    if not key or (
        secret_key is None
        and settings.app_env not in {"dev", "test"}
        and key == b"change-me"
    ):
        raise CancellationConfigurationError("cancellation_secret_not_configured")
    token_bytes = hmac.new(
        key, f"cancellation:{nonce}".encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(token_bytes).decode("ascii").rstrip("=")


def get_trusted_cancellation_base_url() -> str:
    url = settings.cancellation_public_base_url or settings.notification_card_base_url
    if not url or not url.strip():
        raise CancellationConfigurationError(
            "cancellation_public_base_url_not_configured"
        )

    # Reject whitespace or control characters anywhere in URL
    for c in url:
        if c.isspace() or ord(c) < 32 or ord(c) == 127:
            raise CancellationConfigurationError("cancellation_public_base_url_invalid")

    # Reject userinfo, query, fragment
    if "@" in url or "?" in url or "#" in url:
        raise CancellationConfigurationError("cancellation_public_base_url_invalid")

    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise CancellationConfigurationError("cancellation_public_base_url_invalid")

    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CancellationConfigurationError("cancellation_public_base_url_invalid")

    # Reject unexpected path: base URL should only have empty or "/" path
    if parsed.path not in ("", "/"):
        raise CancellationConfigurationError("cancellation_public_base_url_invalid")

    host = parsed.hostname
    if not host:
        raise CancellationConfigurationError("cancellation_public_base_url_invalid")

    host_lower = host.lower()
    is_loopback = False
    try:
        ip = ipaddress.ip_address(host)
        is_loopback = ip.is_loopback
    except ValueError:
        if host_lower in {"localhost", "testserver"} or host_lower.endswith(
            ".localhost"
        ):
            is_loopback = True
        else:
            labels = host_lower.split(".")
            for label in labels:
                if (
                    not label
                    or len(label) > 63
                    or label.startswith("-")
                    or label.endswith("-")
                ):
                    raise CancellationConfigurationError(
                        "cancellation_public_base_url_invalid"
                    )
                if not all(ch.isalnum() or ch == "-" for ch in label):
                    raise CancellationConfigurationError(
                        "cancellation_public_base_url_invalid"
                    )

    if parsed.scheme == "http" and not is_loopback:
        raise CancellationConfigurationError(
            "cancellation_public_base_url_must_be_https"
        )

    return f"{parsed.scheme}://{parsed.netloc}"


class CancellationService:
    def __init__(
        self,
        repository: CardRepository,
        card_service: CardService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.card_service = card_service or CardService(repository)
        self.clock = clock or (lambda: datetime.now(UTC))

    def request_cancellation_link(
        self,
        *,
        session: FrameSession,
        card_id: UUID,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> CancellationLinkResponse:
        # Serialize link requests for this card before checking the cooldown.
        card = self.repository.get_card_by_public_id_for_update(card_id)
        if card is None:
            raise CancellationNotFoundError("card_not_found")
        now = self.clock()

        # Enforce ticket ownership
        if card.omnidesk_ticket_number != session.omnidesk_ticket_number:
            raise CancellationAccessError("ticket_card_mismatch")

        # Fail closed on missing client or ownership mismatch
        if not card.client_id:
            raise CancellationAccessError("ticket_client_missing")

        client = self.repository.get_client_by_id(card.client_id)
        if not client or not client.omnidesk_user_id:
            raise CancellationAccessError("ticket_client_missing")

        if not session.omnidesk_user_id:
            raise CancellationAccessError("ticket_client_missing")

        if client.omnidesk_user_id != session.omnidesk_user_id:
            raise CancellationAccessError("ticket_client_mismatch")

        # Enforce allowed status: ASSIGNED, CONFIRMED, REJECTED
        status = CardStatus(card.status_code)
        if status not in {
            CardStatus.ASSIGNED,
            CardStatus.CONFIRMED,
            CardStatus.REJECTED,
        }:
            raise CancellationConflictError("card_not_cancellable_for_status")

        # Throttling to prevent spamming current ticket (60s cooldown)
        latest_token = self.repository.get_latest_active_cancellation_token(
            card.id, now=now
        )
        if latest_token is not None:
            elapsed = (now - latest_token.created_at).total_seconds()
            if elapsed < 60:
                raise CancellationThrottledError("cancellation_link_throttled")

        # Fail closed on untrusted or missing public base URL (strict validate HTTPS/domain)
        get_trusted_cancellation_base_url()

        # Non-secret nonce + server-keyed HMAC-derived token
        nonce = secrets.token_urlsafe(16)
        raw_token = derive_cancellation_token(nonce)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        expires_at = now + timedelta(minutes=5)

        token_id = self.repository.create_cancellation_token(
            nonce=nonce,
            token_hash=token_hash,
            card_id=card.id,
            omnidesk_ticket_number=session.omnidesk_ticket_number,
            omnidesk_user_id=session.omnidesk_user_id,
            expires_at=expires_at,
            created_at=now,
        )

        # Outbox payload stores token ID ONLY (no raw token, no content, no token hash at rest)
        if hasattr(self.repository, "create_omnidesk_outbox_intent"):
            self.repository.create_omnidesk_outbox_intent(
                card_id=card.id,
                source_event_id=None,
                omnidesk_ticket_number=session.omnidesk_ticket_number,
                action_type="cancellation_link_public_message",
                payload={
                    "token_id": token_id,
                },
            )

        # Token and cancellation URL are never returned to the caller/browser
        return CancellationLinkResponse(status="link_queued", expires_in_seconds=300)

    def verify_token(self, token: str) -> CancellationVerificationResponse:
        now = self.clock()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = self.repository.get_cancellation_token(token_hash)
        if record is None:
            raise CancellationNotFoundError("cancellation_token_not_found")

        if now > record.expires_at:
            return CancellationVerificationResponse(
                valid=False,
                status="expired",
                expires_at=record.expires_at,
                can_cancel=False,
            )

        if record.consumed_at is not None:
            return CancellationVerificationResponse(
                valid=False,
                status="already_consumed",
                expires_at=record.expires_at,
                can_cancel=False,
            )

        card = getattr(self.repository, "get_card_by_id", None)
        card_record = (
            card(record.card_id)
            if card is not None
            else getattr(self.repository, "get_card_by_id_for_update", lambda _: None)(
                record.card_id
            )
        )
        if card_record is None:
            return CancellationVerificationResponse(
                valid=False,
                status="card_not_found",
                expires_at=record.expires_at,
                can_cancel=False,
            )

        if card_record.omnidesk_ticket_number != record.omnidesk_ticket_number:
            return CancellationVerificationResponse(
                valid=False,
                status="ticket_mismatch",
                expires_at=record.expires_at,
                can_cancel=False,
            )

        # Ownership verification: fail closed if client_id missing or mismatched
        if not card_record.client_id or not record.omnidesk_user_id:
            return CancellationVerificationResponse(
                valid=False,
                status="ownership_mismatch",
                expires_at=record.expires_at,
                can_cancel=False,
            )

        client = self.repository.get_client_by_id(card_record.client_id)
        if (
            not client
            or not client.omnidesk_user_id
            or client.omnidesk_user_id != record.omnidesk_user_id
        ):
            return CancellationVerificationResponse(
                valid=False,
                status="ownership_mismatch",
                expires_at=record.expires_at,
                can_cancel=False,
            )

        card_status = CardStatus(card_record.status_code)
        if card_status == CardStatus.CANCELLED:
            return CancellationVerificationResponse(
                valid=True,
                status="already_cancelled",
                card_public_id=card_record.public_id,
                omnidesk_ticket_number=card_record.omnidesk_ticket_number,
                planned_start_at=card_record.planned_start_at,
                planned_duration_minutes=card_record.planned_duration_minutes,
                card_status=status_slug(card_status),
                card_status_label=status_label(card_status),
                expires_at=record.expires_at,
                can_cancel=False,
            )

        if card_status not in {
            CardStatus.ASSIGNED,
            CardStatus.CONFIRMED,
            CardStatus.REJECTED,
        }:
            return CancellationVerificationResponse(
                valid=False,
                status="card_not_cancellable_for_status",
                card_public_id=card_record.public_id,
                omnidesk_ticket_number=card_record.omnidesk_ticket_number,
                planned_start_at=card_record.planned_start_at,
                planned_duration_minutes=card_record.planned_duration_minutes,
                card_status=status_slug(card_status),
                card_status_label=status_label(card_status),
                expires_at=record.expires_at,
                can_cancel=False,
            )

        return CancellationVerificationResponse(
            valid=True,
            status="cancellable",
            card_public_id=card_record.public_id,
            omnidesk_ticket_number=card_record.omnidesk_ticket_number,
            planned_start_at=card_record.planned_start_at,
            planned_duration_minutes=card_record.planned_duration_minutes,
            card_status=status_slug(card_status),
            card_status_label=status_label(card_status),
            expires_at=record.expires_at,
            can_cancel=True,
        )

    def confirm_cancellation(
        self,
        token: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> CancellationConfirmResponse:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        # Atomic check and lock on token
        record = self.repository.get_cancellation_token_for_update(token_hash)
        if record is None:
            raise CancellationNotFoundError("cancellation_token_not_found")
        now = self.clock()

        # Expired token stays expired
        if now > record.expires_at:
            raise CancellationExpiredError("cancellation_token_expired")

        card = self.repository.get_card_by_id_for_update(record.card_id)
        if card is None:
            raise CancellationNotFoundError("card_not_found")

        # Ticket verification
        if card.omnidesk_ticket_number != record.omnidesk_ticket_number:
            raise CancellationAccessError("token_ticket_mismatch")

        # Client ownership verification (fail closed on missing values)
        if not card.client_id:
            raise CancellationAccessError("ticket_client_missing")

        client = self.repository.get_client_by_id(card.client_id)
        if not client or not client.omnidesk_user_id or not record.omnidesk_user_id:
            raise CancellationAccessError("ticket_client_missing")

        if client.omnidesk_user_id != record.omnidesk_user_id:
            raise CancellationAccessError("token_client_mismatch")

        # Retry by same consumed token: reports prior cancellation by THIS token idempotently
        if record.consumed_at is not None:
            if CardStatus(card.status_code) == CardStatus.CANCELLED:
                return CancellationConfirmResponse(
                    status="already_cancelled",
                    card_public_id=card.public_id,
                    cancelled_at=record.consumed_at,
                    idempotent=True,
                )
            raise CancellationConflictError("token_already_consumed")

        # Unconsumed token on an already cancelled card (cancelled by another actor)
        if CardStatus(card.status_code) == CardStatus.CANCELLED:
            raise CancellationConflictError("card_already_cancelled_by_other")

        status = CardStatus(card.status_code)
        if status not in {
            CardStatus.ASSIGNED,
            CardStatus.CONFIRMED,
            CardStatus.REJECTED,
        }:
            raise CancellationConflictError("card_not_cancellable_for_status")

        # Mark token consumed atomically
        consumed = self.repository.consume_cancellation_token(
            token_id=record.id,
            consumed_at=now,
            consumed_by_ip=ip_address,
            consumed_by_user_agent=user_agent,
        )
        if not consumed:
            raise CancellationConflictError("token_already_consumed")

        # Cancel card via domain CardService
        updated_card = self.card_service.cancel_card_by_client(
            card.public_id,
            comment="client_self_cancellation",
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return CancellationConfirmResponse(
            status="cancelled",
            card_public_id=updated_card.public_id,
            cancelled_at=now,
            idempotent=False,
        )

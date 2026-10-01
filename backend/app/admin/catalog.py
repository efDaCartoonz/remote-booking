from __future__ import annotations

import string
from dataclasses import dataclass

from app.core.config import settings


class CatalogError(Exception):
    """Base domain exception for administrative catalog operations."""


class ResultCodeConflictError(CatalogError):
    """Raised when attempting to create a connection result with an existing code."""


class ResultNotFoundError(CatalogError):
    """Raised when a connection result code does not exist."""


class LastActiveResultDeactivationError(CatalogError):
    """Raised when attempting to deactivate the only remaining active connection result."""


class NotificationTemplateNotFoundError(CatalogError):
    """Raised when a notification template code is not found."""


class InvalidTemplatePlaceholderError(CatalogError):
    """Raised when a template string has invalid formatting syntax or unsupported placeholders."""


ALLOWED_NOTIFICATION_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "card_number",
        "ticket",
        "timestamp",
        "duration",
        "client_suffix",
        "url",
        "status",
        "reason",
        "action",
    }
)


def extract_template_placeholders(template_str: str | None) -> set[str]:
    """Extract all placeholder variable names from a template string."""
    if not template_str:
        return set()
    formatter = string.Formatter()
    placeholders: set[str] = set()
    try:
        for _, field_name, _, _ in formatter.parse(template_str):
            if field_name is not None and field_name != "":
                placeholders.add(field_name)
    except Exception as exc:
        raise InvalidTemplatePlaceholderError(
            f"invalid_template_syntax: {exc}"
        ) from exc
    return placeholders


def validate_template_placeholders(template_str: str | None) -> None:
    """Validate that all placeholders in the template are known and supported."""
    if not template_str:
        return
    placeholders = extract_template_placeholders(template_str)
    unknown = placeholders - ALLOWED_NOTIFICATION_PLACEHOLDERS
    if unknown:
        sorted_unknown = ", ".join(sorted(unknown))
        raise InvalidTemplatePlaceholderError(
            f"unknown_template_placeholders: {sorted_unknown}"
        )


@dataclass(frozen=True)
class OmnideskIntegrationStatus:
    configured: bool
    enabled: bool
    base_url: str
    staff_email: str
    case_index_enabled: bool
    case_backfill_window_days: int
    timeout_seconds: float
    public_notification_enabled: bool
    cancellation_public_notification_enabled: bool


@dataclass(frozen=True)
class TelegramIntegrationStatus:
    configured: bool
    enabled: bool
    api_url: str
    delivery_enabled: bool
    scanner_enabled: bool
    scan_interval_seconds: int
    reminder_l1_interval_seconds: int
    reminder_l2_interval_seconds: int


@dataclass(frozen=True)
class Bitrix24IntegrationStatus:
    configured: bool
    enabled: bool
    bot_id: str
    bot_client_id: str
    delivery_enabled: bool


@dataclass(frozen=True)
class IntegrationsStatus:
    omnidesk: OmnideskIntegrationStatus
    telegram: TelegramIntegrationStatus
    bitrix24: Bitrix24IntegrationStatus


def build_integrations_status(
    *,
    public_notification_enabled: bool = True,
    cancellation_public_notification_enabled: bool = True,
) -> IntegrationsStatus:
    """Build safe, secret-free status records for external service integrations."""
    omnidesk_configured = bool(
        settings.omnidesk_api_key
        and settings.omnidesk_staff_email
        and settings.omnidesk_base_url
    )
    omnidesk_status = OmnideskIntegrationStatus(
        configured=omnidesk_configured,
        enabled=settings.omnidesk_case_index_enabled,
        base_url=settings.omnidesk_base_url,
        staff_email=settings.omnidesk_staff_email,
        case_index_enabled=settings.omnidesk_case_index_enabled,
        case_backfill_window_days=settings.omnidesk_case_backfill_window_days,
        timeout_seconds=settings.omnidesk_timeout_seconds,
        public_notification_enabled=public_notification_enabled,
        cancellation_public_notification_enabled=cancellation_public_notification_enabled,
    )

    telegram_configured = bool(settings.telegram_bot_token)
    telegram_status = TelegramIntegrationStatus(
        configured=telegram_configured,
        enabled=settings.notification_delivery_enabled,
        api_url=settings.telegram_api_url,
        delivery_enabled=settings.notification_delivery_enabled,
        scanner_enabled=settings.reminder_scanner_enabled,
        scan_interval_seconds=settings.reminder_scan_interval_seconds,
        reminder_l1_interval_seconds=settings.reminder_l1_interval_seconds,
        reminder_l2_interval_seconds=settings.reminder_l2_interval_seconds,
    )

    bitrix24_configured = bool(
        settings.bitrix24_bot_webhook_url
        and settings.bitrix24_bot_id
        and settings.bitrix24_bot_client_id
    )
    bitrix24_status = Bitrix24IntegrationStatus(
        configured=bitrix24_configured,
        enabled=settings.notification_delivery_enabled,
        bot_id=settings.bitrix24_bot_id,
        bot_client_id=settings.bitrix24_bot_client_id,
        delivery_enabled=settings.notification_delivery_enabled,
    )

    return IntegrationsStatus(
        omnidesk=omnidesk_status,
        telegram=telegram_status,
        bitrix24=bitrix24_status,
    )

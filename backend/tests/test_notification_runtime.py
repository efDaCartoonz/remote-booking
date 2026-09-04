from types import SimpleNamespace

import pytest

from app.notifications import (
    Bitrix24Adapter,
    PermanentDeliveryError,
    TelegramAdapter,
    TemporaryDeliveryError,
)


@pytest.mark.parametrize("adapter", [TelegramAdapter(), Bitrix24Adapter()])
def test_missing_channel_configuration_is_permanent(monkeypatch, adapter):
    monkeypatch.setattr(
        "app.notifications.settings",
        SimpleNamespace(telegram_bot_token="", bitrix24_webhook_url=""),
    )
    with pytest.raises(PermanentDeliveryError):
        adapter.send(recipient="", text="x", idempotency_key="intent:1")


def test_telegram_transport_failure_is_temporary(monkeypatch):
    monkeypatch.setattr("app.notifications.settings", SimpleNamespace(telegram_bot_token="token", telegram_api_url="https://telegram"))
    def fail(*args, **kwargs):
        raise __import__("httpx").RequestError("offline")
    monkeypatch.setattr("httpx.post", fail)
    with pytest.raises(TemporaryDeliveryError):
        TelegramAdapter().send(recipient="42", text="x", idempotency_key="intent:1")


def test_telegram_rejection_is_permanent_without_real_network(monkeypatch):
    monkeypatch.setattr("app.notifications.settings", SimpleNamespace(telegram_bot_token="token", telegram_api_url="https://telegram"))
    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: SimpleNamespace(status_code=400))
    with pytest.raises(PermanentDeliveryError):
        TelegramAdapter().send(recipient="42", text="x", idempotency_key="intent:1")

from types import SimpleNamespace

import pytest
from app.assignments.manager_escalation import (
    ManagerEscalationService,
    ManagerRecipient,
)
from app.cards.constants import CardEventType, CardStatus
from app.notifications import RecordingNotificationService


class Repo:
    def __init__(self, recipients):
        self.recipients = recipients
        self.audit = []

    def list_active_manager_recipients(self):
        return self.recipients

    def add_audit_log(self, **kwargs):
        self.audit.append(kwargs)


def card():
    return SimpleNamespace(id=7, status_code=int(CardStatus.REJECTED))


def run(recipients, *, event_id=101):
    repo = Repo(recipients)
    notifications = RecordingNotificationService()
    result = ManagerEscalationService(repo, notifications).escalate(
        card=card(),
        source_event_id=event_id,
        source_event_type=CardEventType.STATUS_CHANGED,
        reason="no_available_l2_candidates",
        ip_address=None,
        user_agent=None,
    )
    return repo, notifications, result


@pytest.mark.parametrize(
    ("recipients", "channels"),
    [
        ([], 0),
        ([ManagerRecipient(1)], 0),
        ([ManagerRecipient(1, telegram_chat_id="tg")], 1),
        ([ManagerRecipient(1, bitrix24_user_id="bx")], 1),
        ([ManagerRecipient(1, "tg", "bx")], 2),
        ([ManagerRecipient(1, "tg"), ManagerRecipient(2, "bx")], 2),
    ],
)
def test_manager_recipient_and_channel_selection(recipients, channels):
    repo, notifications, result = run(recipients)
    assert result.managers == len(recipients)
    assert result.channels == channels
    assert result.created_intents == channels
    assert result.deduplicated_intents == 0
    assert len(repo.audit) == 1
    assert card().status_code == int(CardStatus.REJECTED)
    assert len(notifications.notifications) == channels


def test_repeated_source_event_is_deduplicated_and_audited_once_per_call():
    repo = Repo([ManagerRecipient(1, "tg", "bx")])
    notifications = RecordingNotificationService()
    service = ManagerEscalationService(repo, notifications)
    kwargs = {
        "card": card(),
        "source_event_id": 55,
        "source_event_type": CardEventType.STATUS_CHANGED,
        "reason": "all_l2_candidates_rejected",
        "ip_address": None,
        "user_agent": None,
    }
    first = service.escalate(**kwargs)
    second = service.escalate(**kwargs)
    assert first.created_intents == 2
    assert second.created_intents == 0
    assert second.deduplicated_intents == 2
    assert len(notifications.notifications) == 2
    assert len(repo.audit) == 2


def test_no_recipients_and_no_channels_have_distinct_safe_reasons():
    empty_repo, _, _ = run([])
    no_channel_repo, _, _ = run([ManagerRecipient(1)])
    assert (
        empty_repo.audit[0]["new_values"]["reason"]
        == "manager_escalation_no_recipients"
    )
    assert (
        no_channel_repo.audit[0]["new_values"]["reason"]
        == "manager_escalation_no_deliverable_recipients"
    )


def test_unsupported_or_untrusted_event_is_rejected():
    repo = Repo([ManagerRecipient(1, "tg")])
    service = ManagerEscalationService(repo, RecordingNotificationService())
    with pytest.raises(ValueError):
        service.escalate(
            card=card(),
            source_event_id=1,
            source_event_type=CardEventType.STATUS_CHANGED,
            reason="urgent_collision",
            ip_address=None,
            user_agent=None,
        )

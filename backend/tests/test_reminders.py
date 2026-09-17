from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.cards.service import CardService
from app.reminders import DueReminder, ReminderService, _resolve_l1_mode
from app.worker import celery_app, scan_reminders
from test_cards import (
    FakeCardRepository,
    create_payload,
    seed_l1_candidate,
    seed_l2_candidate,
)


class FakeNotifications:
    def __init__(self):
        self.items = []

    def notify(self, **kwargs):
        self.items.append(kwargs)
        return True


class FakeRepository:
    def __init__(self, reminder):
        self.reminder = reminder
        self.advanced = []
        self.events = []

    def claim_due(self, **kwargs):
        return [self.reminder]

    def current(self, reminder):
        return True

    def record_timer_event(self, **kwargs):
        self.events.append(kwargs)
        return len(self.events)

    def recipients(self, **kwargs):
        return [("telegram", "test")]

    def managers(self):
        return [(2, "telegram", "manager")]

    def advance(self, **kwargs):
        self.advanced.append(kwargs)


def test_catch_up_creates_one_current_reminder_and_l2_escalates_once():
    anchor = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    reminder = DueReminder(1, 10, "l2_reminder", 20, anchor, 600, 2, 0, False)
    repo = FakeRepository(reminder)
    notifications = FakeNotifications()

    created = ReminderService(repo, notifications).scan(
        now=anchor + timedelta(minutes=35), batch_size=100
    )

    assert created == 2
    assert len(repo.events) == 1
    assert repo.events[0]["count"] == 3
    assert len(notifications.items) == 2
    assert repo.advanced[0]["next_due_at"] == anchor + timedelta(minutes=40)


def test_l1_escalation_repeats_only_after_snapshot_interval():
    anchor = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    reminder = DueReminder(
        1,
        10,
        "l1_reminder",
        20,
        anchor,
        600,
        2,
        2,
        True,
        anchor + timedelta(minutes=20),
        1800,
    )
    repo = FakeRepository(reminder)
    notifications = FakeNotifications()
    ReminderService(repo, notifications).scan(
        now=anchor + timedelta(minutes=49), batch_size=100
    )
    assert [item["event"] for item in notifications.items] == ["l1_reminder"]
    ReminderService(repo, notifications).scan(
        now=anchor + timedelta(minutes=50), batch_size=100
    )
    assert [item["event"] for item in notifications.items].count(
        "manager_escalation"
    ) == 1


def test_post_informed_l1_reminder_never_escalates_including_catch_up():
    anchor = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
    reminder = DueReminder(
        1,
        10,
        "l1_reminder",
        20,
        anchor,
        600,
        1,
        0,
        False,
        l1_mode="post_informed",
        client_informed=True,
    )
    repo = FakeRepository(reminder)
    notifications = FakeNotifications()

    ReminderService(repo, notifications).scan(
        now=anchor + timedelta(minutes=35), batch_size=100
    )

    assert [item["event"] for item in notifications.items] == ["l1_reminder"]
    assert repo.events[0]["count"] == 3


def test_legacy_informed_l1_schedule_uses_post_informed_mode():
    reminder = DueReminder(
        1,
        10,
        "l1_reminder",
        20,
        datetime(2026, 9, 4, 10, tzinfo=UTC),
        600,
        1,
        0,
        False,
        client_informed=True,
    )
    assert _resolve_l1_mode(reminder) == "post_informed"


def test_unknown_l1_mode_creates_no_partial_side_effects():
    reminder = DueReminder(
        1,
        10,
        "l1_reminder",
        20,
        datetime(2026, 9, 4, 10, tzinfo=UTC),
        600,
        1,
        0,
        False,
        l1_mode="invalid",
    )
    repo = FakeRepository(reminder)
    notifications = FakeNotifications()

    assert (
        ReminderService(repo, notifications).scan(
            now=datetime(2026, 9, 4, 10, 10, tzinfo=UTC), batch_size=100
        )
        == 0
    )
    assert not repo.events
    assert not notifications.items


def test_stale_schedule_is_closed_without_notification():
    reminder = DueReminder(
        1, 10, "l2_reminder", 20, datetime(2026, 9, 4, 10, tzinfo=UTC), 600, 2, 0, False
    )
    repo = FakeRepository(reminder)
    repo.current = lambda _: False
    notifications = FakeNotifications()
    assert (
        ReminderService(repo, notifications).scan(
            now=datetime(2026, 9, 4, 10, 10, tzinfo=UTC), batch_size=100
        )
        == 0
    )
    assert not notifications.items
    assert repo.advanced[0]["close"] is True


def test_overdue_l2_assignment_assigns_l1_once_and_escalates_manager():
    repository = FakeCardRepository()
    seed_l2_candidate(repository, 20)
    seed_l1_candidate(repository, 10)
    card = CardService(repository).create_card(
        create_payload(), actor_user_id=1, ip_address=None, user_agent=None
    )
    reminder = DueReminder(
        1,
        card.id,
        "l2_reminder",
        20,
        datetime(2020, 1, 1, tzinfo=UTC),
        600,
        2,
        0,
        False,
    )
    repository.due_reminders = [reminder]
    notifications = FakeNotifications()

    ReminderService(repository, notifications).scan(
        now=datetime.now(UTC), batch_size=1
    )
    ReminderService(repository, notifications).scan(
        now=datetime.now(UTC), batch_size=1
    )

    updated = repository.get_card_by_public_id(card.public_id)
    assert updated is not None
    assert updated.overdue_flag is True
    assert updated.l1_owner_id == 10
    assert sum(event["comment"] == "l2_overdue" for event in repository.events) == 1
    assert sum(item["event"] == "l1_followup" for item in notifications.items) == 2
    assert sum(item["event"] == "manager_escalation" for item in notifications.items) == 1


def test_batch_limit_is_clamped_to_500():
    reminder = DueReminder(
        1, 10, "l2_reminder", 20, datetime(2026, 9, 4, 10, tzinfo=UTC), 600, 2, 0, False
    )
    repo = FakeRepository(reminder)
    seen = []
    original = repo.claim_due

    def claim_due(**kwargs):
        seen.append(kwargs["limit"])
        return original(**kwargs)

    repo.claim_due = claim_due
    ReminderService(repo, FakeNotifications()).scan(
        now=datetime(2026, 9, 4, 10, 10, tzinfo=UTC), batch_size=999
    )
    assert seen == [500]


def test_one_error_does_not_stop_following_reminder():
    first = DueReminder(
        1, 10, "l2_reminder", 20, datetime(2026, 9, 4, 10, tzinfo=UTC), 600, 2, 0, False
    )
    second = DueReminder(
        2, 11, "l2_reminder", 21, datetime(2026, 9, 4, 10, tzinfo=UTC), 600, 2, 0, False
    )
    repo = FakeRepository(first)
    repo.claim_due = lambda **_: [first, second]
    repo.current = lambda item: item.id == 2
    notifications = FakeNotifications()
    ReminderService(repo, notifications).scan(
        now=datetime(2026, 9, 4, 10, 10, tzinfo=UTC), batch_size=100
    )
    assert notifications.items


def test_disabled_scanner_does_not_open_database(monkeypatch):
    monkeypatch.setattr("app.worker.settings.reminder_scanner_enabled", False)
    monkeypatch.setattr(
        "app.worker.db_connection", lambda: (_ for _ in ()).throw(AssertionError())
    )

    assert scan_reminders() == 0
    assert "scan-reminders" not in celery_app.conf.beat_schedule


def test_reminder_migration_uses_one_active_schedule_index():
    migration = (
        Path(__file__).parents[1]
        / "alembic/versions/20260904_0005_reminder_schedules.py"
    ).read_text()

    assert "ux_reminder_schedules_one_active_per_kind" in migration
    assert "WHERE closed_at IS NULL" in migration

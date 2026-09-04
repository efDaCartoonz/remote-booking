from datetime import UTC, datetime, timedelta

from app.reminders import DueReminder, ReminderService


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
    reminder = DueReminder(1, 10, "l1_reminder", 20, anchor, 600, 2, 2, True, anchor + timedelta(minutes=20), 1800)
    repo = FakeRepository(reminder)
    notifications = FakeNotifications()
    ReminderService(repo, notifications).scan(now=anchor + timedelta(minutes=49), batch_size=100)
    assert [item["event"] for item in notifications.items] == ["l1_reminder"]
    ReminderService(repo, notifications).scan(now=anchor + timedelta(minutes=50), batch_size=100)
    assert [item["event"] for item in notifications.items].count("manager_escalation") == 1


def test_stale_schedule_is_closed_without_notification():
    reminder = DueReminder(1, 10, "l2_reminder", 20, datetime(2026, 9, 4, 10, tzinfo=UTC), 600, 2, 0, False)
    repo = FakeRepository(reminder)
    repo.current = lambda _: False
    notifications = FakeNotifications()
    assert ReminderService(repo, notifications).scan(now=datetime(2026, 9, 4, 10, 10, tzinfo=UTC), batch_size=100) == 0
    assert not notifications.items
    assert repo.advanced[0]["close"] is True


def test_batch_limit_is_clamped_to_500():
    reminder = DueReminder(1, 10, "l2_reminder", 20, datetime(2026, 9, 4, 10, tzinfo=UTC), 600, 2, 0, False)
    repo = FakeRepository(reminder)
    seen = []
    original = repo.claim_due
    def claim_due(**kwargs):
        seen.append(kwargs["limit"])
        return original(**kwargs)
    repo.claim_due = claim_due
    ReminderService(repo, FakeNotifications()).scan(now=datetime(2026, 9, 4, 10, 10, tzinfo=UTC), batch_size=999)
    assert seen == [500]


def test_one_error_does_not_stop_following_reminder():
    first = DueReminder(1, 10, "l2_reminder", 20, datetime(2026, 9, 4, 10, tzinfo=UTC), 600, 2, 0, False)
    second = DueReminder(2, 11, "l2_reminder", 21, datetime(2026, 9, 4, 10, tzinfo=UTC), 600, 2, 0, False)
    repo = FakeRepository(first)
    repo.claim_due = lambda **_: [first, second]
    repo.current = lambda item: item.id == 2
    notifications = FakeNotifications()
    ReminderService(repo, notifications).scan(now=datetime(2026, 9, 4, 10, 10, tzinfo=UTC), batch_size=100)
    assert notifications.items

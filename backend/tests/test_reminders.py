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

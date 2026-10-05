"""Shared expectations for PostgreSQL notification tests."""

from datetime import UTC, datetime

import pytest

import app.assignments.l1_service as l1_service


@pytest.fixture
def role_notification_channels():
    """Return every active role holder's enabled, configured channels."""

    def collect(connection, role_ids):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT u.id,
                       NULLIF(us.telegram_chat_id, '') IS NOT NULL
                           AND COALESCE(us.notify_telegram, true) AS telegram,
                       NULLIF(us.bitrix24_user_id, '') IS NOT NULL
                           AND COALESCE(us.notify_bitrix24, true) AS bitrix24
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                LEFT JOIN user_settings us ON us.user_id = u.id
                WHERE u.is_active AND ur.role_id = ANY(%s)
                """,
                ([int(role_id) for role_id in role_ids],),
            )
            recipients = cursor.fetchall()

        channels = set()
        for recipient in recipients:
            if recipient["telegram"]:
                channels.add((recipient["id"], 0))
            if recipient["bitrix24"]:
                channels.add((recipient["id"], 1))
        return channels

    return collect


# Candidate schedules in the unit tests are weekday based, so the L1 availability
# check ("at the moment of assignment") must not depend on the day the suite runs.
DEFAULT_CLOCK = datetime(2026, 9, 7, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen_l1_assignment_clock(monkeypatch):
    monkeypatch.setattr(l1_service, "_now", lambda: DEFAULT_CLOCK)

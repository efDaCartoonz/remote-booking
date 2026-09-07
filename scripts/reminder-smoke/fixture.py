"""Create safe synthetic reminder data through the normal repository/service."""

from datetime import UTC, datetime, timedelta

from app.cards.constants import ActorType
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.db import db_connection

MARKER = "REMINDER_SMOKE_SYNTHETIC"


def create_user(connection, *, username: str, role_id: int, channel_id: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO users (username, password_hash, full_name, is_active)
            VALUES (%(username)s, 'smoke-only', %(marker)s, true)
            RETURNING id
            """,
            {"username": username, "marker": MARKER},
        )
        user_id = int(cursor.fetchone()["id"])
        cursor.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES (%(user_id)s, %(role_id)s)",
            {"user_id": user_id, "role_id": role_id},
        )
        cursor.execute(
            """
            INSERT INTO user_settings (user_id, timezone, telegram_chat_id, bitrix24_user_id)
            VALUES (%(user_id)s, 'UTC', %(telegram)s, %(bitrix)s)
            """,
            {
                "user_id": user_id,
                "telegram": channel_id,
                "bitrix": channel_id,
            },
        )
    return user_id


def main() -> None:
    with db_connection() as connection:
        l1_id = create_user(connection, username="smoke-l1", role_id=1, channel_id="1")
        l2_id = create_user(connection, username="smoke-l2", role_id=2, channel_id="2")
        create_user(connection, username="smoke-manager", role_id=3, channel_id="3")
        actor_id = create_user(
            connection, username="smoke-actor", role_id=4, channel_id="4"
        )

        repository = PostgresCardRepository(connection)
        names = ("chain", "post-informed", "overdue", "catch-up", "concurrent",
                 "savepoint", "delivery-temporary", "delivery-permanent",
                 "confirm", "reject", "reassign", "cycle", "reschedule", "terminal")
        for index, name in enumerate(names):
            card = CardService(repository).create_card(
                CardCreateRequest(
                    omnidesk_ticket_number=f"999-{index:06d}",
                    planned_start_at=datetime.now(UTC) - timedelta(minutes=2 + index * 31),
                    planned_duration_minutes=30,
                    l1_owner_id=l1_id,
                    l2_engineer_id=l2_id,
                    description=f"{MARKER}:{name}",
                ),
                actor_user_id=actor_id,
                ip_address=None,
                user_agent="reminder-smoke",
                actor_type=ActorType.INTERNAL_USER,
            )
            repository.create_reminder_schedule(
                card_id=card.id,
                kind="l2_reminder",
                owner_id=l2_id,
                anchor_at=datetime.now(UTC) - timedelta(seconds=5),
            )
        connection.commit()
    print("fixture=created marker=REMINDER_SMOKE_SYNTHETIC")


if __name__ == "__main__":
    main()

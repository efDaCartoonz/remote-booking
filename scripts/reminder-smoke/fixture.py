"""Create safe synthetic reminder data through the normal repository/service."""

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
        create_user(connection, username="smoke-actor", role_id=4, channel_id="4")

        connection.commit()
    print("fixture=users-ready marker=REMINDER_SMOKE_SYNTHETIC")


if __name__ == "__main__":
    main()

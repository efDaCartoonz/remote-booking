"""Create a safe synthetic fixture through the normal repository/service."""
from datetime import UTC, datetime, timedelta
from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.cards.constants import ActorType
from app.db import db_connection

with db_connection() as connection:
    repo = PostgresCardRepository(connection)
    with connection.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE is_active ORDER BY id LIMIT 3")
        users = [row["id"] for row in cur.fetchall()]
    if len(users) < 3: raise RuntimeError("migration seed must provide three active users")
    card = CardService(repo).create_card(CardCreateRequest(
        omnidesk_ticket_number="999-999999", planned_start_at=datetime.now(UTC) - timedelta(minutes=2),
        planned_duration_minutes=30, l1_owner_id=users[0], l2_engineer_id=users[1],
        description="REMINDER_SMOKE_SYNTHETIC", client_contact_value="smoke-only",
    ), actor_user_id=users[2], ip_address=None, user_agent="reminder-smoke", actor_type=ActorType.INTERNAL_USER)
    connection.commit(); print(f"fixture card={card.id} marker=REMINDER_SMOKE_SYNTHETIC")

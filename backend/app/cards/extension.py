from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import psycopg

from app.admin.repository import AdministrativeRepository
from app.cards.constants import ActorType, CardEventType, CardStatus
from app.cards.repository import PostgresCardRepository
from app.cards.service import CardService
from app.notifications import PostgresNotificationService

logger = logging.getLogger(__name__)


def process_due_in_progress_sessions(
    connection: psycopg.Connection, now: datetime | None = None
) -> int:
    if now is None:
        now = datetime.now(UTC)

    admin_repo = AdministrativeRepository(connection)
    interval_seconds = admin_repo.get_extension_interval()
    if interval_seconds < 60 or interval_seconds > 86_400 or interval_seconds % 60:
        raise ValueError("session_extension_interval_must_be_whole_minutes")

    card_repo = PostgresCardRepository(connection)
    card_service = CardService(
        repository=card_repo,
        notifications=PostgresNotificationService(connection),
    )
    extended_count = 0

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM connection_cards
            WHERE status_code = %s
              AND NOT (
                  extension_collision_flag
                  AND COALESCE(
                      extension_collision_details->>'reason' = 'in_progress_collision',
                      false
                  )
              )
              AND planned_start_at + planned_duration_minutes * interval '1 minute' <= %s
            ORDER BY id
            """,
            (int(CardStatus.IN_PROGRESS), now),
        )
        due_card_ids = [int(row["id"]) for row in cursor.fetchall()]

    connection.commit()

    for card_id in due_card_ids:
        try:
            # Each card is its own transaction: one failure rolls back only that card.
            card = card_repo.get_card_by_id_for_update(card_id)
            if card is None or CardStatus(card.status_code) != CardStatus.IN_PROGRESS:
                connection.commit()
                continue

            previous_end = card.planned_start_at + timedelta(
                minutes=card.planned_duration_minutes
            )
            # The candidate query is only a hint; another worker may have
            # extended this row before we acquired its lock.
            if previous_end > now:
                connection.commit()
                continue

            if card.planned_duration_minutes + (interval_seconds // 60) > 720:
                updated_card = card_service.end_pending_result(
                    public_id=card.public_id,
                    actor_user_id=None,
                    actor_type=ActorType.SYSTEM,
                    comment="завершено автоматически",
                    ip_address=None,
                    user_agent=None,
                )
                if card_service.notifications is not None:
                    # Auto-end fetches the actual latest event ID to satisfy the deduplication contract
                    with connection.cursor() as cur:
                        cur.execute(
                            "SELECT id FROM card_events WHERE card_id = %s AND event_type_code = %s ORDER BY id DESC LIMIT 1",
                            (updated_card.id, int(CardEventType.STATUS_CHANGED)),
                        )
                        row = cur.fetchone()
                        if not row or not row["id"]:
                            raise RuntimeError(
                                f"Missing STATUS_CHANGED event for card {updated_card.id}"
                            )
                        event_id = row["id"]

                    # Notify assigned L2
                    if updated_card.l2_engineer_id:
                        for channel in ("telegram", "bitrix24"):
                            card_service.notifications.notify(
                                event="card_ended_automatically",
                                card_id=updated_card.id,
                                source_event_id=event_id,
                                source_event_type=int(CardEventType.STATUS_CHANGED),
                                recipient_user_id=updated_card.l2_engineer_id,
                                channel=channel,
                                payload={
                                    "card_id": updated_card.id,
                                    "assignment": "l2",
                                },
                            )

                    # Notify managers
                    for manager in card_repo.list_active_manager_recipients():
                        for channel in ("telegram", "bitrix24"):
                            card_service.notifications.notify(
                                event="card_ended_automatically",
                                card_id=updated_card.id,
                                source_event_id=event_id,
                                source_event_type=int(CardEventType.STATUS_CHANGED),
                                recipient_user_id=manager.user_id,
                                channel=channel,
                                payload={
                                    "card_id": updated_card.id,
                                    "assignment": "manager_escalation",
                                },
                            )
                connection.commit()
                continue

            new_end = previous_end + timedelta(seconds=interval_seconds)
            conflicting_cards = card_repo.list_displaceable_active_cards_for_update(
                planned_start_at=previous_end,
                planned_end_at=new_end,
                l2_engineer_id=card.l2_engineer_id,
            )

            displaced_collisions = [
                card_service._displace_for_urgent_collision(
                    other,
                    actor_user_id=card.l2_engineer_id,
                    ip_address=None,
                    user_agent=None,
                )
                for other in conflicting_cards
            ]
            collision_card_id = (
                displaced_collisions[0].displaced.id if displaced_collisions else None
            )
            admin_repo.record_session_extension(
                card_id=card.id,
                previous_planned_end_at=previous_end,
                new_planned_end_at=new_end,
                interval_seconds=interval_seconds,
                collision_card_id=collision_card_id,
            )

            for collision in displaced_collisions:
                card_service._record_urgent_card_collision(
                    card=card,
                    collision=collision,
                    actor_user_id=card.l2_engineer_id,
                    ip_address=None,
                    user_agent=None,
                )
                card_service._notify_urgent_collision(
                    displaced=collision.displaced,
                    event_id=collision.event_id,
                    affected_l1_id=collision.affected_l1_id,
                    affected_l2_id=collision.affected_l2_id,
                    ip_address=None,
                    user_agent=None,
                )
            extended_count += 1
            connection.commit()
        except Exception:
            connection.rollback()
            logger.exception("session extension failed for card id %s", card_id)

    return extended_count

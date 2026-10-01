"""Seed default notification templates for all event and channel combinations.

Revision ID: 20261001_0017
Revises: 20260930_0016
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261001_0017"
down_revision: str | None = "20260930_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEEDED_TEMPLATES: list[tuple[str, int, str]] = [
    (
        "l1_followup.telegram",
        0,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "l1_followup.bitrix24",
        1,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "manager_escalation.telegram",
        0,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин; причина: {reason}; текущий статус: {status}; действие: {action}. {url}",
    ),
    (
        "manager_escalation.bitrix24",
        1,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин; причина: {reason}; текущий статус: {status}; действие: {action}. {url}",
    ),
    (
        "l2_reminder.telegram",
        0,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "l2_reminder.bitrix24",
        1,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "l1_reminder.telegram",
        0,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "l1_reminder.bitrix24",
        1,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "urgent_collision.telegram",
        0,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "urgent_collision.bitrix24",
        1,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "card_ended_automatically.telegram",
        0,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "card_ended_automatically.bitrix24",
        1,
        "Карточка {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "omnidesk_staff_mapping_missing.telegram",
        0,
        "Не настроена связь исполнителя RDM с сотрудником Omnidesk. Проверьте назначение в карточке {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "omnidesk_staff_mapping_missing.bitrix24",
        1,
        "Не настроена связь исполнителя RDM с сотрудником Omnidesk. Проверьте назначение в карточке {card_number}{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "card_cancelled.telegram",
        0,
        "Карточка {card_number} отменена{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
    (
        "card_cancelled.bitrix24",
        1,
        "Карточка {card_number} отменена{client_suffix}; тикет {ticket}; {timestamp}; {duration} мин. {url}",
    ),
]


def upgrade() -> None:
    for code, channel_code, body_template in SEEDED_TEMPLATES:
        op.execute(
            sa.text(
                """
                INSERT INTO notification_templates (code, channel_code, visible, subject_template, body_template)
                VALUES (:code, :channel_code, true, NULL, :body_template)
                ON CONFLICT (code) DO NOTHING
                """
            ).bindparams(
                code=code,
                channel_code=channel_code,
                body_template=body_template,
            )
        )


def downgrade() -> None:
    seeded_codes = [t[0] for t in SEEDED_TEMPLATES]
    op.execute(
        sa.text(
            """
            DELETE FROM notification_templates
            WHERE code = ANY(:codes)
            """
        ).bindparams(codes=seeded_codes)
    )

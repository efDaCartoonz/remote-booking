"""Add client cancellation tokens table and make outbox source_event_id nullable.

Revision ID: 20260929_0015
Revises: 20260925_0014
Create Date: 2026-09-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260929_0015"
down_revision: str | None = "20260925_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE client_cancellation_tokens (
            id bigserial PRIMARY KEY,
            nonce varchar(64) NOT NULL,
            token_hash varchar(64) NOT NULL UNIQUE,
            card_id bigint NOT NULL REFERENCES connection_cards(id) ON DELETE CASCADE,
            omnidesk_ticket_number varchar(20) NOT NULL,
            omnidesk_user_id varchar(100),
            action varchar(50) NOT NULL DEFAULT 'cancel',
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            consumed_at timestamptz,
            consumed_by_ip inet,
            consumed_by_user_agent text
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_client_cancellation_tokens_card "
        "ON client_cancellation_tokens (card_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_client_cancellation_tokens_ticket "
        "ON client_cancellation_tokens (omnidesk_ticket_number, created_at DESC)"
    )
    op.execute("ALTER TABLE omnidesk_outbox ALTER COLUMN source_event_id DROP NOT NULL")


def downgrade() -> None:
    # Link delivery is an FE-03 feature. Discard its queued/sent intents before
    # restoring the previous outbox constraint; they cannot be delivered after
    # the token table is removed.
    op.execute(
        "DELETE FROM omnidesk_outbox "
        "WHERE source_event_id IS NULL "
        "AND action_type = 'cancellation_link_public_message'"
    )
    op.execute("ALTER TABLE omnidesk_outbox ALTER COLUMN source_event_id SET NOT NULL")
    op.execute("DROP INDEX IF EXISTS ix_client_cancellation_tokens_ticket")
    op.execute("DROP INDEX IF EXISTS ix_client_cancellation_tokens_card")
    op.execute("DROP TABLE IF EXISTS client_cancellation_tokens")

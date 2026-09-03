"""Add the L1 client-inform marker required by rejected-card follow-up."""

from alembic import op

revision = "20260903_0002"
down_revision = "20260901_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE connection_cards ADD COLUMN client_informed boolean NOT NULL DEFAULT false"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ux_notifications_delivery_intent
        ON notifications (card_id, recipient_user_id, channel_code, event_type_code)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_notifications_delivery_intent")
    op.execute("ALTER TABLE connection_cards DROP COLUMN client_informed")

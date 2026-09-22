"""Add Omnidesk internal-note outbox table for completion intents."""

from alembic import op

revision = "20260922_0008"
down_revision = "20260917_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE omnidesk_internal_note_outbox (
            id bigserial PRIMARY KEY,
            card_id bigint NOT NULL REFERENCES connection_cards(id) ON DELETE CASCADE,
            source_event_id bigint NOT NULL UNIQUE REFERENCES card_events(id) ON DELETE CASCADE,
            omnidesk_ticket_number varchar(20) NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            status_code integer NOT NULL DEFAULT 0,
            attempts integer NOT NULL DEFAULT 0,
            locked_at timestamptz,
            next_attempt_at timestamptz,
            sent_at timestamptz,
            error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CHECK (status_code >= 0),
            CHECK (attempts >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_omnidesk_internal_note_outbox_runtime "
        "ON omnidesk_internal_note_outbox (status_code, next_attempt_at, locked_at, id)"
    )
    op.execute(
        "CREATE INDEX ix_omnidesk_internal_note_outbox_card "
        "ON omnidesk_internal_note_outbox (card_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_omnidesk_internal_note_outbox_card")
    op.execute("DROP INDEX ix_omnidesk_internal_note_outbox_runtime")
    op.execute("DROP TABLE omnidesk_internal_note_outbox")

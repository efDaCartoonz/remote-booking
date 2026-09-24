"""Rename omnidesk_internal_note_outbox to omnidesk_outbox and add action_type."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260924_0010"
down_revision = "20260923_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename table
    op.rename_table("omnidesk_internal_note_outbox", "omnidesk_outbox")

    # Rename indexes
    op.execute(
        "ALTER INDEX ix_omnidesk_internal_note_outbox_runtime RENAME TO ix_omnidesk_outbox_runtime"
    )
    op.execute(
        "ALTER INDEX ix_omnidesk_internal_note_outbox_card RENAME TO ix_omnidesk_outbox_card"
    )

    # Add action_type column, defaulting to 'internal_note' for existing records
    op.add_column(
        "omnidesk_outbox",
        sa.Column(
            "action_type",
            sa.String(length=50),
            nullable=False,
            server_default="internal_note",
        ),
    )
    op.alter_column("omnidesk_outbox", "action_type", server_default=None)


def downgrade() -> None:
    op.drop_column("omnidesk_outbox", "action_type")

    op.execute(
        "ALTER INDEX ix_omnidesk_outbox_card RENAME TO ix_omnidesk_internal_note_outbox_card"
    )
    op.execute(
        "ALTER INDEX ix_omnidesk_outbox_runtime RENAME TO ix_omnidesk_internal_note_outbox_runtime"
    )

    op.rename_table("omnidesk_outbox", "omnidesk_internal_note_outbox")

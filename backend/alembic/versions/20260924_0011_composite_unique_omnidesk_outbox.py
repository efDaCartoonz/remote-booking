"""Change unique constraint on omnidesk_outbox to include action_type."""

from alembic import op

revision = "20260924_0011"
down_revision = "20260924_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The constraint name retains the old table name because postgres doesn't rename constraints automatically on rename_table
    op.drop_constraint(
        "omnidesk_internal_note_outbox_source_event_id_key",
        "omnidesk_outbox",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_omnidesk_outbox_source_event_action",
        "omnidesk_outbox",
        ["source_event_id", "action_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_omnidesk_outbox_source_event_action", "omnidesk_outbox", type_="unique"
    )
    op.create_unique_constraint(
        "omnidesk_internal_note_outbox_source_event_id_key",
        "omnidesk_outbox",
        ["source_event_id"],
    )

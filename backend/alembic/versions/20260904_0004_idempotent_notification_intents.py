"""Store source card event and database-enforced notification dedupe key."""

from alembic import op

revision = "20260904_0004"
down_revision = "20260904_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notifications ADD COLUMN source_event_id bigint "
        "REFERENCES card_events(id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE notifications ADD COLUMN source_event_type_code integer"
    )
    op.execute("ALTER TABLE notifications ADD COLUMN dedupe_key varchar(255)")
    op.execute(
        "CREATE UNIQUE INDEX ux_notifications_dedupe_key "
        "ON notifications (dedupe_key) WHERE dedupe_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_notifications_dedupe_key")
    op.execute(
        "ALTER TABLE notifications DROP COLUMN dedupe_key, "
        "DROP COLUMN source_event_type_code, DROP COLUMN source_event_id"
    )

"""notification runtime delivery state"""
from alembic import op

revision = "20260904_0003"
down_revision = "20260903_0002"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("ALTER TABLE notifications ADD COLUMN attempts integer NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE notifications ADD COLUMN locked_at timestamptz")
    op.execute("ALTER TABLE notifications ADD COLUMN next_attempt_at timestamptz")
    op.execute("CREATE INDEX ix_notifications_runtime_claim ON notifications (status_code, next_attempt_at, locked_at, id)")

def downgrade() -> None:
    op.execute("DROP INDEX ix_notifications_runtime_claim")
    op.execute("ALTER TABLE notifications DROP COLUMN next_attempt_at, DROP COLUMN locked_at, DROP COLUMN attempts")

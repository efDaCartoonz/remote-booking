from alembic import op

revision = "20260904_0005"
down_revision = "20260904_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE reminder_schedules (
        id bigserial PRIMARY KEY,
        card_id bigint NOT NULL REFERENCES connection_cards(id) ON DELETE CASCADE,
        kind varchar(32) NOT NULL CHECK (kind IN ('l2_reminder', 'l1_reminder')),
        cycle_id bigint REFERENCES assignment_cycles(id) ON DELETE CASCADE,
        attempt_id bigint REFERENCES assignment_attempts(id) ON DELETE CASCADE,
        owner_id bigint REFERENCES users(id) ON DELETE SET NULL,
        anchor_at timestamptz NOT NULL,
        interval_seconds integer NOT NULL CHECK (interval_seconds > 0),
        escalation_after_count integer NOT NULL CHECK (escalation_after_count > 0),
        next_due_at timestamptz NOT NULL,
        last_count integer NOT NULL DEFAULT 0 CHECK (last_count >= 0),
        escalation_sent boolean NOT NULL DEFAULT false,
        last_escalated_at timestamptz,
        closed_at timestamptz,
        settings_snapshot jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (card_id, kind, cycle_id, attempt_id, owner_id)
    )
    """)
    op.execute("ALTER TABLE connection_cards ADD COLUMN overdue_at timestamptz")
    op.execute("CREATE INDEX ix_reminder_schedules_due ON reminder_schedules (next_due_at, id) WHERE closed_at IS NULL")


def downgrade() -> None:
    op.execute("DROP INDEX ix_reminder_schedules_due")
    op.execute("DROP TABLE reminder_schedules")
    op.execute("ALTER TABLE connection_cards DROP COLUMN overdue_at")

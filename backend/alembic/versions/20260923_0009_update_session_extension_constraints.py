"""Update session extension interval constraints."""

from alembic import op

revision = "20260923_0009"
down_revision = "20260922_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update system_settings check constraint
    op.execute(
        "ALTER TABLE system_settings DROP CONSTRAINT ck_session_extension_interval_seconds"
    )
    op.execute(
        """
        ALTER TABLE system_settings
        ADD CONSTRAINT ck_session_extension_interval_seconds
        CHECK (
            key <> 'session_extension_interval_seconds'
            OR (
                jsonb_typeof(value) = 'number'
                AND (value #>> '{}')::numeric BETWEEN 60 AND 86400
                AND mod((value #>> '{}')::numeric, 60) = 0
            )
        )
        """
    )

    # Update session_extensions check constraint
    op.execute(
        """
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            SELECT conname INTO constraint_name
            FROM pg_constraint
            WHERE conrelid = 'session_extensions'::regclass
            AND pg_get_constraintdef(oid) LIKE '%interval_seconds%';

            IF constraint_name IS NOT NULL THEN
                EXECUTE 'ALTER TABLE session_extensions DROP CONSTRAINT ' || constraint_name;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE session_extensions
        ADD CONSTRAINT session_extensions_interval_seconds_check
        CHECK (interval_seconds BETWEEN 60 AND 86400 AND mod(interval_seconds, 60) = 0)
        """
    )

    # Enforce L2 invariant: at most one in-progress card
    op.execute("CREATE UNIQUE INDEX ix_one_in_progress_per_l2 ON connection_cards (l2_engineer_id) WHERE status_code = 3")


def downgrade() -> None:
    # Remove L2 invariant
    op.execute("DROP INDEX ix_one_in_progress_per_l2")

    # Revert session_extensions check constraint
    op.execute(
        "ALTER TABLE session_extensions DROP CONSTRAINT session_extensions_interval_seconds_check"
    )
    op.execute(
        """
        ALTER TABLE session_extensions
        ADD CONSTRAINT session_extensions_interval_seconds_check
        CHECK (interval_seconds > 0)
        """
    )

    # Revert system_settings check constraint
    op.execute(
        "ALTER TABLE system_settings DROP CONSTRAINT ck_session_extension_interval_seconds"
    )
    op.execute(
        """
        ALTER TABLE system_settings
        ADD CONSTRAINT ck_session_extension_interval_seconds
        CHECK (
            key <> 'session_extension_interval_seconds'
            OR (
                jsonb_typeof(value) = 'number'
                AND (value #>> '{}')::integer BETWEEN 60 AND 86400
            )
        )
        """
    )

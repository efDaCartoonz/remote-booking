"""Add administrative storage contracts and session extension history."""

from alembic import op

revision = "20260917_0007"
down_revision = "20260914_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE connection_results (
            code integer PRIMARY KEY,
            name varchar(255) NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            sort_order integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (sort_order >= 0)
        )
        """
    )
    op.execute(
        """
        INSERT INTO connection_results (code, name, is_active, sort_order)
        SELECT code, description, visible, sort_order
        FROM dict_values
        WHERE sysname = 'connection_result'
        """
    )
    op.execute(
        """
        INSERT INTO connection_results (code, name, is_active, sort_order)
        SELECT DISTINCT c.result_code, 'Legacy result ' || c.result_code, false, c.result_code
        FROM connection_cards c
        LEFT JOIN connection_results r ON r.code = c.result_code
        WHERE c.result_code IS NOT NULL AND r.code IS NULL
        """
    )
    op.execute(
        "ALTER TABLE connection_cards ADD CONSTRAINT fk_connection_cards_result_code "
        "FOREIGN KEY (result_code) REFERENCES connection_results(code) ON DELETE RESTRICT"
    )
    op.execute(
        """
        CREATE TABLE session_extensions (
            id bigserial PRIMARY KEY,
            card_id bigint NOT NULL REFERENCES connection_cards(id) ON DELETE CASCADE,
            previous_planned_end_at timestamptz NOT NULL,
            new_planned_end_at timestamptz NOT NULL,
            interval_seconds integer NOT NULL,
            occurred_at timestamptz NOT NULL DEFAULT now(),
            has_collision boolean NOT NULL DEFAULT false,
            collision_card_id bigint REFERENCES connection_cards(id) ON DELETE SET NULL,
            event_id bigint UNIQUE REFERENCES card_events(id) ON DELETE SET NULL,
            CHECK (interval_seconds > 0),
            CHECK (new_planned_end_at > previous_planned_end_at),
            CHECK (NOT has_collision OR collision_card_id IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_session_extensions_card_occurred "
        "ON session_extensions (card_id, occurred_at, id)"
    )
    op.execute(
        "ALTER TABLE connection_cards ADD COLUMN actual_duration_minutes integer "
        "CHECK (actual_duration_minutes IS NULL OR actual_duration_minutes >= 0)"
    )
    op.execute(
        "ALTER TABLE connection_cards ADD COLUMN extension_count integer NOT NULL DEFAULT 0 "
        "CHECK (extension_count >= 0)"
    )
    op.execute(
        "ALTER TABLE connection_cards ADD COLUMN extension_collision_at timestamptz"
    )
    op.execute(
        "ALTER TABLE connection_cards ADD COLUMN extension_collision_flag boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE connection_cards ADD COLUMN extension_collision_details jsonb"
    )
    op.execute(
        "CREATE TABLE db02_migration_owned_settings (key varchar(100) PRIMARY KEY)"
    )
    op.execute(
        """
        WITH inserted AS (
            INSERT INTO system_settings (key, value, description)
            VALUES ('session_extension_interval_seconds', '900'::jsonb,
                    'Automatic in-progress session extension interval in seconds')
            ON CONFLICT (key) DO NOTHING
            RETURNING key
        )
        INSERT INTO db02_migration_owned_settings (key) SELECT key FROM inserted
        """
    )
    op.execute(
        "INSERT INTO dict_values (sysname, code, description, visible, sort_order) VALUES ('card_event_type', 9, 'Session extended', true, 100) ON CONFLICT (sysname, code) DO NOTHING"
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


def downgrade() -> None:
    op.execute(
        "ALTER TABLE system_settings DROP CONSTRAINT ck_session_extension_interval_seconds"
    )
    op.execute(
        "DELETE FROM system_settings s USING db02_migration_owned_settings o WHERE s.key = o.key"
    )
    op.execute("DROP TABLE db02_migration_owned_settings")
    op.execute(
        "DELETE FROM dict_values WHERE sysname = 'card_event_type' AND code = 9 AND description = 'Session extended'"
    )
    op.execute("ALTER TABLE connection_cards DROP COLUMN extension_collision_details")
    op.execute("ALTER TABLE connection_cards DROP COLUMN extension_collision_flag")
    op.execute("ALTER TABLE connection_cards DROP COLUMN extension_collision_at")
    op.execute("ALTER TABLE connection_cards DROP COLUMN extension_count")
    op.execute("ALTER TABLE connection_cards DROP COLUMN actual_duration_minutes")
    op.execute("DROP INDEX ix_session_extensions_card_occurred")
    op.execute("DROP TABLE session_extensions")
    op.execute(
        "ALTER TABLE connection_cards DROP CONSTRAINT fk_connection_cards_result_code"
    )
    op.execute("DROP TABLE connection_results")

"""Add cancellation public notification settings to system_settings."""

from alembic import op

revision = "20260925_0013"
down_revision = "20260924_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ie02_cancellation_migration_owned_settings (
            key varchar(100) PRIMARY KEY
        )
        """
    )
    op.execute(
        """
        WITH inserted AS (
            INSERT INTO system_settings (key, value, description)
            VALUES
                ('omnidesk_cancellation_public_notification_enabled', 'true'::jsonb,
                 'Enable public Omnidesk client notification upon card cancellation'),
                ('omnidesk_cancellation_public_notification_template', '"Заявка на удаленное подключение отменена."'::jsonb,
                 'Template for public Omnidesk client notification upon card cancellation')
            ON CONFLICT (key) DO NOTHING
            RETURNING key
        )
        INSERT INTO ie02_cancellation_migration_owned_settings (key)
        SELECT key FROM inserted
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM system_settings s
        USING ie02_cancellation_migration_owned_settings o
        WHERE s.key = o.key
        """
    )
    op.execute("DROP TABLE IF EXISTS ie02_cancellation_migration_owned_settings")

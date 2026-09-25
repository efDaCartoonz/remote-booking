"""Add notification dictionary entry for card cancellation."""

from alembic import op

revision = "20260925_0014"
down_revision = "20260925_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE ie02_cancellation_migration_owned_event (
            sysname varchar(100) NOT NULL,
            code integer NOT NULL,
            PRIMARY KEY (sysname, code)
        )
        """
    )
    op.execute(
        """
        WITH inserted AS (
            INSERT INTO dict_values (sysname, code, description, visible, sort_order)
            VALUES ('notification_event_type', 9, 'Карточка отменена', true, 90)
            ON CONFLICT (sysname, code) DO NOTHING
            RETURNING sysname, code
        )
        INSERT INTO ie02_cancellation_migration_owned_event (sysname, code)
        SELECT sysname, code FROM inserted
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM dict_values d
        USING ie02_cancellation_migration_owned_event o
        WHERE d.sysname = o.sysname AND d.code = o.code
        """
    )
    op.execute("DROP TABLE ie02_cancellation_migration_owned_event")

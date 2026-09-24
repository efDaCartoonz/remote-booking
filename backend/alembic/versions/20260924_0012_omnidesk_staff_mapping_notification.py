"""Add notification dictionary entry for missing Omnidesk staff mapping."""

from alembic import op

revision = "20260924_0012"
down_revision = "20260924_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO dict_values (sysname, code, description, visible, sort_order)
        VALUES ('notification_event_type', 8, 'Не настроена связь сотрудника с Omnidesk', true, 80)
        ON CONFLICT (sysname, code) DO UPDATE
        SET description = EXCLUDED.description, visible = EXCLUDED.visible,
            sort_order = EXCLUDED.sort_order
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM dict_values WHERE sysname = 'notification_event_type' AND code = 8"
    )

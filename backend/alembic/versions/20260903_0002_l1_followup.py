"""Add the L1 client-inform marker required by rejected-card follow-up."""

from alembic import op

revision = "20260903_0002"
down_revision = "20260901_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE connection_cards ADD COLUMN client_informed boolean NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE connection_cards DROP COLUMN client_informed")

"""Add client_name and client_company_name snapshot columns to connection_cards.

Revision ID: 20260930_0016
Revises: 20260929_0015
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260930_0016"
down_revision: str | None = "20260929_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "connection_cards",
        sa.Column("client_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "connection_cards",
        sa.Column("client_company_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connection_cards", "client_company_name")
    op.drop_column("connection_cards", "client_name")

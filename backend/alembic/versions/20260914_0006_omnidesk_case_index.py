"""Add the internal Omnidesk case index and manual backfill state."""

from alembic import op

revision = "20260914_0006"
down_revision = "20260904_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE omnidesk_case_index (
        id bigserial PRIMARY KEY,
        case_id varchar(64) NOT NULL,
        case_number varchar(64) NOT NULL,
        omnidesk_user_id varchar(128),
        status varchar(64) NOT NULL,
        deleted boolean NOT NULL DEFAULT false,
        spam boolean NOT NULL DEFAULT false,
        omnidesk_created_at timestamptz NOT NULL,
        omnidesk_updated_at timestamptz NOT NULL,
        synced_at timestamptz NOT NULL DEFAULT now(),
        conflict_code varchar(64),
        unavailable boolean NOT NULL DEFAULT false,
        CONSTRAINT ux_omnidesk_case_index_case_id UNIQUE (case_id),
        CONSTRAINT ux_omnidesk_case_index_case_number UNIQUE (case_number)
    )
    """)
    op.execute("CREATE INDEX ix_omnidesk_case_index_updated_at ON omnidesk_case_index (omnidesk_updated_at, id)")
    op.execute("CREATE INDEX ix_omnidesk_case_index_number_available ON omnidesk_case_index (case_number) WHERE conflict_code IS NULL AND NOT unavailable")
    op.execute("""
    CREATE TABLE omnidesk_case_index_sync_state (
        sync_name varchar(64) PRIMARY KEY,
        window_from timestamptz,
        window_to timestamptz,
        page integer NOT NULL DEFAULT 1 CHECK (page > 0),
        pages_processed integer NOT NULL DEFAULT 0 CHECK (pages_processed >= 0),
        total_count integer,
        last_success_at timestamptz,
        last_error_code varchar(64),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """)
    op.execute("""
    CREATE TABLE omnidesk_case_index_conflicts (
        id bigserial PRIMARY KEY,
        conflict_code varchar(64) NOT NULL,
        case_id varchar(64),
        case_number varchar(64),
        observed_at timestamptz NOT NULL DEFAULT now(),
        resolved_at timestamptz
    )
    """)
    op.execute("CREATE INDEX ix_omnidesk_case_index_conflicts_open ON omnidesk_case_index_conflicts (observed_at) WHERE resolved_at IS NULL")


def downgrade() -> None:
    op.execute("DROP TABLE omnidesk_case_index_conflicts")
    op.execute("DROP TABLE omnidesk_case_index_sync_state")
    op.execute("DROP INDEX ix_omnidesk_case_index_number_available")
    op.execute("DROP INDEX ix_omnidesk_case_index_updated_at")
    op.execute("DROP TABLE omnidesk_case_index")

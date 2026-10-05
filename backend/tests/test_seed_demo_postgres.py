from __future__ import annotations

import io
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.reports.service import PostgresReportsRepository  # noqa: E402
from scripts import seed_demo  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)

SECRET = "Sentinel-Demo-Pass-42"


@pytest.fixture
def database_url() -> str:
    return os.getenv("PSYCOPG_DATABASE_URL") or os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


@pytest.fixture(autouse=True)
def clean_demo(database_url: str):
    def wipe() -> None:
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            seed_demo.remove_demo(connection)
            connection.execute("DELETE FROM audit_log WHERE user_agent = 'demo-seed'")

    wipe()
    yield
    wipe()


def counts(connection) -> dict[str, int]:
    def scalar(sql: str) -> int:
        return connection.execute(sql).fetchone()["n"]

    return {
        "users": scalar("SELECT count(*) AS n FROM users WHERE username LIKE 'demo-%'"),
        "cards": scalar(
            "SELECT count(*) AS n FROM connection_cards WHERE description LIKE 'DEMO:%'"
        ),
        "shifts": scalar(
            "SELECT count(*) AS n FROM schedules s JOIN users u ON u.id = s.user_id "
            "WHERE u.username LIKE 'demo-%' AND s.valid_from = s.valid_to"
        ),
    }


def test_seed_is_idempotent_feeds_reports_and_removes_only_demo_records(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        before_other_cards = connection.execute(
            "SELECT count(*) AS n FROM connection_cards WHERE description NOT LIKE 'DEMO:%' "
            "OR description IS NULL"
        ).fetchone()["n"]

        assert (
            seed_demo.main(
                ["--apply", "--password-stdin"], stdin=io.StringIO(SECRET + "\n")
            )
            == 0
        )
        first = counts(connection)
        assert first["users"] == len(seed_demo.DEMO_USERS)
        assert first["cards"] > 20
        assert first["shifts"] > 50

        # A second run changes nothing.
        assert (
            seed_demo.main(
                ["--apply", "--password-stdin"], stdin=io.StringIO(SECRET + "\n")
            )
            == 0
        )
        assert counts(connection) == first

        # The password is stored hashed and never printed.
        output = capsys.readouterr()
        assert SECRET not in output.out + output.err
        stored = connection.execute(
            "SELECT password_hash FROM users WHERE username = 'demo-manager'"
        ).fetchone()["password_hash"]
        assert SECRET not in stored

        # Named fixtures exist for every role block and are described in the table.
        fixtures = connection.execute(
            "SELECT count(*) AS n FROM connection_cards WHERE omnidesk_ticket_number LIKE '910-%'"
        ).fetchone()["n"]
        assert fixtures == len(seed_demo.FIXTURES)
        table = seed_demo.render_fixtures(connection)
        for label in ("L1-1", "L1-7", "L2-4", "L2-5", "M-1"):
            assert f"| {label} |" in table
        inactive = connection.execute(
            "SELECT is_active FROM users WHERE username = %s",
            (seed_demo.INACTIVE_USER,),
        ).fetchone()
        assert inactive["is_active"] is False

        # Reports have something to show.
        now = datetime.now(UTC)
        summary = PostgresReportsRepository(connection).get_summary(
            now - timedelta(days=60), now + timedelta(days=1)
        )
        assert summary.created > 20
        assert summary.completed > 10
        assert summary.rejected_share.numerator > 0

        # Active demo cards never overlap for one engineer.
        overlaps = connection.execute(
            """
            SELECT count(*) AS n FROM connection_cards a JOIN connection_cards b
              ON a.l2_engineer_id = b.l2_engineer_id AND a.id < b.id
             AND a.status_code IN (1, 2, 3) AND b.status_code IN (1, 2, 3)
             AND tstzrange(a.planned_start_at, a.planned_start_at + a.planned_duration_minutes * interval '1 minute')
              && tstzrange(b.planned_start_at, b.planned_start_at + b.planned_duration_minutes * interval '1 minute')
            WHERE a.description LIKE 'DEMO:%'
            """
        ).fetchone()["n"]
        assert overlaps == 0

        assert seed_demo.main(["--remove"]) == 0
        assert counts(connection) == {"users": 0, "cards": 0, "shifts": 0}
        after_other_cards = connection.execute(
            "SELECT count(*) AS n FROM connection_cards WHERE description NOT LIKE 'DEMO:%' "
            "OR description IS NULL"
        ).fetchone()["n"]
        assert after_other_cards == before_other_cards


def test_apply_requires_a_valid_password_from_stdin() -> None:
    assert seed_demo.main(["--apply"]) == 2
    assert (
        seed_demo.main(["--apply", "--password-stdin"], stdin=io.StringIO("short\n"))
        == 2
    )

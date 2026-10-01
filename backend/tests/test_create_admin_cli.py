from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import create_admin  # noqa: E402

pg_only = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)

SECRET = "Sentinel-Bootstrap-Pass-42"


def test_validate_password_bounds() -> None:
    assert create_admin.validate_password("12345678") == "12345678"
    with pytest.raises(ValueError):
        create_admin.validate_password("1234567")
    with pytest.raises(ValueError):
        create_admin.validate_password("x" * 129)


def test_read_password_from_stdin_strips_only_line_ending() -> None:
    stdin = io.StringIO(" pass word 1 \r\nsecond line ignored\n")
    assert create_admin.read_password(from_stdin=True, stdin=stdin) == " pass word 1 "


def test_read_password_without_tty_requires_stdin_flag() -> None:
    with pytest.raises(ValueError):
        create_admin.read_password(from_stdin=False, stdin=io.StringIO("x"))


def test_main_rejects_short_password_without_touching_database(capsys) -> None:
    code = create_admin.main(
        ["--username", "boot", "--full-name", "Boot", "--password-stdin"],
        stdin=io.StringIO("short\n"),
    )
    out = capsys.readouterr()
    assert code == 2
    assert "short" not in out.out + out.err.replace("password must be", "")


def _database_url() -> str:
    return os.getenv("PSYCOPG_DATABASE_URL") or os.environ["DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


@pg_only
def test_creates_admin_once_hashes_password_and_never_prints_it(capsys) -> None:
    from app.auth.security import verify_password

    username = "boot-admin-cli-test"
    with psycopg.connect(_database_url(), row_factory=dict_row) as connection:
        connection.execute("DELETE FROM users WHERE username = %s", (username,))
        connection.commit()
    try:
        args = ["--username", username, "--full-name", "Boot Admin", "--password-stdin"]
        assert create_admin.main(args, stdin=io.StringIO(SECRET + "\n")) == 0
        out = capsys.readouterr()
        assert SECRET not in out.out + out.err
        assert username in out.out

        with psycopg.connect(_database_url(), row_factory=dict_row) as connection:
            user = connection.execute(
                "SELECT id, password_hash, is_active FROM users WHERE username = %s",
                (username,),
            ).fetchone()
            roles = [
                row["role_id"]
                for row in connection.execute(
                    "SELECT role_id FROM user_roles WHERE user_id = %s", (user["id"],)
                ).fetchall()
            ]
            audit = connection.execute(
                "SELECT actor_user_id, new_values FROM audit_log "
                "WHERE entity_type = 'user' AND entity_id = %s",
                (user["id"],),
            ).fetchone()
        assert user["is_active"] is True
        assert roles == [4]
        assert user["password_hash"] != SECRET
        assert verify_password(SECRET, user["password_hash"])
        assert audit is not None and audit["actor_user_id"] is None
        assert SECRET not in str(audit["new_values"])

        # A second run must refuse and leave the stored hash unchanged.
        assert create_admin.main(args, stdin=io.StringIO("Another-Password-1\n")) == 1
        out = capsys.readouterr()
        assert "already exists" in out.err
        with psycopg.connect(_database_url(), row_factory=dict_row) as connection:
            again = connection.execute(
                "SELECT password_hash FROM users WHERE username = %s", (username,)
            ).fetchone()
        assert again["password_hash"] == user["password_hash"]
    finally:
        with psycopg.connect(_database_url(), row_factory=dict_row) as connection:
            connection.execute(
                "DELETE FROM audit_log WHERE entity_type = 'user' AND entity_id IN "
                "(SELECT id FROM users WHERE username = %s)",
                (username,),
            )
            connection.execute("DELETE FROM users WHERE username = %s", (username,))
            connection.commit()

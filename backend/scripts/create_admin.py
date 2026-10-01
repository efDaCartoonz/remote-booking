"""Create the first administrator on an empty database (test stand bootstrap).

The admin API needs an existing administrator, so a fresh stand has no way in.
The password is read from stdin (or prompted on a TTY), never from argv or the
environment, and is never printed. An existing username is never overwritten.

Usage (inside the backend container):
    printf '%s' "$PASSWORD" | python scripts/create_admin.py \
        --username admin --full-name "Администратор" --password-stdin
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from typing import TextIO

from app.admin.users import (
    InvalidRoleError,
    PostgresAdminUserRepository,
    UsernameConflictError,
)
from app.cards.constants import RoleId
from app.db import db_connection

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


def validate_password(password: str) -> str:
    if not (MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH):
        raise ValueError(
            f"password must be {MIN_PASSWORD_LENGTH}..{MAX_PASSWORD_LENGTH} characters"
        )
    return password


def read_password(*, from_stdin: bool, stdin: TextIO) -> str:
    if from_stdin:
        # One line, trailing newline removed; the password itself is untouched.
        return validate_password(stdin.readline().rstrip("\r\n"))
    if not stdin.isatty():
        raise ValueError("no TTY: pass --password-stdin and pipe the password")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat password: "):
        raise ValueError("passwords do not match")
    return validate_password(first)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--username", required=True)
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--email", default=None)
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from the first line of stdin",
    )
    return parser


def main(argv: Sequence[str] | None = None, stdin: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    stdin = stdin or sys.stdin
    username = args.username.strip()
    full_name = args.full_name.strip()
    if not username or len(username) > 100 or not full_name or len(full_name) > 255:
        print(
            "ERROR: username (1..100) and full name (1..255) are required",
            file=sys.stderr,
        )
        return 2
    try:
        password = read_password(from_stdin=args.password_stdin, stdin=stdin)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        with db_connection() as connection:
            user = PostgresAdminUserRepository(connection).create_user(
                username=username,
                password=password,
                full_name=full_name,
                email=(args.email or None),
                phone=None,
                omnidesk_staff_id=None,
                roles=[int(RoleId.ADMIN)],
                is_active=True,
                actor_user_id=None,
                ip_address=None,
                user_agent="stage-bootstrap-cli",
            )
    except UsernameConflictError:
        print(
            f"ERROR: user '{username}' already exists; nothing changed", file=sys.stderr
        )
        return 1
    except InvalidRoleError:
        print(
            "ERROR: administrator role is missing; apply migrations first",
            file=sys.stderr,
        )
        return 1

    print(f"Created administrator '{user.username}' (id {user.id}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

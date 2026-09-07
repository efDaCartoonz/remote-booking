"""Bounded assertions for scheduler -> worker -> intent -> delivery."""

import argparse
import json
import time
import urllib.request

from app.db import db_connection
from psycopg import OperationalError


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    last_reason: str | None = None
    while time.monotonic() < deadline:
        try:
            with db_connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT count(*) AS n FROM reminder_schedules")
                if cursor.fetchone()["n"]:
                    break
        except OperationalError as exc:
            last_reason = type(exc).__name__
        time.sleep(1)
    else:
        detail = f"; last temporary error={last_reason}" if last_reason else ""
        raise TimeoutError(f"reminder schedule was not created{detail}")

    with urllib.request.urlopen("http://stub:8080/stats", timeout=3) as response:
        snapshot = json.load(response)
    if set(snapshot) != {"telegram", "bitrix"}:
        raise AssertionError("unsafe stub stats")
    with db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version")
        if cursor.fetchone()["version_num"] != "20260904_0005":
            raise AssertionError("unexpected alembic revision")
    print(json.dumps({"db": "ok", "stub": snapshot}, sort_keys=True))


if __name__ == "__main__":
    main()

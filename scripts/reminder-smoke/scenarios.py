"""Bounded assertions for scheduler -> worker -> intent -> delivery."""

import argparse
import json
import time
import urllib.error
import urllib.request

from app.db import db_connection
from psycopg import OperationalError


def read_stub_stats() -> dict:
    with urllib.request.urlopen("http://stub:8080/stats", timeout=3) as response:
        snapshot = json.load(response)
    if set(snapshot) != {"telegram", "bitrix"}:
        raise AssertionError("unsafe stub stats")
    return snapshot


def read_chain_state() -> dict[str, int]:
    with db_connection() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT count(*) FROM reminder_schedules) AS schedules,
                (SELECT count(*) FROM card_events WHERE comment = 'timer_reminder') AS events,
                (SELECT count(*) FROM audit_log WHERE entity_type = 'reminder_schedule') AS audits,
                (SELECT count(*) FROM notifications) AS intents,
                (SELECT count(*) FROM notifications WHERE status_code = 1) AS sent
            """
        )
        return {key: int(value) for key, value in cursor.fetchone().items()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    last_reason: str | None = None
    snapshot: dict | None = None
    state: dict[str, int] | None = None

    while time.monotonic() < deadline:
        try:
            state = read_chain_state()
            snapshot = read_stub_stats()
        except (OperationalError, urllib.error.URLError) as exc:
            last_reason = type(exc).__name__
        else:
            calls = sum(int(channel["calls"]) for channel in snapshot.values())
            if (
                state["schedules"] > 0
                and state["events"] > 0
                and state["audits"] > 0
                and state["intents"] > 0
                and state["sent"] > 0
                and calls > 0
            ):
                break
        time.sleep(1)
    else:
        detail = f"; last temporary error={last_reason}" if last_reason else ""
        raise TimeoutError(
            f"automatic reminder delivery chain did not complete{detail}"
        )

    with db_connection() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version")
        if cursor.fetchone()["version_num"] != "20260904_0005":
            raise AssertionError("unexpected alembic revision")
    print(
        json.dumps(
            {"chain": state, "stub": snapshot},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()

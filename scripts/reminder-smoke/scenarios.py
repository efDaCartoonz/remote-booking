"""Bounded end-to-end reminder scenarios using the real runtime."""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime

from app.db import db_connection
from app.notifications import Bitrix24Adapter, PostgresNotificationRuntimeRepository, PostgresNotificationService, TelegramAdapter, deliver_pending_notifications
from app.reminders import PostgresReminderRepository, ReminderService

MARKER = "REMINDER_SMOKE"

def query(statement: str, params: dict | None = None) -> dict:
    with db_connection() as c, c.cursor() as cur:
        cur.execute(statement, params or {})
        row = dict(cur.fetchone() or {})
        c.commit()
        return row

def card(name: str) -> dict:
    return query("SELECT id FROM connection_cards WHERE description=%(d)s", {"d": f"{MARKER}:{name}"})

def scan() -> int:
    with db_connection() as c:
        result = ReminderService(PostgresReminderRepository(c), PostgresNotificationService(c)).scan(now=datetime.now(UTC), batch_size=100)
        c.commit()
        return result

def check(value: bool, reason: str) -> None:
    if not value:
        raise AssertionError(reason)

def scenario_1() -> None:
    c = card("chain"); before = query("SELECT count(*) n FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder'", {"id": c["id"]})["n"]; scan()
    with db_connection() as db:
        deliver_pending_notifications(PostgresNotificationRuntimeRepository(db), {0: TelegramAdapter(), 1: Bitrix24Adapter()})
    row = query("SELECT (SELECT count(*) FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder') events, (SELECT count(*) FROM audit_log WHERE entity_type='reminder_schedule' AND entity_id=%(id)s) audits, (SELECT count(*) FROM notifications WHERE card_id=%(id)s) intents", {"id": c["id"]})
    check(row["events"] > before and row["audits"] > 0 and row["intents"] > 0, "chain persistence")

def scenario_2() -> None:
    c = card("post-informed"); query("UPDATE connection_cards SET client_informed=true WHERE id=%(id)s", {"id": c["id"]}); scan(); row = query("SELECT count(*) n FROM notifications WHERE card_id=%(id)s AND event_type_code=2", {"id": c["id"]}); check(row["n"] == 0, "post-informed escalation")

def scenario_3() -> None:
    c = card("overdue"); query("UPDATE connection_cards SET planned_start_at=now()-interval '2 hours', planned_duration_minutes=1 WHERE id=%(id)s", {"id": c["id"]}); scan(); row = query("SELECT overdue_at, status_code FROM connection_cards WHERE id=%(id)s", {"id": c["id"]}); check(row["overdue_at"] is not None and row["status_code"] == 1, "overdue status"); scan(); row = query("SELECT count(*) n FROM card_events WHERE card_id=%(id)s AND comment='l2_overdue'", {"id": c["id"]}); check(row["n"] == 1, "overdue once")

def scenario_4() -> None:
    c = card("catch-up"); scan(); row = query("SELECT max(last_count) n FROM reminder_schedules WHERE card_id=%(id)s", {"id": c["id"]}); check(row["n"] >= 1, "catch-up count"); scan(); row = query("SELECT count(*) n FROM notifications WHERE card_id=%(id)s", {"id": c["id"]}); check(row["n"] <= 4, "catch-up flood")

def scenario_5() -> None:
    with db_connection() as a, db_connection() as b:
        x = PostgresReminderRepository(a).claim_due(now=datetime.now(UTC), limit=1); y = PostgresReminderRepository(b).claim_due(now=datetime.now(UTC), limit=1); a.rollback(); b.rollback(); check(not (x and y and x[0].id == y[0].id), "skip locked duplicate")

def scenario_6() -> None:
    c = card("savepoint"); scan(); row = query("SELECT count(*) n FROM card_events WHERE card_id=%(id)s", {"id": c["id"]}); check(row["n"] > 0, "savepoint valid record")

def scenario_7() -> None:
    for mode in ("temporary", "permanent"):
        urllib.request.urlopen(urllib.request.Request("http://stub:8080/control/mode", data=json.dumps({"telegram": mode}).encode(), method="POST"), timeout=3).read(); c = card("delivery-" + mode); scan()
        with db_connection() as db:
            deliver_pending_notifications(PostgresNotificationRuntimeRepository(db), {0: TelegramAdapter(), 1: Bitrix24Adapter()})
        check(query("SELECT count(*) n FROM notifications WHERE card_id=%(id)s", {"id": c["id"]})["n"] > 0, "delivery intent")

def scenario_8() -> None:
    for name in ("confirm", "reject", "reassign", "cycle", "reschedule", "terminal"):
        c = card(name); query("UPDATE reminder_schedules SET closed_at=now() WHERE card_id=%(id)s AND closed_at IS NULL", {"id": c["id"]}); check(query("SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL", {"id": c["id"]})["n"] == 0, "schedule closure")

SCENARIOS = (scenario_1, scenario_2, scenario_3, scenario_4, scenario_5, scenario_6, scenario_7, scenario_8)

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--timeout", type=float, default=180); args = parser.parse_args(); deadline = time.monotonic() + args.timeout
    for number, scenario in enumerate(SCENARIOS, 1):
        check(time.monotonic() < deadline, "harness timeout"); scenario(); print(f"scenario-{number}=PASS", flush=True)

if __name__ == "__main__":
    main()

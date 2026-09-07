"""Bounded end-to-end reminder scenarios using the real runtime."""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime, timedelta

from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.cards.constants import ActorType
from app.db import db_connection
from app.notifications import Bitrix24Adapter, PostgresNotificationRuntimeRepository, PostgresNotificationService, TelegramAdapter, deliver_pending_notifications
from app.reminders import PostgresReminderRepository, ReminderService

MARKER = "REMINDER_SMOKE"
FIXTURE_NAMES = ("chain", "post-informed", "overdue", "catch-up", "concurrent", "savepoint", "delivery-temporary", "delivery-permanent", "confirm", "reject", "reassign", "cycle", "reschedule", "terminal")

def query(statement: str, params: dict | None = None) -> dict:
    with db_connection() as c, c.cursor() as cur:
        cur.execute(statement, params or {})
        row = dict(cur.fetchone() or {}) if cur.description else {}
        c.commit()
        return row

def card(name: str) -> dict:
    ticket = f"999-{FIXTURE_NAMES.index(name):06d}"
    with db_connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", ("smoke-l2",)); l2 = cur.fetchone()["id"]
            cur.execute("SELECT id FROM users WHERE username=%s", ("smoke-actor",)); actor = cur.fetchone()["id"]
        start = datetime.now(UTC) + (timedelta(minutes=30) if name != "overdue" else timedelta(hours=-2))
        created = CardService(PostgresCardRepository(c)).create_card(
            CardCreateRequest(omnidesk_ticket_number=ticket, planned_start_at=start, planned_duration_minutes=30,
                              l1_owner_id=None, l2_engineer_id=l2, description=f"{MARKER}:{name}"),
            actor_user_id=actor, ip_address=None, user_agent="reminder-smoke", actor_type=ActorType.INTERNAL_USER)
        PostgresCardRepository(c).create_reminder_schedule(card_id=created.id, kind="l2_reminder", owner_id=l2,
                                                            anchor_at=datetime.now(UTC) - timedelta(seconds=5))
        c.commit()
        return {"id": created.id}

def scan() -> int:
    with db_connection() as c:
        result = ReminderService(PostgresReminderRepository(c), PostgresNotificationService(c)).scan(now=datetime.now(UTC), batch_size=100)
        c.commit()
        return result

def check(value: bool, reason: str) -> None:
    if not value:
        raise AssertionError(reason)

def scenario_1() -> None:
    c = card("chain")
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        row = query("SELECT (SELECT count(*) FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder') events, (SELECT count(*) FROM audit_log WHERE entity_type='reminder_schedule' AND entity_id=%(id)s) audits, (SELECT count(*) FROM notifications WHERE card_id=%(id)s) intents, (SELECT count(*) FROM notifications WHERE card_id=%(id)s AND status_code=1) sent", {"id": c["id"]})
        if row["events"] > 0 and row["audits"] > 0 and row["intents"] > 0 and row["sent"] > 0:
            with urllib.request.urlopen("http://stub:8080/stats", timeout=3) as response:
                stats = json.load(response)
            check(sum(channel["calls"] for channel in stats.values()) > 0, "stub delivery")
            return
        time.sleep(1)
    raise TimeoutError("background beat worker chain timeout")

def scenario_2() -> None:
    c = card("post-informed"); query("UPDATE connection_cards SET client_informed=true WHERE id=%(id)s", {"id": c["id"]}); scan(); row = query("SELECT count(*) n FROM notifications WHERE card_id=%(id)s AND event_type_code=2", {"id": c["id"]}); check(row["n"] == 0, "post-informed escalation")

def scenario_3() -> None:
    c = card("overdue"); query("UPDATE connection_cards SET planned_start_at=now()-interval '2 hours', planned_duration_minutes=30 WHERE id=%(id)s", {"id": c["id"]}); scan(); row = query("SELECT overdue_at, status_code FROM connection_cards WHERE id=%(id)s", {"id": c["id"]}); check(row["overdue_at"] is not None and row["status_code"] == 1, "overdue status"); scan(); row = query("SELECT count(*) n FROM card_events WHERE card_id=%(id)s AND comment='l2_overdue'", {"id": c["id"]}); check(row["n"] == 1, "overdue once")

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

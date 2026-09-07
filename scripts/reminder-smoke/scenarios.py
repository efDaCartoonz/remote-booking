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
        return {"id": created.id, "public_id": created.public_id, "l2": l2, "actor": actor}

def scan() -> int:
    with db_connection() as c:
        result = ReminderService(PostgresReminderRepository(c), PostgresNotificationService(c)).scan(now=datetime.now(UTC), batch_size=100)
        c.commit()
        return result

def workflow_card(name: str) -> dict:
    ticket = f"998-{FIXTURE_NAMES.index(name):06d}"
    with db_connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", ("smoke-actor",)); actor = cur.fetchone()["id"]
        created = CardService(PostgresCardRepository(c)).create_card(CardCreateRequest(omnidesk_ticket_number=ticket, planned_start_at=datetime.now(UTC) + timedelta(hours=3 + FIXTURE_NAMES.index(name)), planned_duration_minutes=30, description=f"{MARKER}:workflow:{name}"), actor_user_id=actor, ip_address=None, user_agent="reminder-smoke", actor_type=ActorType.INTERNAL_USER)
        c.commit()
        return {"id": created.id, "public_id": created.public_id, "actor": actor, "l2": created.l2_engineer_id}

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
    c = workflow_card("post-informed")
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        rejected = service.reject_card(c["public_id"], actor_user_id=c["l2"], rejection_reason="smoke-rejected", ip_address=None, user_agent="reminder-smoke")
        db.commit()
        check(rejected.l1_owner_id is not None and rejected.status_code == 4, "rejected lifecycle")
        informed = service.mark_client_informed(rejected.public_id, actor_user_id=rejected.l1_owner_id, ip_address=None, user_agent="reminder-smoke")
        informed_again = service.mark_client_informed(rejected.public_id, actor_user_id=rejected.l1_owner_id, ip_address=None, user_agent="reminder-smoke")
        db.commit()
        check(informed.client_informed and informed_again == informed, "client informed service")
    row = query("SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL AND kind='l1_reminder' AND settings_snapshot->>'l1_mode'='post_informed'", {"id": c["id"]})
    check(row["n"] == 1, "post-informed schedule")
    scan()
    row = query("SELECT count(*) n FROM notifications WHERE card_id=%(id)s AND event_type_code=2", {"id": c["id"]})
    check(row["n"] == 0, "post-informed escalation")

def scenario_3() -> None:
    c = card("overdue"); query("UPDATE connection_cards SET planned_start_at=now()-interval '2 hours', planned_duration_minutes=30 WHERE id=%(id)s", {"id": c["id"]}); scan(); row = query("SELECT overdue_at, status_code FROM connection_cards WHERE id=%(id)s", {"id": c["id"]}); check(row["overdue_at"] is not None and row["status_code"] == 1, "overdue status"); scan(); row = query("SELECT count(*) events, (SELECT count(*) FROM audit_log WHERE entity_type='connection_card' AND entity_id=%(id)s AND new_values @> '{\"overdue\": true}') audits, (SELECT count(*) FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL) active FROM card_events WHERE card_id=%(id)s AND comment='l2_overdue'", {"id": c["id"]}); check(row["events"] == 1 and row["audits"] == 1 and row["active"] == 0, "overdue once and close")

def scenario_4() -> None:
    c = card("catch-up"); row = query("SELECT planned_start_at + planned_duration_minutes * interval '1 minute' > now() future FROM connection_cards WHERE id=%(id)s", {"id": c["id"]}); check(row["future"], "catch-up not overdue"); query("UPDATE reminder_schedules SET anchor_at=now()-interval '5 seconds', next_due_at=now()-interval '1 second' WHERE card_id=%(id)s", {"id": c["id"]}); scan(); row = query("SELECT last_count, next_due_at > now() future, (SELECT count(*) FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder') events, (SELECT count(*) FROM notifications WHERE card_id=%(id)s) intents FROM reminder_schedules WHERE card_id=%(id)s", {"id": c["id"]}); check(row["last_count"] == 2 and row["future"] and row["events"] == 1 and row["intents"] <= 2, "catch-up exact")

def scenario_5() -> None:
    c = card("concurrent"); target = query("SELECT id FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL", {"id": c["id"]})["id"]
    with db_connection() as a, db_connection() as b:
        with a.cursor() as cur: cur.execute("SELECT id FROM reminder_schedules WHERE id=%s FOR UPDATE", (target,))
        y = PostgresReminderRepository(b).claim_due(now=datetime.now(UTC), limit=100)
        b.rollback(); a.rollback(); check(all(item.id != target for item in y), "specific schedule skip locked")

def scenario_6() -> None:
    bad, good = card("savepoint"), card("catch-up")
    query("UPDATE reminder_schedules SET next_due_at=now()-interval '1 second' WHERE card_id IN (%(bad)s,%(good)s)", {"bad": bad["id"], "good": good["id"]})
    with db_connection() as db, db.cursor() as cur:
        cur.execute("CREATE OR REPLACE FUNCTION reminder_smoke_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.card_id = %s THEN RAISE EXCEPTION 'reminder_smoke_trigger'; END IF; RETURN NEW; END $$", (bad["id"],))
        cur.execute("CREATE TRIGGER reminder_smoke_fail_trigger BEFORE INSERT ON card_events FOR EACH ROW EXECUTE FUNCTION reminder_smoke_fail()")
        db.commit()
    try:
        scan()
    finally:
        query("DROP TRIGGER IF EXISTS reminder_smoke_fail_trigger ON card_events; DROP FUNCTION IF EXISTS reminder_smoke_fail()")
    row = query("SELECT (SELECT count(*) FROM card_events WHERE card_id=%(bad)s AND comment='timer_reminder') bad_events, (SELECT count(*) FROM card_events WHERE card_id=%(good)s AND comment='timer_reminder') good_events", {"bad": bad["id"], "good": good["id"]})
    check(row["bad_events"] == 0 and row["good_events"] == 1, "savepoint isolation")

def scenario_7() -> None:
    urllib.request.urlopen(urllib.request.Request("http://stub:8080/control/reset", data=b"{}", method="POST"), timeout=3).read()
    urllib.request.urlopen(urllib.request.Request("http://stub:8080/control/mode", data=json.dumps({"telegram": "temporary", "bitrix": "success"}).encode(), method="POST"), timeout=3).read()
    temp = card("delivery-temporary"); scan()
    with db_connection() as db: deliver_pending_notifications(PostgresNotificationRuntimeRepository(db), {0: TelegramAdapter(), 1: Bitrix24Adapter()})
    query("UPDATE notifications SET next_attempt_at=now() WHERE card_id=%(id)s AND status_code=0", {"id": temp["id"]})
    with db_connection() as db: deliver_pending_notifications(PostgresNotificationRuntimeRepository(db), {0: TelegramAdapter(), 1: Bitrix24Adapter()})
    row = query("SELECT status_code, attempts FROM notifications WHERE card_id=%(id)s AND channel_code=0", {"id": temp["id"]}); check(row["status_code"] == 1 and row["attempts"] == 2, "temporary retry")
    with urllib.request.urlopen("http://stub:8080/stats", timeout=3) as response: stats = json.load(response)
    check(stats["telegram"]["calls"] == 2 and stats["telegram"]["codes"] == [500, 200], "temporary stub calls")
    urllib.request.urlopen(urllib.request.Request("http://stub:8080/control/reset", data=b"{}", method="POST"), timeout=3).read()
    urllib.request.urlopen(urllib.request.Request("http://stub:8080/control/mode", data=json.dumps({"telegram": "permanent", "bitrix": "success"}).encode(), method="POST"), timeout=3).read()
    permanent = card("delivery-permanent"); scan()
    with db_connection() as db: deliver_pending_notifications(PostgresNotificationRuntimeRepository(db), {0: TelegramAdapter(), 1: Bitrix24Adapter()})
    row = query("SELECT status_code, attempts, error_message FROM notifications WHERE card_id=%(id)s AND channel_code=0", {"id": permanent["id"]}); check(row["status_code"] == 2 and row["attempts"] == 1 and row["error_message"] == "telegram_rejected", "permanent terminal")
    with urllib.request.urlopen("http://stub:8080/stats", timeout=3) as response: stats = json.load(response)
    check(stats["telegram"]["calls"] == 1 and stats["telegram"]["codes"] == [400], "permanent stub calls")

def scenario_8() -> None:
    c = workflow_card("confirm")
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db)); service.confirm_card(c["public_id"], actor_user_id=c["l2"], comment="smoke", ip_address=None, user_agent="reminder-smoke"); db.commit()
    check(query("SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL", {"id": c["id"]})["n"] == 0, "confirm closes schedule")
    r = workflow_card("reject")
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db)); rejected = service.reject_card(r["public_id"], actor_user_id=r["l2"], rejection_reason="smoke", ip_address=None, user_agent="reminder-smoke"); db.commit()
    check(query("SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL", {"id": r["id"]})["n"] == 1, "reject creates next lifecycle schedule")
    s = workflow_card("reschedule")
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db)); service.reject_card(s["public_id"], actor_user_id=s["l2"], rejection_reason="smoke", ip_address=None, user_agent="reminder-smoke")
        with db.cursor() as cur: cur.execute("SELECT l1_owner_id FROM connection_cards WHERE id=%s", (s["id"],)); l1_owner = cur.fetchone()["l1_owner_id"]
        service.update_rejected_card(s["public_id"], actor_user_id=l1_owner, planned_start_at=datetime.now(UTC) + timedelta(hours=2), planned_duration_minutes=30, description="smoke-rescheduled", ip_address=None, user_agent="reminder-smoke"); db.commit()
    check(query("SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL", {"id": s["id"]})["n"] <= 1, "reschedule closes prior schedule")
    t = workflow_card("terminal")
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db)); service.confirm_card(t["public_id"], actor_user_id=t["l2"], comment="smoke", ip_address=None, user_agent="reminder-smoke"); service.start_card(t["public_id"], actor_user_id=t["l2"], comment=None, ip_address=None, user_agent="reminder-smoke"); service.complete_card(t["public_id"], result_code=0, engineer_report="smoke complete", actor_user_id=t["l2"], comment=None, ip_address=None, user_agent="reminder-smoke"); db.commit()
    check(query("SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL", {"id": t["id"]})["n"] == 0, "terminal closes schedule")

SCENARIOS = (scenario_1, scenario_2, scenario_3, scenario_4, scenario_5, scenario_6, scenario_7, scenario_8)

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--timeout", type=float, default=180); args = parser.parse_args(); deadline = time.monotonic() + args.timeout
    for number, scenario in enumerate(SCENARIOS, 1):
        check(time.monotonic() < deadline, "harness timeout"); scenario(); print(f"scenario-{number}=PASS", flush=True)

if __name__ == "__main__":
    main()

"""Bounded end-to-end reminder scenarios using the real runtime."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from psycopg import sql

from app.cards.repository import PostgresCardRepository
from app.cards.schemas import CardCreateRequest
from app.cards.service import CardService
from app.cards.constants import ActorType
from app.db import db_connection
from app.notifications import (
    Bitrix24Adapter,
    PostgresNotificationRuntimeRepository,
    PostgresNotificationService,
    TelegramAdapter,
    deliver_pending_notifications,
)
from app.reminders import PostgresReminderRepository, ReminderService

MARKER = "REMINDER_SMOKE"
FIXTURE_NAMES = (
    "chain",
    "post-informed",
    "overdue",
    "catch-up",
    "concurrent",
    "savepoint",
    "savepoint-good",
    "delivery-temporary",
    "delivery-permanent",
    "confirm",
    "reject",
    "reassign",
    "cycle",
    "reschedule",
    "terminal",
)


def query_one(label: str, statement: str, params: dict | None = None) -> dict:
    with db_connection() as c, c.cursor() as cur:
        try:
            cur.execute(statement, params or {})
        except Exception as exc:
            raise type(exc)(
                f"{label}: SQL failed; params={sorted((params or {}).keys())}"
            ) from exc
        rows = cur.fetchall()
        if len(rows) != 1:
            raise AssertionError(f"{label}: expected one row, got {len(rows)}")
        row = dict(rows[0])
        c.commit()
        return row


def execute(
    label: str,
    statement: str,
    params: dict | None = None,
    expected_rowcount: int | None = None,
) -> int:
    with db_connection() as c, c.cursor() as cur:
        try:
            cur.execute(statement, params or {})
        except Exception as exc:
            raise type(exc)(
                f"{label}: SQL failed; params={sorted((params or {}).keys())}"
            ) from exc
        count = cur.rowcount
        if expected_rowcount is not None and count != expected_rowcount:
            raise AssertionError(
                f"{label}: expected rowcount {expected_rowcount}, got {count}"
            )
        c.commit()
        return count


def install_selective_failure_trigger(connection, bad_card_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION reminder_smoke_fail() RETURNS trigger LANGUAGE plpgsql AS 'BEGIN RAISE EXCEPTION ''reminder_smoke_trigger''; END'"
            )
        )
        cur.execute(
            sql.SQL(
                "CREATE TRIGGER reminder_smoke_fail_trigger BEFORE INSERT ON card_events FOR EACH ROW WHEN (NEW.card_id = {}) EXECUTE FUNCTION reminder_smoke_fail()"
            ).format(sql.Literal(bad_card_id))
        )
    connection.commit()


def trigger_selectivity_preflight(bad_card_id: int, good_card_id: int) -> None:
    with db_connection() as connection:
        try:
            with connection.transaction():
                with connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO card_events (card_id, event_type_code, actor_type_code, comment) VALUES (%s, 4, 2, 'reminder_smoke_preflight_good')",
                        (good_card_id,),
                    )
                raise RuntimeError("preflight rollback")
        except RuntimeError:
            pass
        try:
            with connection.transaction():
                with connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO card_events (card_id, event_type_code, actor_type_code, comment) VALUES (%s, 4, 2, 'reminder_smoke_preflight_bad')",
                        (bad_card_id,),
                    )
        except Exception as exc:
            primary = getattr(getattr(exc, "diag", None), "message_primary", None)
            check(primary == "reminder_smoke_trigger", "trigger preflight exception")
        with connection.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            check(cur.fetchone()["ok"] == 1, "trigger preflight connection")


def query(statement: str, params: dict | None = None) -> dict:
    """Compatibility shim; new deterministic checks use explicit helpers."""
    if statement.lstrip().upper().startswith(("SELECT", "WITH")):
        return query_one("legacy_query", statement, params)
    execute("legacy_execute", statement, params)
    return {}


def card(name: str) -> dict:
    ticket = f"999-{FIXTURE_NAMES.index(name):06d}"
    with db_connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", ("smoke-l2",))
            l2 = cur.fetchone()["id"]
            cur.execute("SELECT id FROM users WHERE username=%s", ("smoke-actor",))
            actor = cur.fetchone()["id"]
        start = (
            (datetime.now(UTC) + timedelta(hours=8))
            if name == "catch-up"
            else (
                datetime.now(UTC)
                + (
                    timedelta(minutes=30 + FIXTURE_NAMES.index(name) * 40)
                    if name != "overdue"
                    else timedelta(hours=-2)
                )
            )
        )
        created = CardService(PostgresCardRepository(c)).create_card(
            CardCreateRequest(
                omnidesk_ticket_number=ticket,
                planned_start_at=start,
                planned_duration_minutes=30,
                l1_owner_id=None,
                l2_engineer_id=l2,
                description=f"{MARKER}:{name}",
            ),
            actor_user_id=actor,
            ip_address=None,
            user_agent="reminder-smoke",
            actor_type=ActorType.INTERNAL_USER,
        )
        anchor = (
            datetime.now(UTC) + timedelta(seconds=60)
            if name == "catch-up"
            else datetime.now(UTC) - timedelta(seconds=5)
        )
        PostgresCardRepository(c).create_reminder_schedule(
            card_id=created.id, kind="l2_reminder", owner_id=l2, anchor_at=anchor
        )
        c.commit()
        return {
            "id": created.id,
            "public_id": created.public_id,
            "l2": l2,
            "actor": actor,
        }


def scan(now: datetime | None = None) -> int:
    with db_connection() as c:
        result = ReminderService(
            PostgresReminderRepository(c), PostgresNotificationService(c)
        ).scan(now=now or datetime.now(UTC), batch_size=100)
        c.commit()
        return result


def workflow_card(name: str) -> dict:
    ticket = f"998-{FIXTURE_NAMES.index(name):06d}"
    with db_connection() as c:
        with c.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username=%s", ("smoke-actor",))
            actor = cur.fetchone()["id"]
        created = CardService(PostgresCardRepository(c)).create_card(
            CardCreateRequest(
                omnidesk_ticket_number=ticket,
                planned_start_at=datetime.now(UTC)
                + timedelta(hours=2, minutes=(20 + FIXTURE_NAMES.index(name)) * 45),
                planned_duration_minutes=30,
                description=f"{MARKER}:workflow:{name}",
            ),
            actor_user_id=actor,
            ip_address=None,
            user_agent="reminder-smoke",
            actor_type=ActorType.INTERNAL_USER,
        )
        c.commit()
        return {
            "id": created.id,
            "public_id": created.public_id,
            "actor": actor,
            "l2": created.l2_engineer_id,
        }


def check(value: bool, reason: str) -> None:
    if not value:
        raise AssertionError(reason)


def lifecycle_state(label: str, card_id: int) -> dict:
    return query_one(
        label,
        "SELECT status_code, l1_owner_id IS NOT NULL has_l1, l2_engineer_id IS NOT NULL has_l2, (SELECT count(*) FROM assignment_attempts WHERE card_id=c.id AND status_code=0) pending_attempts, (SELECT coalesce(string_agg(kind, ',' ORDER BY kind), '') FROM reminder_schedules WHERE card_id=c.id AND closed_at IS NULL) active_schedules FROM connection_cards c WHERE c.id=%(id)s",
        {"id": card_id},
    )


def require_lifecycle(
    label: str,
    card_id: int,
    *,
    status: int,
    l1: bool | None = None,
    l2: bool | None = None,
    pending: int | None = None,
) -> dict:
    row = lifecycle_state(label, card_id)
    check(row["status_code"] == status, f"{label}: status={row['status_code']}")
    if l1 is not None:
        check(row["has_l1"] is l1, f"{label}: l1={row['has_l1']}")
    if l2 is not None:
        check(row["has_l2"] is l2, f"{label}: l2={row['has_l2']}")
    if pending is not None:
        check(
            row["pending_attempts"] == pending,
            f"{label}: pending={row['pending_attempts']}",
        )
    return row


def scenario_1() -> None:
    c = card("chain")
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        row = query(
            "SELECT (SELECT count(*) FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder') events, (SELECT count(*) FROM audit_log WHERE entity_type='reminder_schedule' AND entity_id=%(id)s) audits, (SELECT count(*) FROM notifications WHERE card_id=%(id)s) intents, (SELECT count(*) FROM notifications WHERE card_id=%(id)s AND status_code=1) sent",
            {"id": c["id"]},
        )
        if (
            row["events"] > 0
            and row["audits"] > 0
            and row["intents"] > 0
            and row["sent"] > 0
        ):
            with urllib.request.urlopen(
                "http://stub:8080/stats", timeout=3
            ) as response:
                stats = json.load(response)
            check(
                sum(channel["calls"] for channel in stats.values()) > 0, "stub delivery"
            )
            return
        time.sleep(1)
    raise TimeoutError("background beat worker chain timeout")


def scenario_2() -> None:
    c = workflow_card("post-informed")
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        rejected = service.reject_card(
            c["public_id"],
            actor_user_id=c["l2"],
            rejection_reason="smoke-rejected",
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
        check(
            rejected.l1_owner_id is not None and rejected.status_code == 4,
            "rejected lifecycle",
        )
        informed = service.mark_client_informed(
            rejected.public_id,
            actor_user_id=rejected.l1_owner_id,
            ip_address=None,
            user_agent="reminder-smoke",
        )
        informed_again = service.mark_client_informed(
            rejected.public_id,
            actor_user_id=rejected.l1_owner_id,
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
        check(
            informed.client_informed and informed_again == informed,
            "client informed service",
        )
    fixed_now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    schedule = query_one(
        "post_informed_schedule",
        "SELECT id, interval_seconds FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL AND kind='l1_reminder' AND settings_snapshot->>'l1_mode'='post_informed'",
        {"id": c["id"]},
    )
    execute(
        "post_informed_due",
        "UPDATE reminder_schedules SET anchor_at=%(anchor)s, next_due_at=%(now)s WHERE id=%(schedule_id)s",
        {
            "anchor": fixed_now - timedelta(seconds=schedule["interval_seconds"]),
            "now": fixed_now,
            "schedule_id": schedule["id"],
        },
        expected_rowcount=1,
    )
    scan(fixed_now)
    row = query_one(
        "post_informed_assertions",
        "SELECT (SELECT count(*) FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder' AND new_values @> '{\"timer\": \"l1_reminder\"}') l1_events, (SELECT count(*) FROM notifications WHERE card_id=%(id)s AND event_type_code=5) l1_intents, (SELECT count(*) FROM notifications WHERE card_id=%(id)s AND event_type_code=2) manager_intents, (SELECT count(*) FROM reminder_schedules WHERE id=%(schedule_id)s AND closed_at IS NULL AND settings_snapshot->>'l1_mode'='post_informed') active",
        {"id": c["id"], "schedule_id": schedule["id"]},
    )
    check(
        row["l1_events"] == 1
        and row["l1_intents"] > 0
        and row["manager_intents"] == 0
        and row["active"] == 1,
        "post-informed assertions",
    )
    scan(fixed_now)
    check(
        query_one(
            "post_informed_no_duplicate",
            "SELECT count(*) n FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder' AND new_values @> '{\"timer\": \"l1_reminder\"}'",
            {"id": c["id"]},
        )["n"]
        == 1,
        "post-informed idempotent",
    )


def scenario_3() -> None:
    c = card("overdue")
    query(
        "UPDATE connection_cards SET planned_start_at=now()-interval '2 hours', planned_duration_minutes=30 WHERE id=%(id)s",
        {"id": c["id"]},
    )
    scan()
    row = query(
        "SELECT overdue_at, status_code FROM connection_cards WHERE id=%(id)s",
        {"id": c["id"]},
    )
    check(row["overdue_at"] is not None and row["status_code"] == 1, "overdue status")
    scan()
    row = query(
        "SELECT count(*) events, (SELECT count(*) FROM audit_log WHERE entity_type='connection_card' AND entity_id=%(id)s AND new_values @> '{\"overdue\": true}') audits, (SELECT count(*) FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL) active FROM card_events WHERE card_id=%(id)s AND comment='l2_overdue'",
        {"id": c["id"]},
    )
    check(
        row["events"] == 1 and row["audits"] == 1 and row["active"] == 0,
        "overdue once and close",
    )


def scenario_4() -> None:
    c = card("catch-up")
    scan_now = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    window = query_one(
        "catchup_card_window",
        "SELECT planned_start_at + planned_duration_minutes * interval '1 minute' > %(now)s future FROM connection_cards WHERE id=%(id)s",
        {"id": c["id"], "now": scan_now},
    )
    check(window["future"], "catchup_card_window")
    schedule = query_one(
        "catchup_schedule_id",
        "SELECT id, interval_seconds FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL",
        {"id": c["id"]},
    )
    interval = timedelta(seconds=1)
    execute(
        "catchup_schedule_update",
        "UPDATE reminder_schedules SET anchor_at=%(anchor)s, next_due_at=%(now)s WHERE id=%(schedule_id)s",
        {
            "anchor": scan_now - 3 * interval,
            "now": scan_now,
            "schedule_id": schedule["id"],
        },
        expected_rowcount=1,
    )
    scan(scan_now)
    result = query_one(
        "catchup_schedule_result",
        "SELECT last_count, next_due_at=%(next)s exact_due, closed_at IS NULL active FROM reminder_schedules WHERE id=%(schedule_id)s",
        {"schedule_id": schedule["id"], "next": scan_now + interval},
    )
    events = query_one(
        "catchup_event_count",
        "SELECT count(*) n FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder'",
        {"id": c["id"]},
    )
    check(
        result["last_count"] == 3
        and result["exact_due"]
        and result["active"]
        and events["n"] == 1,
        "catch-up exact",
    )
    scan(scan_now)
    check(
        query_one(
            "catchup_event_count_repeat",
            "SELECT count(*) n FROM card_events WHERE card_id=%(id)s AND comment='timer_reminder'",
            {"id": c["id"]},
        )["n"]
        == 1,
        "catch-up idempotent",
    )


def scenario_5() -> None:
    c = card("concurrent")
    target = query(
        "SELECT id FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL",
        {"id": c["id"]},
    )["id"]
    with db_connection() as a, db_connection() as b:
        with a.cursor() as cur:
            cur.execute(
                "SELECT id FROM reminder_schedules WHERE id=%s FOR UPDATE", (target,)
            )
        y = PostgresReminderRepository(b).claim_due(now=datetime.now(UTC), limit=100)
        b.rollback()
        a.rollback()
        check(all(item.id != target for item in y), "specific schedule skip locked")
    execute(
        "concurrent_schedule_cleanup",
        "UPDATE reminder_schedules SET next_due_at=now()+interval '1 hour' WHERE id=%(schedule_id)s",
        {"schedule_id": target},
        expected_rowcount=1,
    )


def scenario_6() -> None:
    bad, good = card("savepoint"), card("savepoint-good")
    query(
        "UPDATE reminder_schedules SET next_due_at=now()-interval '1 second' WHERE card_id IN (%(bad)s,%(good)s)",
        {"bad": bad["id"], "good": good["id"]},
    )
    with db_connection() as db:
        install_selective_failure_trigger(db, bad["id"])
    trigger_selectivity_preflight(bad["id"], good["id"])
    try:
        scan()
    finally:
        execute(
            "savepoint_trigger_cleanup",
            "DROP TRIGGER IF EXISTS reminder_smoke_fail_trigger ON card_events",
        )
        execute(
            "savepoint_function_cleanup",
            "DROP FUNCTION IF EXISTS reminder_smoke_fail()",
        )
    row = query(
        "SELECT (SELECT count(*) FROM card_events WHERE card_id=%(bad)s AND comment='timer_reminder') bad_events, (SELECT count(*) FROM card_events WHERE card_id=%(good)s AND comment='timer_reminder') good_events",
        {"bad": bad["id"], "good": good["id"]},
    )
    check(
        row["bad_events"] == 0 and row["good_events"] == 1,
        f"savepoint isolation bad_events={row['bad_events']} good_events={row['good_events']}",
    )


def scenario_7() -> None:
    def isolate(label: str) -> None:
        execute(
            label + "_schedules",
            "UPDATE reminder_schedules SET next_due_at=now()+interval '1 day' WHERE closed_at IS NULL AND next_due_at <= now()",
        )
        execute(
            label + "_intents",
            "UPDATE notifications SET next_attempt_at=now()+interval '1 day' WHERE status_code=0",
        )

    isolate("temporary_delivery_isolation")
    urllib.request.urlopen(
        urllib.request.Request(
            "http://stub:8080/control/reset", data=b"{}", method="POST"
        ),
        timeout=3,
    ).read()
    urllib.request.urlopen(
        urllib.request.Request(
            "http://stub:8080/control/mode",
            data=json.dumps({"telegram": "temporary", "bitrix": "success"}).encode(),
            method="POST",
        ),
        timeout=3,
    ).read()
    temp = card("delivery-temporary")
    execute(
        "temporary_delivery_disable_escalation",
        "UPDATE reminder_schedules SET escalation_after_count=999 WHERE card_id=%(id)s",
        {"id": temp["id"]},
        expected_rowcount=1,
    )
    scan()
    target = query_one(
        "temporary_target_intent",
        "SELECT n.id, n.status_code, n.attempts FROM notifications n WHERE n.card_id=%(id)s AND n.channel_code=0 AND n.status_code=0 AND (n.next_attempt_at IS NULL OR n.next_attempt_at <= now())",
        {"id": temp["id"]},
    )
    check(
        query_one(
            "temporary_other_due",
            "SELECT count(*) n FROM notifications n WHERE n.card_id<>%(id)s AND n.channel_code=0 AND n.status_code=0 AND (n.next_attempt_at IS NULL OR n.next_attempt_at <= now())",
            {"id": temp["id"]},
        )["n"]
        == 0,
        "temporary queue isolated",
    )
    with db_connection() as db:
        deliver_pending_notifications(
            PostgresNotificationRuntimeRepository(db),
            {0: TelegramAdapter(), 1: Bitrix24Adapter()},
        )
    first = query_one(
        "temporary_after_first",
        "SELECT status_code, attempts FROM notifications WHERE id=%(id)s",
        {"id": target["id"]},
    )
    check(
        first["status_code"] == 0 and first["attempts"] == 1,
        "temporary first retry state",
    )
    execute(
        "temporary_target_due",
        "UPDATE notifications SET next_attempt_at=now() WHERE id=%(id)s",
        {"id": target["id"]},
        expected_rowcount=1,
    )
    with db_connection() as db:
        deliver_pending_notifications(
            PostgresNotificationRuntimeRepository(db),
            {0: TelegramAdapter(), 1: Bitrix24Adapter()},
        )
    row = query_one(
        "temporary_delivery_assertions",
        "SELECT status_code, attempts FROM notifications WHERE id=%(id)s",
        {"id": target["id"]},
    )
    check(
        row["status_code"] == 1 and row["attempts"] == 2,
        f"temporary retry status={row['status_code']} attempts={row['attempts']}",
    )
    with urllib.request.urlopen("http://stub:8080/stats", timeout=3) as response:
        stats = json.load(response)
    check(
        stats["telegram"]["calls"] == 2 and stats["telegram"]["codes"] == [500, 200],
        "temporary stub calls",
    )
    isolate("permanent_delivery_isolation")
    urllib.request.urlopen(
        urllib.request.Request(
            "http://stub:8080/control/reset", data=b"{}", method="POST"
        ),
        timeout=3,
    ).read()
    urllib.request.urlopen(
        urllib.request.Request(
            "http://stub:8080/control/mode",
            data=json.dumps({"telegram": "permanent", "bitrix": "success"}).encode(),
            method="POST",
        ),
        timeout=3,
    ).read()
    permanent = card("delivery-permanent")
    execute(
        "permanent_delivery_disable_escalation",
        "UPDATE reminder_schedules SET escalation_after_count=999 WHERE card_id=%(id)s",
        {"id": permanent["id"]},
        expected_rowcount=1,
    )
    scan()
    target = query_one(
        "permanent_target_intent",
        "SELECT n.id, n.status_code, n.attempts FROM notifications n WHERE n.card_id=%(id)s AND n.channel_code=0 AND n.status_code=0 AND (n.next_attempt_at IS NULL OR n.next_attempt_at <= now())",
        {"id": permanent["id"]},
    )
    check(
        query_one(
            "permanent_other_due",
            "SELECT count(*) n FROM notifications n WHERE n.card_id<>%(id)s AND n.channel_code=0 AND n.status_code=0 AND (n.next_attempt_at IS NULL OR n.next_attempt_at <= now())",
            {"id": permanent["id"]},
        )["n"]
        == 0,
        "permanent queue isolated",
    )
    with db_connection() as db:
        deliver_pending_notifications(
            PostgresNotificationRuntimeRepository(db),
            {0: TelegramAdapter(), 1: Bitrix24Adapter()},
        )
    row = query_one(
        "permanent_delivery_assertions",
        "SELECT status_code, attempts, error_message FROM notifications WHERE id=%(id)s",
        {"id": target["id"]},
    )
    check(
        row["status_code"] == 2
        and row["attempts"] == 1
        and row["error_message"] == "telegram_rejected",
        "permanent terminal",
    )
    with urllib.request.urlopen("http://stub:8080/stats", timeout=3) as response:
        stats = json.load(response)
    check(
        stats["telegram"]["calls"] == 1 and stats["telegram"]["codes"] == [400],
        "permanent stub calls",
    )


def scenario_8() -> None:
    c = workflow_card("confirm")
    require_lifecycle("confirm_precondition", c["id"], status=1, l2=True, pending=1)
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        service.confirm_card(
            c["public_id"],
            actor_user_id=c["l2"],
            comment="smoke",
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
    check(
        query(
            "SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL",
            {"id": c["id"]},
        )["n"]
        == 0,
        "confirm closes schedule",
    )
    r = workflow_card("reject")
    require_lifecycle("reject_precondition", r["id"], status=1, l2=True, pending=1)
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        rejected = service.reject_card(
            r["public_id"],
            actor_user_id=r["l2"],
            rejection_reason="smoke",
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
    check(
        query(
            "SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL",
            {"id": r["id"]},
        )["n"]
        == 1,
        "reject creates next lifecycle schedule",
    )
    s = workflow_card("reschedule")
    require_lifecycle(
        "reschedule_reject_precondition", s["id"], status=1, l2=True, pending=1
    )
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        service.reject_card(
            s["public_id"],
            actor_user_id=s["l2"],
            rejection_reason="smoke",
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
    require_lifecycle(
        "reschedule_update_precondition",
        s["id"],
        status=4,
        l1=True,
        l2=False,
        pending=0,
    )
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        with db.cursor() as cur:
            cur.execute(
                "SELECT l1_owner_id FROM connection_cards WHERE id=%s", (s["id"],)
            )
            l1_owner = cur.fetchone()["l1_owner_id"]
        service.update_rejected_card(
            s["public_id"],
            actor_user_id=l1_owner,
            planned_start_at=datetime.now(UTC) + timedelta(hours=2),
            planned_duration_minutes=30,
            description="smoke-rescheduled",
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
    check(
        query(
            "SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL",
            {"id": s["id"]},
        )["n"]
        <= 1,
        "reschedule closes prior schedule",
    )
    t = workflow_card("terminal")
    require_lifecycle(
        "terminal_confirm_precondition", t["id"], status=1, l2=True, pending=1
    )
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        service.confirm_card(
            t["public_id"],
            actor_user_id=t["l2"],
            comment="smoke",
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
    require_lifecycle(
        "terminal_start_precondition", t["id"], status=2, l2=True, pending=0
    )
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        service.start_card(
            t["public_id"],
            actor_user_id=t["l2"],
            comment=None,
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
    require_lifecycle(
        "terminal_complete_precondition", t["id"], status=3, l2=True, pending=0
    )
    with db_connection() as db:
        service = CardService(PostgresCardRepository(db))
        service.complete_card(
            t["public_id"],
            result_code=0,
            engineer_report="smoke complete",
            actor_user_id=t["l2"],
            comment=None,
            ip_address=None,
            user_agent="reminder-smoke",
        )
        db.commit()
    check(
        query(
            "SELECT count(*) n FROM reminder_schedules WHERE card_id=%(id)s AND closed_at IS NULL",
            {"id": t["id"]},
        )["n"]
        == 0,
        "terminal closes schedule",
    )


SCENARIOS = (
    scenario_1,
    scenario_2,
    scenario_3,
    scenario_4,
    scenario_5,
    scenario_6,
    scenario_7,
    scenario_8,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=8)
    args = parser.parse_args()
    deadline = time.monotonic() + args.timeout
    for number, scenario in enumerate(SCENARIOS, 1):
        if number < args.start or number > args.end:
            continue
        check(time.monotonic() < deadline, "harness timeout")
        scenario()
        print(f"scenario-{number}=PASS", flush=True)


if __name__ == "__main__":
    main()

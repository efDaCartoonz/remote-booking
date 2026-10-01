"""Create or remove synthetic demo data for the test stand.

Everything created here is marked as demo data and nothing else is touched:
users are named ``demo-*``, clients ``DEMO ...``, cards carry ``DEMO:`` in the
description and use ticket numbers from the ``900-`` range. ``--remove`` deletes
exactly those records. No external system is contacted.

The demo users' password is read from stdin and never printed:
    printf '%s' "$PASSWORD" | python scripts/seed_demo.py --apply --password-stdin
    python scripts/seed_demo.py --remove
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from app.admin.users import PostgresAdminUserRepository, UsernameConflictError
from app.cards.constants import (
    ActorType,
    AssignmentAttemptStatus,
    AssignmentCycleStatus,
    CardEventType,
    CardStatus,
    RoleId,
)
from app.db import db_connection

DEMO_TZ = "Asia/Yekaterinburg"
USER_PREFIX = "demo-"
TICKET_PREFIX = "900"
DESCRIPTION_PREFIX = "DEMO:"
MIN_PASSWORD_LENGTH = 8

# (username, full name, role, schedule kind)
DEMO_USERS: list[tuple[str, str, RoleId, str]] = [
    ("demo-l1-anna", "DEMO Анна Смирнова", RoleId.L1, "weekdays_day"),
    ("demo-l1-oleg", "DEMO Олег Фёдоров", RoleId.L1, "weekdays_day"),
    ("demo-l2-ivan", "DEMO Иван Петров", RoleId.L2, "weekdays_day"),
    ("demo-l2-maria", "DEMO Мария Козлова", RoleId.L2, "cycle_2_2"),
    ("demo-l2-denis", "DEMO Денис Орлов", RoleId.L2, "weekdays_late"),
    ("demo-manager", "DEMO Руководитель", RoleId.MANAGER, "none"),
]

RESULT_CODES = (0, 0, 0, 1, 2, 3)


def read_password(stdin) -> str:
    password = stdin.readline().rstrip("\r\n")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return password


def schedule_day(kind: str, day: date) -> tuple[time, time] | None:
    weekday = day.isoweekday()
    if kind == "weekdays_day":
        return (time(9, 0), time(18, 0)) if weekday <= 5 else None
    if kind == "weekdays_late":
        return (time(13, 0), time(22, 0)) if weekday <= 5 else None
    if kind == "cycle_2_2":
        return (time(8, 0), time(20, 0)) if (day.toordinal() % 4) < 2 else None
    return None


def remove_demo(connection) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM connection_cards WHERE description LIKE %s",
            (f"{DESCRIPTION_PREFIX}%",),
        )
        cards = cursor.rowcount
        cursor.execute("DELETE FROM clients WHERE display_name LIKE 'DEMO %'")
        clients = cursor.rowcount
        cursor.execute("DELETE FROM users WHERE username LIKE %s", (f"{USER_PREFIX}%",))
        users = cursor.rowcount
    return {"cards": cards, "clients": clients, "users": users}


def local_dt(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=ZoneInfo(DEMO_TZ)).astimezone(UTC)


def add_event(
    cursor, card_id, event, at, actor_id, actor_type, new_values, comment=None
):
    cursor.execute(
        """
        INSERT INTO card_events
            (card_id, event_type_code, actor_user_id, actor_type_code, created_at, new_values, comment)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            card_id,
            int(event),
            actor_id,
            int(actor_type),
            at,
            Jsonb(new_values),
            comment,
        ),
    )


def status_event(cursor, card_id, at, actor_id, status, extra=None):
    values = {"status_code": int(status)}
    if extra:
        values.update(extra)
    add_event(
        cursor,
        card_id,
        CardEventType.STATUS_CHANGED,
        at,
        actor_id,
        ActorType.INTERNAL_USER,
        values,
    )


def insert_card(
    cursor,
    *,
    index,
    client_id,
    status,
    planned_start,
    duration,
    l1_id,
    l2_id,
    created_at,
    result_code=None,
    urgent=False,
    out_of_hours=False,
    unsuccessful=0,
    actual=None,
):
    cursor.execute(
        """
        INSERT INTO connection_cards
            (omnidesk_ticket_number, client_id, status_code, urgency_code, planned_start_at,
             planned_duration_minutes, actual_start_at, actual_end_at, l1_owner_id, l2_engineer_id,
             assignment_method_code, unsuccessful_cycle_count, description, urgent_reason,
             out_of_hours_flag, overdue_flag, result_code, engineer_report, created_source_code,
             created_by_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s)
        RETURNING id
        """,
        (
            f"{TICKET_PREFIX}-{100000 + index:06d}",
            client_id,
            int(status),
            1 if urgent else 0,
            planned_start,
            duration,
            actual[0] if actual else None,
            actual[1] if actual else None,
            l1_id,
            l2_id,
            unsuccessful,
            f"{DESCRIPTION_PREFIX} демонстрационная карточка {index}",
            "DEMO: срочное подключение" if urgent else None,
            out_of_hours,
            False,
            result_code,
            "DEMO: работы выполнены" if result_code is not None else None,
            l1_id,
            created_at,
            created_at,
        ),
    )
    return cursor.fetchone()["id"]


def attempt(
    cursor, card_id, cycle_id, l2_id, status, assigned_at, responded_at, reason=None
):
    cursor.execute(
        """
        INSERT INTO assignment_attempts
            (cycle_id, card_id, l2_engineer_id, status_code, assigned_at, responded_at, rejection_reason)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (cycle_id, card_id, l2_id, int(status), assigned_at, responded_at, reason),
    )


def cycle(cursor, card_id, status, started_at, completed_at):
    cursor.execute(
        """
        INSERT INTO assignment_cycles (card_id, cycle_number, status_code, started_at, completed_at)
        VALUES (%s, 1, %s, %s, %s) RETURNING id
        """,
        (card_id, int(status), started_at, completed_at),
    )
    return cursor.fetchone()["id"]


def seed_cards(connection, l1_ids, l2_ids, days: int) -> int:
    rng = random.Random(20261001)
    today = datetime.now(UTC).date()
    count = 0
    with connection.cursor() as cursor:
        clients = []
        for i in range(1, 13):
            cursor.execute(
                "INSERT INTO clients (omnidesk_user_id, display_name, last_confirmed_timezone) "
                "VALUES (%s, %s, %s) RETURNING id",
                (f"demo-client-{i}", f"DEMO Клиент {i}", DEMO_TZ),
            )
            clients.append(cursor.fetchone()["id"])

        index = 0
        for offset in range(days, 0, -1):
            day = today - timedelta(days=offset)
            if day.isoweekday() > 5:
                continue
            for slot in range(rng.choice((1, 2, 2, 3))):
                index += 1
                l1 = rng.choice(l1_ids)
                l2 = l2_ids[(index + slot) % len(l2_ids)]
                second_l2 = l2_ids[(index + slot + 1) % len(l2_ids)]
                created = local_dt(day, time(9 + slot, rng.choice((5, 20, 40))))
                planned = created + timedelta(hours=rng.choice((3, 4, 5)))
                duration = rng.choice((60, 90, 120))
                kind = rng.choices(
                    (
                        "completed",
                        "cancelled",
                        "rejected_then_done",
                        "all_rejected",
                        "urgent_done",
                    ),
                    weights=(58, 10, 14, 8, 10),
                )[0]
                client = rng.choice(clients)
                count += 1
                if kind in ("completed", "urgent_done", "rejected_then_done"):
                    actual_start = planned + timedelta(minutes=rng.choice((0, 5, 10)))
                    actual_end = actual_start + timedelta(
                        minutes=duration + rng.choice((-10, 0, 15))
                    )
                    result = rng.choice(RESULT_CODES)
                    card = insert_card(
                        cursor,
                        index=index,
                        client_id=client,
                        status=CardStatus.COMPLETED,
                        planned_start=planned,
                        duration=duration,
                        l1_id=l1,
                        l2_id=l2,
                        created_at=created,
                        result_code=result,
                        urgent=(kind == "urgent_done"),
                        out_of_hours=(planned.astimezone(ZoneInfo(DEMO_TZ)).hour >= 18),
                        actual=(actual_start, actual_end),
                    )
                    add_event(
                        cursor,
                        card,
                        CardEventType.CREATED,
                        created,
                        l1,
                        ActorType.INTERNAL_USER,
                        {"status_code": int(CardStatus.CREATED)},
                    )
                    cyc = cycle(
                        cursor,
                        card,
                        AssignmentCycleStatus.ASSIGNED,
                        created,
                        created + timedelta(minutes=20),
                    )
                    when = created + timedelta(minutes=5)
                    if kind == "rejected_then_done":
                        attempt(
                            cursor,
                            card,
                            cyc,
                            second_l2,
                            AssignmentAttemptStatus.REJECTED,
                            when,
                            when + timedelta(minutes=8),
                            "DEMO: нет возможности",
                        )
                        status_event(
                            cursor,
                            card,
                            when + timedelta(minutes=8),
                            second_l2,
                            CardStatus.REJECTED,
                        )
                        when += timedelta(minutes=10)
                    attempt(
                        cursor,
                        card,
                        cyc,
                        l2,
                        AssignmentAttemptStatus.CONFIRMED,
                        when,
                        when + timedelta(minutes=6),
                    )
                    add_event(
                        cursor,
                        card,
                        CardEventType.ENGINEER_ASSIGNED,
                        when,
                        None,
                        ActorType.SYSTEM,
                        {"l2_engineer_id": l2},
                    )
                    status_event(cursor, card, when, None, CardStatus.ASSIGNED)
                    status_event(
                        cursor,
                        card,
                        when + timedelta(minutes=6),
                        l2,
                        CardStatus.CONFIRMED,
                    )
                    status_event(cursor, card, actual_start, l2, CardStatus.IN_PROGRESS)
                    status_event(cursor, card, actual_end, l2, CardStatus.COMPLETED)
                    if actual_end > planned + timedelta(minutes=duration):
                        status_event(
                            cursor,
                            card,
                            planned + timedelta(minutes=duration),
                            None,
                            CardStatus.IN_PROGRESS,
                            {"overdue": True},
                        )
                elif kind == "cancelled":
                    card = insert_card(
                        cursor,
                        index=index,
                        client_id=client,
                        status=CardStatus.CANCELLED,
                        planned_start=planned,
                        duration=duration,
                        l1_id=l1,
                        l2_id=None,
                        created_at=created,
                    )
                    add_event(
                        cursor,
                        card,
                        CardEventType.CREATED,
                        created,
                        l1,
                        ActorType.INTERNAL_USER,
                        {"status_code": int(CardStatus.CREATED)},
                    )
                    status_event(
                        cursor,
                        card,
                        created + timedelta(minutes=45),
                        l1,
                        CardStatus.CANCELLED,
                    )
                else:  # all_rejected
                    card = insert_card(
                        cursor,
                        index=index,
                        client_id=client,
                        status=CardStatus.REJECTED,
                        planned_start=planned,
                        duration=duration,
                        l1_id=l1,
                        l2_id=None,
                        created_at=created,
                        unsuccessful=2,
                    )
                    add_event(
                        cursor,
                        card,
                        CardEventType.CREATED,
                        created,
                        l1,
                        ActorType.INTERNAL_USER,
                        {"status_code": int(CardStatus.CREATED)},
                    )
                    cyc = cycle(
                        cursor,
                        card,
                        AssignmentCycleStatus.ALL_REJECTED,
                        created,
                        created + timedelta(minutes=30),
                    )
                    for n, engineer in enumerate(l2_ids[:2]):
                        at = created + timedelta(minutes=5 + 10 * n)
                        attempt(
                            cursor,
                            card,
                            cyc,
                            engineer,
                            AssignmentAttemptStatus.REJECTED,
                            at,
                            at + timedelta(minutes=6),
                            "DEMO: занят на другом подключении",
                        )
                        status_event(
                            cursor,
                            card,
                            at + timedelta(minutes=6),
                            engineer,
                            CardStatus.REJECTED,
                        )

        # Upcoming cards on different engineers so that active cards never overlap.
        start_day = today + timedelta(days=1)
        for n, status in enumerate(
            (CardStatus.ASSIGNED, CardStatus.CONFIRMED, CardStatus.CONFIRMED)
        ):
            index += 1
            day = start_day + timedelta(days=n)
            while day.isoweekday() > 5:
                day += timedelta(days=1)
            l2 = l2_ids[n % len(l2_ids)]
            created = datetime.now(UTC) - timedelta(hours=2 + n)
            planned = local_dt(day, time(11 + n, 0))
            card = insert_card(
                cursor,
                index=index,
                client_id=clients[n],
                status=status,
                planned_start=planned,
                duration=60,
                l1_id=l1_ids[0],
                l2_id=l2,
                created_at=created,
            )
            add_event(
                cursor,
                card,
                CardEventType.CREATED,
                created,
                l1_ids[0],
                ActorType.INTERNAL_USER,
                {"status_code": int(CardStatus.CREATED)},
            )
            status_event(
                cursor, card, created + timedelta(minutes=5), None, CardStatus.ASSIGNED
            )
            if status == CardStatus.CONFIRMED:
                status_event(
                    cursor,
                    card,
                    created + timedelta(minutes=20),
                    l2,
                    CardStatus.CONFIRMED,
                )
            count += 1
    return count


def seed(connection, password: str, days: int) -> dict[str, int]:
    repo = PostgresAdminUserRepository(connection)
    user_ids: dict[str, int] = {}
    created_users = 0
    with connection.cursor() as cursor:
        for username, full_name, role, _kind in DEMO_USERS:
            cursor.execute("SELECT id FROM users WHERE username = %s", (username,))
            existing = cursor.fetchone()
            if existing:
                user_ids[username] = existing["id"]
                continue
            try:
                user = repo.create_user(
                    username=username,
                    password=password,
                    full_name=full_name,
                    email=None,
                    phone=None,
                    omnidesk_staff_id=None,
                    roles=[int(role)],
                    is_active=True,
                    actor_user_id=None,
                    ip_address=None,
                    user_agent="demo-seed",
                )
            except UsernameConflictError:
                continue
            user_ids[username] = user.id
            created_users += 1
        today = datetime.now(UTC).date()
        for username, _name, role, kind in DEMO_USERS:
            user_id = user_ids.get(username)
            if user_id is None or kind == "none":
                continue
            cursor.execute("DELETE FROM schedules WHERE user_id = %s", (user_id,))
            for offset in range(-days - 5, 21):
                day = today + timedelta(days=offset)
                shift = schedule_day(kind, day)
                if shift:
                    cursor.execute(
                        """
                        INSERT INTO schedules (user_id, weekday, start_time, end_time, timezone, valid_from, valid_to)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            day.isoweekday(),
                            shift[0],
                            shift[1],
                            DEMO_TZ,
                            day,
                            day,
                        ),
                    )
            cursor.execute(
                """
                INSERT INTO distribution_members (user_id, pool_code, is_enabled, enabled_at, comment)
                VALUES (%s, %s, true, now(), 'DEMO: включён для демонстрации')
                ON CONFLICT (user_id, pool_code) DO NOTHING
                """,
                (user_id, 1 if role == RoleId.L1 else 2),
            )
    l1_ids = [
        user_ids[u] for u, _n, r, _k in DEMO_USERS if r == RoleId.L1 and u in user_ids
    ]
    l2_ids = [
        user_ids[u] for u, _n, r, _k in DEMO_USERS if r == RoleId.L2 and u in user_ids
    ]
    cards = 0
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS n FROM connection_cards WHERE description LIKE %s",
            (f"{DESCRIPTION_PREFIX}%",),
        )
        already = cursor.fetchone()["n"]
    if not already and l1_ids and len(l2_ids) >= 2:
        cards = seed_cards(connection, l1_ids, l2_ids, days)
    return {"users": created_users, "cards": cards}


def main(argv: Sequence[str] | None = None, stdin=None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic demo data for the test stand"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--apply", action="store_true", help="create demo users, schedules and cards"
    )
    mode.add_argument(
        "--remove", action="store_true", help="delete exactly the demo records"
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the demo users' password from stdin",
    )
    parser.add_argument(
        "--days", type=int, default=30, help="how many past days of cards to create"
    )
    args = parser.parse_args(argv)
    if args.remove:
        with db_connection() as connection:
            removed = remove_demo(connection)
        print(f"Removed demo data: {removed}")
        return 0
    if not args.password_stdin:
        print("ERROR: --apply needs --password-stdin", file=sys.stderr)
        return 2
    try:
        password = read_password(stdin or sys.stdin)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    with db_connection() as connection:
        result = seed(connection, password, max(1, min(args.days, 90)))
    print(f"Demo data ready: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import os

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.api.reports import get_reports_service, router
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import RoleId
from app.reports.service import PostgresReportsRepository, ReportsService

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


def _setup_test_app(connection: psycopg.Connection, user: UserAuthRecord) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    reports_service = ReportsService(PostgresReportsRepository(connection))
    app.dependency_overrides[get_reports_service] = lambda: reports_service
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_reports_summary_postgres() -> None:
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        try:
            with connection.cursor() as cursor:
                # 1. Create test users
                # Manager
                cursor.execute(
                    "INSERT INTO users (id, username, password_hash, full_name, is_active) "
                    "VALUES (99901, 'rep-mgr', 'test', 'Report Manager', true)"
                )
                cursor.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (99901, %s)",
                    (int(RoleId.MANAGER),),
                )
                # L2 Engineer 1
                cursor.execute(
                    "INSERT INTO users (id, username, password_hash, full_name, is_active) "
                    "VALUES (99904, 'rep-l2-1', 'test', 'Engineer One', true)"
                )
                cursor.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (99904, %s)",
                    (int(RoleId.L2),),
                )
                # L2 Engineer 2
                cursor.execute(
                    "INSERT INTO users (id, username, password_hash, full_name, is_active) "
                    "VALUES (99905, 'rep-l2-2', 'test', 'Engineer Two', true)"
                )
                cursor.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (99905, %s)",
                    (int(RoleId.L2),),
                )
                # Inactive L2
                cursor.execute(
                    "INSERT INTO users (id, username, password_hash, full_name, is_active) "
                    "VALUES (99906, 'rep-l2-inact', 'test', 'Inactive Engineer', false)"
                )
                cursor.execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (99906, %s)",
                    (int(RoleId.L2),),
                )

                # Period: 2026-03-01T00:00:00Z to 2026-03-10T00:00:00Z
                # Card 1: Created inside (2026-03-02), Completed inside (2026-03-03), assigned to L2 1 (60 min)
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes,
                     l2_engineer_id, created_at)
                    VALUES ('999-000001', 5, '2026-03-03T10:00:00Z', 60, 99904, '2026-03-02T10:00:00Z')
                    RETURNING id
                    """
                )
                c1_id = cursor.fetchone()["id"]
                cursor.execute(
                    "INSERT INTO assignment_cycles (card_id, cycle_number, status_code) VALUES (%s, 1, 1) RETURNING id",
                    (c1_id,),
                )
                cycle1_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO assignment_attempts (cycle_id, card_id, l2_engineer_id, status_code, assigned_at)
                    VALUES (%s, %s, 99904, 1, '2026-03-02T10:05:00Z')
                    """,
                    (cycle1_id, c1_id),
                )
                cursor.execute(
                    """
                    INSERT INTO card_events (card_id, event_type_code, actor_type_code, created_at, new_values)
                    VALUES (%s, 1, 0, '2026-03-03T11:00:00Z', %s)
                    """,
                    (c1_id, Jsonb({"status_code": 5})),
                )

                # Card 2: Created inside (2026-03-03), Rejected inside (2026-03-04), 1st unsuccessful cycle
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes,
                     unsuccessful_cycle_count, created_at)
                    VALUES ('999-000002', 4, '2026-03-04T10:00:00Z', 60, 1, '2026-03-03T10:00:00Z')
                    RETURNING id
                    """
                )
                c2_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO card_events (card_id, event_type_code, actor_type_code, created_at, new_values, comment)
                    VALUES (%s, 1, 2, '2026-03-04T10:05:00Z', %s, 'all_l2_candidates_rejected: busy')
                    """,
                    (c2_id, Jsonb({"status_code": 4})),
                )

                # Card 3: Created inside (2026-03-04), Rejected inside (2026-03-05), 2nd unsuccessful cycle
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes,
                     unsuccessful_cycle_count, created_at)
                    VALUES ('999-000003', 4, '2026-03-05T10:00:00Z', 60, 2, '2026-03-04T10:00:00Z')
                    RETURNING id
                    """
                )
                c3_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO card_events (card_id, event_type_code, actor_type_code, created_at, new_values, comment)
                    VALUES (%s, 1, 2, '2026-03-05T10:05:00Z', %s, 'all_l2_candidates_rejected: busy')
                    """,
                    (c3_id, Jsonb({"status_code": 4})),
                )

                # Card 4: Created inside (2026-03-05), Urgent with urgent_reason, assigned to L2 1 (120 min)
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes,
                     urgency_code, urgent_reason, l2_engineer_id, created_at)
                    VALUES ('999-000004', 1, '2026-03-06T10:00:00Z', 120, 1, 'Critical server outage', 99904, '2026-03-05T10:00:00Z')
                    RETURNING id
                    """
                )
                c4_id = cursor.fetchone()["id"]
                cursor.execute(
                    "INSERT INTO assignment_cycles (card_id, cycle_number, status_code) VALUES (%s, 1, 1) RETURNING id",
                    (c4_id,),
                )
                cycle4_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO assignment_attempts (cycle_id, card_id, l2_engineer_id, status_code, assigned_at)
                    VALUES (%s, %s, 99904, 0, '2026-03-05T10:01:00Z')
                    """,
                    (cycle4_id, c4_id),
                )

                # Card 5: Created inside (2026-03-06), Overdue in period
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes,
                     overdue_flag, created_at)
                    VALUES ('999-000005', 1, '2026-03-06T12:00:00Z', 60, true, '2026-03-06T10:00:00Z')
                    RETURNING id
                    """
                )
                c5_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO card_events (card_id, event_type_code, actor_type_code, created_at, new_values, comment)
                    VALUES (%s, 1, 2, '2026-03-06T13:00:00Z', %s, 'l2_overdue')
                    """,
                    (c5_id, Jsonb({"overdue": True})),
                )

                # Card 6: Created inside (2026-03-07), Urgent collision event in period
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes, created_at)
                    VALUES ('999-000006', 1, '2026-03-07T10:00:00Z', 60, '2026-03-07T10:00:00Z')
                    RETURNING id
                    """
                )
                c6_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO card_events (card_id, event_type_code, actor_type_code, created_at, comment)
                    VALUES (%s, 10, 2, '2026-03-07T10:15:00Z', 'urgent_collision')
                    """,
                    (c6_id,),
                )

                # Card 7: Created BEFORE period (2026-02-20), Completed INSIDE period (2026-03-02)
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes, created_at)
                    VALUES ('999-000007', 5, '2026-03-02T09:00:00Z', 60, '2026-02-20T10:00:00Z')
                    RETURNING id
                    """
                )
                c7_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO card_events (card_id, event_type_code, actor_type_code, created_at, new_values)
                    VALUES (%s, 1, 0, '2026-03-02T10:00:00Z', %s)
                    """,
                    (c7_id, Jsonb({"status_code": 5})),
                )

                # Card 8: Created AFTER period (2026-03-15), not in period
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes, created_at)
                    VALUES ('999-000008', 0, '2026-03-16T10:00:00Z', 60, '2026-03-15T10:00:00Z')
                    """
                )

                # Boundary Card 9: Created at exact start (2026-03-01T00:00:00Z - INCLUDED)
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes, created_at)
                    VALUES ('999-000009', 0, '2026-03-01T10:00:00Z', 60, '2026-03-01T00:00:00Z')
                    """
                )

                # Boundary Card 10: Created at exact end (2026-03-10T00:00:00Z - EXCLUDED)
                cursor.execute(
                    """
                    INSERT INTO connection_cards
                    (omnidesk_ticket_number, status_code, planned_start_at, planned_duration_minutes, created_at)
                    VALUES ('999-000010', 0, '2026-03-10T10:00:00Z', 60, '2026-03-10T00:00:00Z')
                    """
                )

            manager_user = UserAuthRecord(
                id=99901,
                username="rep-mgr",
                password_hash="unused",
                full_name="Report Manager",
                email=None,
                roles=(RoleRecord(id=int(RoleId.MANAGER), name=RoleId.MANAGER.name),),
            )
            client = _setup_test_app(connection, manager_user)

            # Test 1: GET /summary
            summary_resp = client.get(
                "/api/v1/reports/summary?from=2026-03-01T00:00:00Z&to=2026-03-10T00:00:00Z"
            )
            assert summary_resp.status_code == 200
            data = summary_resp.json()
            # created = cards 1, 2, 3, 4, 5, 6, 9 (total 7)
            assert data["created"] == 7
            # completed = cards 1 and 7 (total 2)
            assert data["completed"] == 2
            # rejected_share = 2 / 7
            assert data["rejected_share"]["numerator"] == 2
            assert data["rejected_share"]["denominator"] == 7
            assert data["rejected_share"]["value"] == 0.2857
            # repeat_rejected_share = 1 / 2
            assert data["repeat_rejected_share"]["numerator"] == 1
            assert data["repeat_rejected_share"]["denominator"] == 2
            assert data["repeat_rejected_share"]["value"] == 0.5
            # overdue = card 5 (1)
            assert data["overdue"]["count"] == 1
            # urgent = card 4 (1)
            assert data["urgent"]["count"] == 1
            # urgent_collisions = card 6 (1)
            assert data["urgent_collisions"]["count"] == 1

            # Test 2: GET /overdue
            overdue_resp = client.get(
                "/api/v1/reports/overdue?from=2026-03-01T00:00:00Z&to=2026-03-10T00:00:00Z"
            )
            assert overdue_resp.status_code == 200
            overdue_data = overdue_resp.json()
            assert overdue_data["total"] == 1
            assert len(overdue_data["items"]) == 1
            c5_number = connection.execute(
                "SELECT number FROM connection_cards WHERE id = %s", (c5_id,)
            ).fetchone()["number"]
            assert overdue_data["items"][0]["number"] == c5_number
            assert overdue_data["items"][0]["status"] == "assigned"
            assert "case_id" not in overdue_resp.text

            # Test 3: GET /l2-load
            l2_resp = client.get(
                "/api/v1/reports/l2-load?from=2026-03-01T00:00:00Z&to=2026-03-10T00:00:00Z"
            )
            assert l2_resp.status_code == 200
            l2_items = l2_resp.json()["items"]
            # Active L2 engineers: Engineer One (99904) and Engineer Two (99905)
            # Inactive 99906 is excluded, Manager 99901 is excluded
            returned_ids = {item["user_id"] for item in l2_items}
            assert {99904, 99905} <= returned_ids
            assert 99906 not in returned_ids
            assert 99901 not in returned_ids
            eng1 = next(item for item in l2_items if item["user_id"] == 99904)
            assert eng1["assigned"] == 2  # Cards 1 and 4
            assert eng1["completed"] == 1  # Card 1
            assert eng1["planned_minutes"] == 180  # 60 + 120

            eng2 = next(item for item in l2_items if item["user_id"] == 99905)
            assert eng2["assigned"] == 0
            assert eng2["completed"] == 0
            assert eng2["planned_minutes"] == 0
            assert "case_id" not in l2_resp.text

        finally:
            connection.rollback()

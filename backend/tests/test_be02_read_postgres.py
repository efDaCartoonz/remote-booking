from __future__ import annotations

import os
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.api.cards import get_card_repository
from app.auth.dependencies import get_current_user
from app.auth.store import RoleRecord, UserAuthRecord
from app.cards.constants import RoleId
from app.cards.repository import PostgresCardRepository
from app.main import create_app

pytestmark = pytest.mark.skipif(
    os.getenv("RDM_PG_INTEGRATION") != "1", reason="PostgreSQL integration runner only"
)


def test_card_and_history_reads_use_business_roles_not_admin_level() -> None:
    database_url = os.getenv("PSYCOPG_DATABASE_URL") or os.environ[
        "DATABASE_URL"
    ].replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        try:
            with connection.cursor() as cursor:
                for user_id in (99801, 99802, 99803):
                    cursor.execute(
                        "INSERT INTO users (id, username, password_hash, full_name) "
                        "VALUES (%s, %s, 'test', %s)",
                        (user_id, f"be02-{user_id}", f"BE02 {user_id}"),
                    )
                cursor.execute(
                    "INSERT INTO connection_cards "
                    "(omnidesk_ticket_number, status_code, planned_start_at, "
                    "planned_duration_minutes, l1_owner_id, l2_engineer_id, description) "
                    "VALUES ('998-000001', 1, %s, 60, 99801, 99802, 'private BE02 text') "
                    "RETURNING id, public_id",
                    (datetime(2030, 1, 1, tzinfo=UTC),),
                )
                card = cursor.fetchone()
                cursor.execute(
                    "INSERT INTO card_events "
                    "(card_id, event_type_code, actor_type_code) VALUES (%s, 1, 2)",
                    (card["id"],),
                )

            actor = {"id": 99803, "roles": (RoleId.ADMIN,)}
            app = create_app()
            app.dependency_overrides[get_card_repository] = (
                lambda: PostgresCardRepository(connection)
            )
            app.dependency_overrides[get_current_user] = lambda: UserAuthRecord(
                id=actor["id"],
                username="be02-reader",
                password_hash="unused",
                full_name="BE02 Reader",
                email=None,
                roles=tuple(
                    RoleRecord(id=int(role), name=role.name) for role in actor["roles"]
                ),
            )
            client = TestClient(app)
            for suffix in ("", "/history"):
                actor.update(id=99803, roles=(RoleId.ADMIN,))
                path = f"/api/v1/cards/{card['public_id']}{suffix}"
                denied = client.get(path)
                assert denied.status_code == 403
                assert denied.json() == {"detail": "action_forbidden"}
                assert "998-000001" not in denied.text
                assert "private BE02 text" not in denied.text

                for user_id, roles in (
                    (99801, (RoleId.L1,)),
                    (99802, (RoleId.L2,)),
                    (99803, (RoleId.L1,)),
                    (99803, (RoleId.L2,)),
                    (99803, (RoleId.MANAGER,)),
                ):
                    actor.update(id=user_id, roles=roles)
                    allowed = client.get(path)
                    assert allowed.status_code == 200
                    assert "case_id" not in allowed.text
        finally:
            connection.rollback()

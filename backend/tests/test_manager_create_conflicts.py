from types import SimpleNamespace

import pytest
from app import manager_create


class FakeExclusionViolation(Exception):
    pass


class FakeUniqueViolation(Exception):
    pass


def run_with_error(monkeypatch, error_type, constraint_name):
    monkeypatch.setattr(manager_create, "ExclusionViolation", FakeExclusionViolation)
    monkeypatch.setattr(manager_create, "UniqueViolation", FakeUniqueViolation)
    error = error_type("database failure")
    if constraint_name is not None:
        error.diag = SimpleNamespace(constraint_name=constraint_name)
    rolled_back = []

    def operation():
        raise error

    return manager_create.run_manager_create_transaction(
        operation, rollback=lambda: rolled_back.append(True)
    ), rolled_back


def test_known_l2_exclusion_becomes_safe_conflict(monkeypatch) -> None:
    with pytest.raises(
        manager_create.ManagerCreateConflictError, match="l2_assignment_conflict"
    ):
        run_with_error(
            monkeypatch,
            FakeExclusionViolation,
            "ex_connection_cards_l2_no_overlap",
        )


def test_known_active_ticket_unique_becomes_safe_conflict(monkeypatch) -> None:
    with pytest.raises(
        manager_create.ManagerCreateConflictError, match="active_card_exists_for_ticket"
    ):
        run_with_error(
            monkeypatch,
            FakeUniqueViolation,
            "ux_connection_cards_one_active_per_ticket",
        )


@pytest.mark.parametrize(
    "error_type, constraint_name",
    [
        (FakeExclusionViolation, "unexpected_constraint"),
        (FakeExclusionViolation, None),
        (FakeUniqueViolation, "unexpected_constraint"),
        (FakeUniqueViolation, None),
    ],
)
def test_unknown_or_undecorated_database_error_is_not_masked(
    monkeypatch, error_type, constraint_name
) -> None:
    with pytest.raises(error_type, match="database failure"):
        run_with_error(monkeypatch, error_type, constraint_name)

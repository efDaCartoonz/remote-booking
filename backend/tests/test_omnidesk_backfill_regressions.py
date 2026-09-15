from datetime import UTC, datetime, timedelta

from app.frame.omnidesk import OmnideskCaseList
from app.omnidesk_index.backfill import BackfillOptions, run_backfill
from app.omnidesk_index.repository import CaseIndexItem, CaseIndexValidationError

NOW = datetime.now(UTC)


class FakeConnection:
    def __init__(self):
        self.operations = []

    def execute(self, sql):
        self.operations.append(sql)

    def commit(self):
        self.operations.append("COMMIT")


class FakeRepository:
    def __init__(self, connection):
        self.saved = []
        self.errors = []

    def get_checkpoint(self, name):
        return None

    def upsert(self, item):
        if item.case_id == "invalid":
            raise CaseIndexValidationError("invalid_case_record")
        self.saved.append(item.case_id)
        return "upserted"

    def record_error(self, code):
        self.errors.append(code)

    def save_checkpoint(self, **kwargs):
        self.checkpoint = kwargs


class FakeClient:
    def __init__(self, items):
        self.items = items

    def list_cases(self, **kwargs):
        return OmnideskCaseList(self.items, len(self.items))


def item(case_id):
    return CaseIndexItem(
        case_id, f"123-{case_id}", "user", "open", False, False, NOW, NOW
    )


def options():
    return BackfillOptions(NOW, NOW + timedelta(days=1), 1, 100, 1)


def test_invalid_row_isolated_and_valid_row_saved(monkeypatch):
    repository = FakeRepository(None)
    monkeypatch.setattr(
        "app.omnidesk_index.backfill.CaseIndexRepository", lambda _: repository
    )
    connection = FakeConnection()
    result = run_backfill(
        connection, FakeClient([item("valid"), item("invalid")]), options()
    )
    assert repository.saved == ["valid"]
    assert repository.errors == ["invalid_case_record"]
    assert result["conflicts"] == 1
    assert repository.checkpoint["page"] == 2


def test_invalid_first_row_does_not_block_valid_second(monkeypatch):
    repository = FakeRepository(None)
    monkeypatch.setattr(
        "app.omnidesk_index.backfill.CaseIndexRepository", lambda _: repository
    )
    run_backfill(
        FakeConnection(), FakeClient([item("invalid"), item("valid")]), options()
    )
    assert repository.saved == ["valid"]


def test_dry_run_has_no_repository_writes(monkeypatch):
    repository = FakeRepository(None)
    monkeypatch.setattr(
        "app.omnidesk_index.backfill.CaseIndexRepository", lambda _: repository
    )
    result = run_backfill(
        FakeConnection(),
        FakeClient([item("valid")]),
        BackfillOptions(**{**options().__dict__, "dry_run": True}),
    )
    assert repository.saved == []
    assert repository.errors == []
    assert result["records"] == 1

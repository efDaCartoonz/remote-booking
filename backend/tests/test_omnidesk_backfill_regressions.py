from datetime import UTC, datetime, timedelta

from app.frame.omnidesk import OmnideskCaseList
from app.omnidesk_index.backfill import BackfillOptions, run_backfill
from app.omnidesk_index.repository import CaseIndexItem, CaseIndexValidationError

NOW = datetime.now(UTC)


class CheckpointCursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.sql = sql
        self.params = params


class CheckpointConnection:
    def __init__(self):
        self.cursor_instance = CheckpointCursor()

    def cursor(self):
        return self.cursor_instance


class FakeConnection:
    def __init__(self):
        self.operations = []

    def execute(self, sql):
        self.operations.append(sql)

    def commit(self):
        self.operations.append("COMMIT")


class CheckpointFailure(Exception):
    pass


class CommitFailure(Exception):
    pass


class ReleasedSavepointRollback(Exception):
    pass


class FakeRepository:
    def __init__(self, connection, checkpoint_failure=False):
        self.saved = []
        self.errors = []
        self.checkpoint_failure = checkpoint_failure

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
        if self.checkpoint_failure:
            raise CheckpointFailure("checkpoint_failed")
        self.checkpoint = kwargs


class FakeClient:
    def __init__(self, items):
        self.items = items

    def list_cases(self, **kwargs):
        return OmnideskCaseList(self.items, len(self.items))


class CommitFailureConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self.page_released = False

    def execute(self, sql):
        if sql == "RELEASE SAVEPOINT backfill_page":
            self.page_released = True
        if sql == "ROLLBACK TO SAVEPOINT backfill_page" and self.page_released:
            raise ReleasedSavepointRollback("savepoint_already_released")
        super().execute(sql)

    def commit(self):
        self.operations.append("COMMIT")
        raise CommitFailure("commit_failed")


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


def test_checkpoint_failure_rolls_back_page_and_does_not_commit(monkeypatch):
    repository = FakeRepository(None, checkpoint_failure=True)
    monkeypatch.setattr(
        "app.omnidesk_index.backfill.CaseIndexRepository", lambda _: repository
    )
    connection = FakeConnection()

    try:
        run_backfill(connection, FakeClient([item("valid")]), options())
    except CheckpointFailure:
        pass
    else:
        raise AssertionError("checkpoint failure must propagate")

    assert repository.saved == ["valid"]
    assert not hasattr(repository, "checkpoint")
    assert connection.operations == [
        "SAVEPOINT backfill_page",
        "SAVEPOINT backfill_item_0",
        "RELEASE SAVEPOINT backfill_item_0",
        "ROLLBACK TO SAVEPOINT backfill_page",
        "RELEASE SAVEPOINT backfill_page",
    ]


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


def test_successful_page_saves_checkpoint_after_page_records(monkeypatch):
    repository = FakeRepository(None)
    monkeypatch.setattr(
        "app.omnidesk_index.backfill.CaseIndexRepository", lambda _: repository
    )
    connection = FakeConnection()

    run_backfill(connection, FakeClient([item("valid")]), options())

    assert repository.saved == ["valid"]
    assert repository.checkpoint["page"] == 2
    assert connection.operations[-2:] == ["RELEASE SAVEPOINT backfill_page", "COMMIT"]


def test_commit_failure_preserves_original_error(monkeypatch):
    repository = FakeRepository(None)
    monkeypatch.setattr(
        "app.omnidesk_index.backfill.CaseIndexRepository", lambda _: repository
    )
    connection = CommitFailureConnection()

    try:
        run_backfill(connection, FakeClient([item("valid")]), options())
    except CommitFailure:
        pass
    except ReleasedSavepointRollback as exc:
        raise AssertionError("commit failure was masked by rollback") from exc
    else:
        raise AssertionError("commit failure must propagate")

    assert repository.saved == ["valid"]
    assert repository.checkpoint["page"] == 2
    assert connection.operations == [
        "SAVEPOINT backfill_page",
        "SAVEPOINT backfill_item_0",
        "RELEASE SAVEPOINT backfill_item_0",
        "RELEASE SAVEPOINT backfill_page",
        "COMMIT",
    ]


def test_checkpoint_types_nullable_error_parameter(monkeypatch):
    from app.omnidesk_index.repository import CaseIndexRepository

    connection = CheckpointConnection()
    CaseIndexRepository(connection).save_checkpoint(
        name="manual_backfill",
        window_from=NOW,
        window_to=NOW + timedelta(days=1),
        page=2,
        pages=1,
        total=1,
        error=None,
    )
    assert "CASE WHEN %s::text IS NULL" in connection.cursor_instance.sql
    assert connection.cursor_instance.params[-2:] == (None, None)

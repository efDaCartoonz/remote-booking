from datetime import UTC, datetime

import pytest

import app.omnidesk_index.lookup as lookup
import app.omnidesk_index.resolver as resolver
from app.frame.omnidesk import OmnideskCaseList, OmnideskTicket
from app.omnidesk_index.repository import CaseIndexItem, CaseIndexTicketNotFound


def _item(case_id: str, number: str) -> CaseIndexItem:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    return CaseIndexItem(case_id, number, "client-1", "open", False, False, now, now)


class Connection:
    def __init__(self):
        self.statements = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.statements.append(sql)

    def commit(self):
        self.commits += 1


class PagedClient:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def list_cases(self, *, page, limit, sort, from_updated_time=None, **_):
        self.requests.append((page, limit, sort, from_updated_time))
        total = sum(len(p) for p in self.pages)
        return OmnideskCaseList(items=self.pages[page - 1], total_count=total)


class FakeRepo:
    stored: list[CaseIndexItem] = []

    def __init__(self, connection):
        self.connection = connection

    def upsert(self, item):
        FakeRepo.stored.append(item)
        return "upserted"

    def record_error(self, code):
        raise AssertionError(code)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    FakeRepo.stored = []
    lookup.reset_miss_cache()
    monkeypatch.setattr(lookup, "CaseIndexRepository", FakeRepo)
    yield
    lookup.reset_miss_cache()


def _refresh(client, number="416-108096", max_pages=5):
    return lookup.refresh_recent_cases(
        Connection(),
        client,
        case_number=number,
        days=7,
        page_size=2,
        max_pages=max_pages,
    )


def test_scan_stops_after_the_page_with_the_number():
    client = PagedClient(
        [
            [_item("1", "100-000001"), _item("2", "100-000002")],
            [_item("3", "416-108096"), _item("4", "100-000004")],
            [_item("5", "100-000005")],
        ]
    )
    assert _refresh(client) is True
    assert [r[0] for r in client.requests] == [1, 2]
    assert [i.case_id for i in FakeRepo.stored] == ["1", "2", "3", "4"]
    assert client.requests[0][3] is not None


def test_miss_is_bounded_and_cached():
    client = PagedClient([[_item("1", "100-000001")], [_item("2", "100-000002")]])
    assert _refresh(client, max_pages=1) is False
    assert len(client.requests) == 1
    assert _refresh(client, max_pages=1) is False
    assert len(client.requests) == 1


def test_scan_ends_when_pages_are_exhausted():
    client = PagedClient([[_item("1", "100-000001")]])
    assert _refresh(client) is False
    assert len(client.requests) == 1


class TicketClient:
    def get_ticket_by_case_id(self, case_id):
        return OmnideskTicket(case_id, "416-108096", "client-1", "open")

    def reopen_ticket(self, case_id):
        raise AssertionError("open ticket must not be reopened")


class IndexRepo:
    known: dict[str, str] = {}

    def __init__(self, connection):
        pass

    def resolve_case_id(self, number):
        if number not in IndexRepo.known:
            raise CaseIndexTicketNotFound
        return IndexRepo.known[number]


def test_resolver_uses_lazy_lookup_only_when_enabled(monkeypatch):
    IndexRepo.known = {}
    monkeypatch.setattr(resolver, "CaseIndexRepository", IndexRepo)
    calls = []

    def fake_refresh(connection, client, **kwargs):
        calls.append(kwargs["case_number"])
        IndexRepo.known["416-108096"] = "42"
        return True

    monkeypatch.setattr(resolver, "refresh_recent_cases", fake_refresh)

    monkeypatch.setattr(resolver.settings, "omnidesk_case_lazy_lookup_enabled", False)
    with pytest.raises(resolver.PublicTicketResolutionError) as disabled:
        resolver.resolve_ticket_by_case_number(None, TicketClient(), "416-108096")
    assert disabled.value.status_code == 404
    assert calls == []

    monkeypatch.setattr(resolver.settings, "omnidesk_case_lazy_lookup_enabled", True)
    ticket = resolver.resolve_ticket_by_case_number(None, TicketClient(), "416-108096")
    assert ticket.number == "416-108096"
    assert calls == ["416-108096"]


def test_resolver_keeps_404_when_scan_finds_nothing(monkeypatch):
    IndexRepo.known = {}
    monkeypatch.setattr(resolver, "CaseIndexRepository", IndexRepo)
    monkeypatch.setattr(resolver, "refresh_recent_cases", lambda *a, **k: False)
    monkeypatch.setattr(resolver.settings, "omnidesk_case_lazy_lookup_enabled", True)
    with pytest.raises(resolver.PublicTicketResolutionError) as missing:
        resolver.resolve_ticket_by_case_number(None, TicketClient(), "416-108096")
    assert missing.value.status_code == 404
    assert missing.value.detail == "omnidesk_ticket_not_found"

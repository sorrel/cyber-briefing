"""Tests for collectors/ico_scraper.py.

On 4 Nov 2026 the ICO search API answered some requests with zero results:
roughly 1 in 6 at random, uncached, whatever the client, and for a spell that
morning every "newest" request (the site's own default) while "oldest" still
returned all 229. One empty answer cost the weekly scrape its whole week.
Results had also lost their
``date`` field: the date now lives in ``filterItemMetaData`` ("27 October
2026, Reprimands, ...") and ``createdDateTime``. The result dicts below
follow the live response shape.
"""

import pytest

from cyberbriefing.collectors import ico_scraper as scraper


def _result(n, day, title=None, url=None):
    return {
        "filterItemMetaData": f"{day} October 2026, Reprimands, Criminal justice",
        "filterItemDecisions": [],
        "title": title or f"Organisation {n}",
        "createdDateTime": f"2026-10-{day:02d}T16:16:54.653Z",
        "id": 127000 + n,
        "url": url or f"/action-weve-taken/enforcement/2026/10/org-{n}/",
        "contentTypeAlias": "filterItem",
        "description": f"Organisation {n} has been issued with a Reprimand.",
        "theme": "red",
        "order": ["title", "filterItemMetaData", "description", "filterItemDecisions"],
    }


def _page(results, total, page, per_page=25):
    pages = -(-total // per_page) if total else 0
    return {
        "results": results,
        "pagination": {"totalResults": total, "perPage": per_page,
                       "showingPageNumber": page, "totalPages": pages,
                       "hasMore": page < pages},
    }


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


@pytest.fixture
def api(monkeypatch):
    """Serve canned pages keyed by (order, pageNumber); record each request.

    A value may be a list of payloads, served in turn, to script a flaky
    empty answer followed by a good one.
    """
    pages: dict[tuple[str, int], dict | list[dict]] = {}
    calls: list[dict] = []

    def fake_post(url, json=None, **kwargs):
        calls.append(json)
        payload = pages.get((json["order"], json["pageNumber"]), _page([], 0, 1))
        if isinstance(payload, list):
            payload = payload.pop(0) if len(payload) > 1 else payload[0]
        return _Resp(payload)

    monkeypatch.setattr(scraper.requests, "post", fake_post)
    monkeypatch.setattr(scraper.time, "sleep", lambda seconds: None)
    return pages, calls


def test_newest_order_is_used_when_it_works(api):
    pages, calls = api
    pages[("newest", 1)] = _page([_result(2, 27), _result(1, 19)], total=2, page=1)

    items = scraper.collect()

    assert [i["title"] for i in items] == ["Organisation 2", "Organisation 1"]
    assert [c["order"] for c in calls] == ["newest"]


def test_falls_back_to_the_last_oldest_pages_when_newest_is_empty(api):
    # 29 results over 2 pages: page 2 of "oldest" holds the newest 4.
    pages, calls = api
    pages[("oldest", 1)] = _page([_result(n, 1) for n in range(25)], total=29, page=1)
    pages[("oldest", 2)] = _page([_result(n, n - 20) for n in range(25, 29)], total=29, page=2)

    items = scraper.collect()

    titles = [i["title"] for i in items]
    assert len(items) == 29
    assert titles[0] == "Organisation 28", "newest first"
    assert [c["order"] for c in calls][:3] == ["newest"] * 3


def test_fallback_reads_at_most_two_pages(api):
    pages, calls = api
    for p in range(1, 11):
        pages[("oldest", p)] = _page([_result(p * 100 + n, 1) for n in range(25)],
                                     total=229, page=p)

    items = scraper.collect()

    requested = [(c["order"], c["pageNumber"]) for c in calls]
    # newest (empty on every attempt), oldest p1 for the page count, then the
    # last two pages.
    assert requested == [("newest", 1)] * 3 + [("oldest", 1), ("oldest", 10), ("oldest", 9)]
    assert len(items) == 50
    assert items[0]["title"] == "Organisation 1024"


def test_date_comes_from_the_metadata_line(api):
    pages, _ = api
    pages[("newest", 1)] = _page([_result(1, 27)], total=1, page=1)

    item = scraper.collect()[0]

    assert item["published"].startswith("2026-10-27")


def test_date_falls_back_to_created_datetime(api):
    pages, _ = api
    r = _result(1, 27)
    r["filterItemMetaData"] = "Reprimands, Criminal justice"
    r["createdDateTime"] = "2026-10-26T09:00:00Z"
    pages[("newest", 1)] = _page([r], total=1, page=1)

    assert scraper.collect()[0]["published"].startswith("2026-10-26")


def test_relative_urls_are_made_absolute(api):
    pages, _ = api
    pages[("newest", 1)] = _page([_result(1, 27)], total=1, page=1)

    url = scraper.collect()[0]["url"]

    assert url == "https://ico.org.uk/action-weve-taken/enforcement/2026/10/org-1/"


def test_off_domain_urls_are_dropped(api):
    pages, _ = api
    pages[("newest", 1)] = _page([
        _result(1, 27),
        _result(2, 27, url="https://ico.org.uk.evil.example/x"),
        _result(3, 27, url="https://evil.example/x"),
    ], total=3, page=1)

    assert [i["title"] for i in scraper.collect()] == ["Organisation 1"]


def test_everything_empty_returns_empty(api):
    assert scraper.collect() == []


def test_network_failure_returns_empty_not_raises(monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")

    monkeypatch.setattr(scraper.requests, "post", boom)
    assert scraper.collect() == []


def test_a_flaky_empty_answer_is_retried(api):
    pages, calls = api
    pages[("newest", 1)] = [_page([], 0, 1), _page([_result(1, 27)], total=1, page=1)]

    items = scraper.collect()

    assert [i["title"] for i in items] == ["Organisation 1"]
    assert [c["order"] for c in calls] == ["newest", "newest"]


def test_retries_are_spaced_by_whole_seconds(api, monkeypatch):
    slept: list[int] = []
    monkeypatch.setattr(scraper.time, "sleep", slept.append)

    scraper.collect()

    # 3 attempts each for newest and oldest page 1: two gaps per order.
    assert slept == [scraper._RETRY_GAP_SECONDS] * 4
    assert all(isinstance(s, int) and s >= 1 for s in slept)


def test_everything_empty_gives_up_after_bounded_attempts(api):
    _, calls = api

    assert scraper.collect() == []
    assert [c["order"] for c in calls] == ["newest"] * 3 + ["oldest"] * 3

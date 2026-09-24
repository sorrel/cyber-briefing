"""Tests for collectors/owasp_news_scraper.py.

owasp.org moved to a Next.js site in September 2026 and its Jekyll
``/feed.xml`` began returning 404. The ``/news`` page is rendered client-side
from ``/api/public/news/list``, so the collector reads that JSON directly. The
fixture is a trimmed capture of the real response (24 Sep 2026): the first 10
posts, with ``content`` and the author fields removed.
"""

import json
import logging
from pathlib import Path

import pytest

from cyberbriefing.collectors import owasp_news_scraper as scraper

FIXTURE = Path(__file__).parent / "fixtures" / "owasp_news.json"


class _FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return json.loads(self.text)


def _serve(monkeypatch, text, status=200):
    """Serve ``text`` to the collector and hand back the request it made."""
    calls = {}

    def fake_get(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return _FakeResponse(text, status)

    monkeypatch.setattr(scraper.requests, "get", fake_get)
    return calls


def _serve_posts(monkeypatch, posts):
    return _serve(monkeypatch, json.dumps({"posts": posts}))


def _post(slug="a-post", title="A post", **overrides):
    """One post in the API's own shape, trimmed to the fields that matter."""
    post = {
        "slug": slug,
        "title": title,
        "excerpt": "An excerpt.",
        "category": "News",
        "tags": [],
        "status": "published",
        "published_at": "2026-09-22T17:26:00+00:00",
    }
    post.update(overrides)
    return post


@pytest.fixture
def page(monkeypatch):
    return _serve(monkeypatch, FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The captured response
# ---------------------------------------------------------------------------


def test_collects_every_published_post(page):
    assert len(scraper.collect()) == 10


def test_urls_are_the_news_article_pages(page):
    urls = [i["url"] for i in scraper.collect()]

    # The site's own JS links posts as `news/${slug}`; this one returns 200.
    assert "https://owasp.org/news/owasp-vulnerableapp-builds-a-benchmark" in urls
    assert all(u.startswith("https://owasp.org/news/") for u in urls)


def test_titles_come_through_untouched(page):
    titles = [i["title"] for i in scraper.collect()]

    assert "OWASP Dependency-Track 5.0 Is Now Generally Available" in titles


def test_published_timestamp_is_kept(page):
    by_url = {i["url"]: i for i in scraper.collect()}
    item = by_url["https://owasp.org/news/owasp-vulnerableapp-builds-a-benchmark"]

    assert item["published"] == "2026-09-22T17:26:00+00:00"


def test_snippet_carries_category_and_excerpt(page):
    by_url = {i["url"]: i for i in scraper.collect()}
    snippet = by_url["https://owasp.org/news/owasp-vulnerableapp-builds-a-benchmark"]["snippet"]

    # The category is what separates a project release from foundation news,
    # so the scorer needs it alongside the excerpt.
    assert "Projects" in snippet
    assert "how good they are" in snippet


def test_item_shape(page):
    item = scraper.collect()[0]

    assert item["source"] == "owasp_news"
    assert item["category"] == "research"
    assert item["id"]


# ---------------------------------------------------------------------------
# Fetch behaviour
# ---------------------------------------------------------------------------


def test_requests_the_news_api_with_a_timeout(page):
    scraper.collect()

    assert page["url"] == scraper.OWASP_NEWS_API_URL
    assert page["url"].startswith("https://owasp.org/api/public/news/list")
    assert page["kwargs"]["timeout"] > 0
    assert "User-Agent" in page["kwargs"]["headers"]


def test_network_failure_returns_empty_not_raises(monkeypatch):
    def boom(url, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(scraper.requests, "get", boom)
    assert scraper.collect() == []


def test_http_error_returns_empty(monkeypatch):
    _serve(monkeypatch, "Not Found", status=404)
    assert scraper.collect() == []


def test_html_page_instead_of_json_returns_empty(monkeypatch):
    """The Next.js catch-all answers unknown paths with a 200 HTML shell.

    That is exactly what /news/feed.xml returns today, so an API move would
    look like success at the HTTP layer and must fail at the parse instead.
    """
    _serve(monkeypatch, "<!DOCTYPE html><html><body>Loading news content…</body></html>")
    assert scraper.collect() == []


@pytest.mark.parametrize(
    "body",
    ['[]', '{}', '{"posts": null}', '{"posts": "nope"}', '{"data": []}'],
)
def test_unexpected_json_shape_returns_empty(monkeypatch, body):
    _serve(monkeypatch, body)
    assert scraper.collect() == []


def test_zero_posts_logs_a_warning(monkeypatch, caplog):
    """No per-scraper zero-item alarm exists, so the collector raises its own."""
    _serve_posts(monkeypatch, [])

    with caplog.at_level(logging.WARNING, logger="cyberbriefing.collectors.owasp_news"):
        assert scraper.collect() == []

    assert any("0 OWASP news posts" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Per-post guards
# ---------------------------------------------------------------------------


def test_skips_posts_that_are_not_published(monkeypatch):
    _serve_posts(
        monkeypatch,
        [_post("live", "Live"), _post("draft", "Draft", status="draft")],
    )

    assert [i["title"] for i in scraper.collect()] == ["Live"]


@pytest.mark.parametrize(
    "slug",
    [
        "../../api/auth/roles",
        "https://evil.example/post",
        "//evil.example/post",
        "nested/path",
        "has space",
        "query?x=1",
        "frag#x",
        "",
        None,
        42,
    ],
)
def test_rejects_slugs_that_are_not_a_single_path_segment(monkeypatch, slug):
    """The slug is spliced into a URL, so anything but a plain slug is dropped.

    A traversal or absolute-URL slug would otherwise mint a link to another
    path or another host under the OWASP name.
    """
    _serve_posts(monkeypatch, [_post("real-post", "Real"), _post(slug, "Hostile")])

    items = scraper.collect()

    assert [i["title"] for i in items] == ["Real"]
    assert items[0]["url"] == "https://owasp.org/news/real-post"


def test_skips_posts_without_a_title(monkeypatch):
    _serve_posts(
        monkeypatch,
        [_post("kept", "Kept"), _post("blank", "   "), _post("none", None)],
    )

    assert [i["title"] for i in scraper.collect()] == ["Kept"]


def test_skips_non_object_entries_without_dropping_the_rest(monkeypatch):
    _serve_posts(monkeypatch, [None, "junk", 7, _post("kept", "Kept")])

    assert [i["title"] for i in scraper.collect()] == ["Kept"]


def test_unparseable_date_still_yields_the_post(monkeypatch):
    _serve_posts(monkeypatch, [_post("kept", "Kept", published_at="last Tuesday")])

    items = scraper.collect()

    assert [i["title"] for i in items] == ["Kept"]
    assert items[0]["published"]


def test_duplicate_slugs_collapse_to_one_item(monkeypatch):
    _serve_posts(monkeypatch, [_post("same", "First"), _post("same", "Second")])

    assert len(scraper.collect()) == 1

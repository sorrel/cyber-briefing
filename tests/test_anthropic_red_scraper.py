"""Tests for collectors/anthropic_red_scraper.py.

The scraper silently returned zero items for its whole life: it looked for the
``<a><h3>`` markup of the old red.anthropic.com, which now 301s to
www.anthropic.com/research/team/frontier-red-team and renders its posts as a
``PublicationList`` of ``<a>`` rows carrying a ``<time>``. The fixture is a
trimmed capture of the real page (8 Sep 2026), kept deliberately noisy so the
parser has to reject nav links, the featured card and the footer.
"""

from pathlib import Path

import pytest

from cyberbriefing.collectors import anthropic_red_scraper as scraper

FIXTURE = Path(__file__).parent / "fixtures" / "anthropic_red.html"
FINAL_URL = "https://www.anthropic.com/research/team/frontier-red-team"


class _FakeResponse:
    def __init__(self, text, url=FINAL_URL, status=200):
        self.text = text
        self.url = url
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def page(monkeypatch):
    """Serve the captured page to the scraper and hand back the request kwargs."""
    calls = {}
    html = FIXTURE.read_text(encoding="utf-8")

    def fake_get(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return _FakeResponse(html)

    monkeypatch.setattr(scraper.requests, "get", fake_get)
    return calls


# ---------------------------------------------------------------------------
# The regression: real markup must yield real items
# ---------------------------------------------------------------------------


def test_collects_every_publication(page):
    # The captured list holds 10 posts, and its 10 <time> elements are exactly
    # those rows: the other 17 anchors (nav, featured card, footer) carry none,
    # which is what the "anchor wrapping a <time>" test keys off. The live page
    # has the same 10-and-only-10 shape.
    items = scraper.collect()
    assert len(items) == 10


def test_titles_exclude_the_date_and_subject_chrome(page):
    titles = [i["title"] for i in scraper.collect()]

    assert "Patterns and problems in emerging multiagent systems" in titles
    assert "Discovering cryptographic weaknesses with Claude" in titles
    # The row's text is "Aug 13, 2026 / Frontier Red Team / <title>", and none of
    # that meta may bleed into the title.
    for title in titles:
        assert "Frontier Red Team" not in title
        assert "2026" not in title


def test_urls_are_absolute_against_the_redirect_target(page):
    urls = [i["url"] for i in scraper.collect()]

    assert "https://www.anthropic.com/research/multiagent-systems" in urls
    # The dead red.anthropic.com host must not be re-fabricated onto paths.
    assert all(u.startswith("https://www.anthropic.com/") for u in urls)


def test_rejects_nav_featured_and_footer_links(page):
    urls = [i["url"] for i in scraper.collect()]

    assert "https://www.anthropic.com/research" not in urls
    assert "https://www.anthropic.com/research/team/alignment" not in urls
    assert "https://www.anthropic.com/careers" not in urls
    # The featured "Read more" card points at a post that IS in the list; it
    # must appear exactly once, from the list row that carries a date.
    assert urls.count("https://www.anthropic.com/research/project-fetch-phase-two") == 1


def test_published_dates_are_parsed_to_iso(page):
    by_url = {i["url"]: i for i in scraper.collect()}
    item = by_url["https://www.anthropic.com/research/multiagent-systems"]

    assert item["published"].startswith("2026-08-13")


def test_item_shape(page):
    item = scraper.collect()[0]

    assert item["source"] == "anthropic_red"
    assert item["category"] == "research"
    assert item["id"]
    assert item["snippet"]


# ---------------------------------------------------------------------------
# Fetch behaviour
# ---------------------------------------------------------------------------


def test_requests_the_current_url_with_a_timeout(page):
    scraper.collect()

    assert page["url"] == scraper.ANTHROPIC_RED_URL
    assert page["kwargs"]["timeout"] > 0
    assert "User-Agent" in page["kwargs"]["headers"]


def test_network_failure_returns_empty_not_raises(monkeypatch):
    def boom(url, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(scraper.requests, "get", boom)
    assert scraper.collect() == []


def test_survives_the_title_class_being_renamed(monkeypatch):
    """A cosmetic class rename must degrade the title, not drop the post.

    The CSS-module hash changes on every Anthropic deploy; a rename of the
    stable ``__title`` local name is the next-most-likely breakage.
    """
    html = FIXTURE.read_text(encoding="utf-8").replace("__title", "__renamed")
    monkeypatch.setattr(
        scraper.requests, "get", lambda url, **kwargs: _FakeResponse(html)
    )

    titles = [i["title"] for i in scraper.collect()]

    assert len(titles) == 10
    assert "Patterns and problems in emerging multiagent systems" in titles
    for title in titles:
        assert "Frontier Red Team" not in title
        assert "2026" not in title


def test_unrecognised_markup_returns_empty(monkeypatch):
    monkeypatch.setattr(
        scraper.requests,
        "get",
        lambda url, **kwargs: _FakeResponse("<html><body><p>nothing</p></body></html>"),
    )
    assert scraper.collect() == []


# ---------------------------------------------------------------------------
# Guards the captured page cannot exercise
#
# The real page has neither an orphan <time> nor an off-domain publication row,
# so keeping the fixture faithful means driving these two from minimal markup
# rather than salting the capture with elements Anthropic never served.
# ---------------------------------------------------------------------------


def _serve(monkeypatch, html):
    monkeypatch.setattr(
        scraper.requests, "get", lambda url, **kwargs: _FakeResponse(html)
    )


def _row(href, title="A post", date="Aug 13, 2026"):
    """One publication row in the page's own markup."""
    return (
        f'<li><a class="PublicationList-module-scss-module__KxYrHG__listItem" '
        f'href="{href}">'
        f'<div class="PublicationList-module-scss-module__KxYrHG__meta">'
        f'<time class="PublicationList-module-scss-module__KxYrHG__date">{date}</time>'
        f'<span class="PublicationList-module-scss-module__KxYrHG__subject">'
        f"Frontier Red Team</span></div>"
        f'<span class="PublicationList-module-scss-module__KxYrHG__title">'
        f"{title}</span></a></li>"
    )


def test_ignores_a_time_outside_any_anchor(monkeypatch):
    """A bare <time> is page furniture, not a post.

    The parser's whole selection rule is "an anchor wrapping a <time>". A
    footer build stamp is the obvious way that rule could start over-matching
    if the anchor half were ever dropped.
    """
    _serve(
        monkeypatch,
        "<body><ul>"
        + _row("/research/real-post", title="Real post")
        + "</ul><footer><time>Sep 8, 2026</time>Last updated</footer></body>",
    )

    items = scraper.collect()

    assert [i["title"] for i in items] == ["Real post"]


def test_rejects_rows_pointing_off_anthropic_com(monkeypatch):
    """host_matches must drop a row whose href leaves the domain.

    A lookalike host is the reason the collector uses host_matches rather than
    a substring test, so the rejection needs a test of its own.
    """
    _serve(
        monkeypatch,
        "<body><ul>"
        + _row("https://www.anthropic.com/research/real-post", title="Real post")
        + _row("https://anthropic.com.evil.example/post", title="Lookalike host")
        + _row("https://notanthropic.com/post", title="Suffix collision")
        + _row("https://evil.example/post", title="Unrelated host")
        + "</ul></body>",
    )

    items = scraper.collect()

    assert [i["title"] for i in items] == ["Real post"]


def test_accepts_rows_on_anthropic_subdomains(monkeypatch):
    """The flip side: host_matches allows the apex and real subdomains."""
    _serve(
        monkeypatch,
        "<body><ul>"
        + _row("https://www.anthropic.com/research/a", title="On www")
        + _row("https://anthropic.com/research/b", title="On the apex")
        + "</ul></body>",
    )

    assert len(scraper.collect()) == 2

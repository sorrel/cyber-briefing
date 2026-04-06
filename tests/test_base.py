"""Tests for collectors/base.py shared utilities."""

import time
from datetime import datetime, timezone

import pytest

from collectors.base import (
    USER_AGENT_BOT,
    USER_AGENT_BROWSER,
    make_item,
    parse_feedparser_date,
    strip_utm,
    truncate,
)


# ---------------------------------------------------------------------------
# USER_AGENT constants
# ---------------------------------------------------------------------------

class TestUserAgents:
    def test_bot_ua_is_string(self):
        assert isinstance(USER_AGENT_BOT, str)
        assert "CyberBriefingBot" in USER_AGENT_BOT

    def test_browser_ua_is_string(self):
        assert isinstance(USER_AGENT_BROWSER, str)
        assert "Mozilla" in USER_AGENT_BROWSER

    def test_ua_are_distinct(self):
        assert USER_AGENT_BOT != USER_AGENT_BROWSER


# ---------------------------------------------------------------------------
# strip_utm
# ---------------------------------------------------------------------------

class TestStripUtm:
    def test_strips_utm_source(self):
        url = "https://example.com/article?utm_source=newsletter&id=42"
        assert strip_utm(url) == "https://example.com/article?id=42"

    def test_strips_multiple_utm_params(self):
        url = "https://example.com/?utm_source=x&utm_medium=y&utm_campaign=z&keep=1"
        assert strip_utm(url) == "https://example.com/?keep=1"

    def test_no_query_string_unchanged(self):
        url = "https://example.com/page"
        assert strip_utm(url) == url

    def test_non_utm_params_preserved(self):
        url = "https://example.com/?foo=bar&baz=qux"
        assert strip_utm(url) == url

    def test_all_params_are_utm(self):
        url = "https://example.com/?utm_source=x&utm_medium=y"
        result = strip_utm(url)
        assert "utm_" not in result

    def test_empty_query_unchanged(self):
        url = "https://example.com/?"
        # No utm params to strip, result should be unchanged
        assert "utm_" not in strip_utm(url)


# ---------------------------------------------------------------------------
# parse_feedparser_date
# ---------------------------------------------------------------------------

class TestParseFeedparserDate:
    # feedparser provides published_parsed as a local-time struct_time.
    # mktime() in parse_feedparser_date interprets it as local time, so we must
    # use time.localtime(ts) in tests so mktime round-trips to the same timestamp.

    def _local_struct(self, timestamp: int):
        return time.localtime(timestamp)

    def test_parses_published_parsed(self):
        ts = 86400  # 1970-01-02 00:00:00 UTC
        entry = {"published_parsed": self._local_struct(ts)}
        result = parse_feedparser_date(entry)
        expected = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert abs((datetime.fromisoformat(result) - expected).total_seconds()) < 2

    def test_falls_back_to_updated_parsed(self):
        ts = 86400
        entry = {"updated_parsed": self._local_struct(ts)}
        result = parse_feedparser_date(entry)
        expected = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert abs((datetime.fromisoformat(result) - expected).total_seconds()) < 2

    def test_prefers_published_over_updated(self):
        ts_pub = 86400
        ts_upd = 86400 * 2
        entry = {
            "published_parsed": self._local_struct(ts_pub),
            "updated_parsed": self._local_struct(ts_upd),
        }
        result = parse_feedparser_date(entry)
        expected_pub = datetime.fromtimestamp(ts_pub, tz=timezone.utc)
        expected_upd = datetime.fromtimestamp(ts_upd, tz=timezone.utc)
        result_dt = datetime.fromisoformat(result)
        assert abs((result_dt - expected_pub).total_seconds()) < 2
        assert abs((result_dt - expected_upd).total_seconds()) > 60

    def test_empty_entry_returns_now(self):
        before = datetime.now(timezone.utc)
        result = parse_feedparser_date({})
        after = datetime.now(timezone.utc)
        dt = datetime.fromisoformat(result)
        assert before <= dt <= after

    def test_none_field_falls_back(self):
        ts = 86400
        entry = {"published_parsed": None, "updated_parsed": self._local_struct(ts)}
        result = parse_feedparser_date(entry)
        expected = datetime.fromtimestamp(ts, tz=timezone.utc)
        assert abs((datetime.fromisoformat(result) - expected).total_seconds()) < 2

    def test_returns_iso_8601_string(self):
        entry = {"published_parsed": self._local_struct(86400)}
        result = parse_feedparser_date(entry)
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# make_item
# ---------------------------------------------------------------------------

class TestMakeItem:
    def test_required_fields(self):
        item = make_item(source="test", title="Title", url="https://example.com")
        assert item["source"] == "test"
        assert item["title"] == "Title"
        assert item["url"] == "https://example.com"

    def test_id_is_deterministic(self):
        item1 = make_item(source="s", title="t", url="https://example.com")
        item2 = make_item(source="s", title="t", url="https://example.com")
        assert item1["id"] == item2["id"]

    def test_id_differs_by_url(self):
        item1 = make_item(source="s", title="t", url="https://a.com")
        item2 = make_item(source="s", title="t", url="https://b.com")
        assert item1["id"] != item2["id"]

    def test_snippet_truncated_to_500(self):
        long_snippet = "x" * 600
        item = make_item(source="s", title="t", url="https://example.com", snippet=long_snippet)
        assert len(item["snippet"]) <= 500

    def test_whitespace_stripped_from_title_and_url(self):
        item = make_item(source="s", title="  Title  ", url="  https://example.com  ")
        assert item["title"] == "Title"
        assert item["url"] == "https://example.com"

    def test_extra_field_included(self):
        item = make_item(source="s", title="t", url="https://example.com", extra={"cve": "CVE-2025-1234"})
        assert item["extra"]["cve"] == "CVE-2025-1234"

    def test_no_extra_field_absent(self):
        item = make_item(source="s", title="t", url="https://example.com")
        assert "extra" not in item


# ---------------------------------------------------------------------------
# truncate
# ---------------------------------------------------------------------------

class TestTruncate:
    def test_short_text_unchanged(self):
        assert truncate("hello") == "hello"

    def test_long_text_truncated(self):
        result = truncate("word " * 200, max_len=50)
        assert len(result) <= 50

    def test_adds_ellipsis(self):
        result = truncate("word " * 200, max_len=50)
        assert result.endswith("…")

    def test_empty_string(self):
        assert truncate("") == ""

    def test_none(self):
        assert truncate(None) == ""

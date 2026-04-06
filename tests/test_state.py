"""Tests for db/state.py — state database functions."""

import os
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

from db.state import (
    get_connection,
    is_seen,
    filter_unseen,
    mark_seen,
    mark_seen_batch,
    should_check_scraper,
    update_scraper_run,
    clear_source,
    prune_old_unseen,
    get_stats,
)


@pytest.fixture
def conn():
    """Temporary in-memory-equivalent DB for each test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    c = get_connection(path)
    yield c
    c.close()
    os.unlink(path)


def _make_item(item_id: str, source: str = "test_src") -> dict:
    return {
        "id": item_id,
        "source": source,
        "title": f"Title {item_id}",
        "url": f"https://example.com/{item_id}",
    }


# ---------------------------------------------------------------------------
# is_seen / mark_seen
# ---------------------------------------------------------------------------

class TestIsSeenMarkSeen:
    def test_unseen_item_returns_false(self, conn):
        assert not is_seen(conn, "abc123")

    def test_seen_after_mark(self, conn):
        mark_seen(conn, "abc123", "src")
        assert is_seen(conn, "abc123")

    def test_mark_seen_idempotent(self, conn):
        mark_seen(conn, "abc123", "src")
        mark_seen(conn, "abc123", "src")  # should not raise
        assert is_seen(conn, "abc123")


# ---------------------------------------------------------------------------
# filter_unseen
# ---------------------------------------------------------------------------

class TestFilterUnseen:
    def test_all_unseen(self, conn):
        items = [_make_item("a"), _make_item("b")]
        result = filter_unseen(conn, items)
        assert len(result) == 2

    def test_filters_seen_items(self, conn):
        mark_seen(conn, "a", "src")
        items = [_make_item("a"), _make_item("b")]
        result = filter_unseen(conn, items)
        assert len(result) == 1
        assert result[0]["id"] == "b"

    def test_empty_input(self, conn):
        assert filter_unseen(conn, []) == []

    def test_all_seen(self, conn):
        for i in ["a", "b", "c"]:
            mark_seen(conn, i, "src")
        items = [_make_item(i) for i in ["a", "b", "c"]]
        assert filter_unseen(conn, items) == []


# ---------------------------------------------------------------------------
# mark_seen_batch
# ---------------------------------------------------------------------------

class TestMarkSeenBatch:
    def test_marks_multiple_items(self, conn):
        items = [_make_item("x"), _make_item("y"), _make_item("z")]
        mark_seen_batch(conn, items, included=True)
        assert all(is_seen(conn, i["id"]) for i in items)

    def test_included_flag_stored(self, conn):
        items = [_make_item("p")]
        mark_seen_batch(conn, items, included=True)
        row = conn.execute("SELECT included_in_briefing FROM seen_items WHERE item_id='p'").fetchone()
        assert row[0] == 1

    def test_excluded_flag_stored(self, conn):
        items = [_make_item("q")]
        mark_seen_batch(conn, items, included=False)
        row = conn.execute("SELECT included_in_briefing FROM seen_items WHERE item_id='q'").fetchone()
        assert row[0] == 0

    def test_empty_batch_no_error(self, conn):
        mark_seen_batch(conn, [], included=False)  # should not raise


# ---------------------------------------------------------------------------
# should_check_scraper / update_scraper_run
# ---------------------------------------------------------------------------

class TestScraperSchedule:
    def test_new_scraper_should_run(self, conn):
        assert should_check_scraper(conn, "my_scraper", interval_hours=24)

    def test_just_run_should_not_run(self, conn):
        update_scraper_run(conn, "my_scraper")
        assert not should_check_scraper(conn, "my_scraper", interval_hours=24)

    def test_old_run_should_run_again(self, conn):
        # Manually insert a last_checked time 25 hours ago
        old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO scraper_runs (source, last_checked) VALUES (?, ?)",
            ("stale_scraper", old_time),
        )
        conn.commit()
        assert should_check_scraper(conn, "stale_scraper", interval_hours=24)


# ---------------------------------------------------------------------------
# clear_source
# ---------------------------------------------------------------------------

class TestClearSource:
    def test_clears_only_target_source(self, conn):
        mark_seen_batch(conn, [_make_item("a", "source_a"), _make_item("b", "source_a")])
        mark_seen_batch(conn, [_make_item("c", "source_b")])
        removed = clear_source(conn, "source_a")
        assert removed == 2
        assert not is_seen(conn, "a")
        assert not is_seen(conn, "b")
        assert is_seen(conn, "c")  # untouched

    def test_clear_nonexistent_source(self, conn):
        assert clear_source(conn, "does_not_exist") == 0

    def test_clear_returns_count(self, conn):
        mark_seen_batch(conn, [_make_item(str(i), "src") for i in range(5)])
        assert clear_source(conn, "src") == 5


# ---------------------------------------------------------------------------
# prune_old_unseen
# ---------------------------------------------------------------------------

class TestPruneOldUnseen:
    def _insert(self, conn, item_id, source, days_ago, included):
        date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO seen_items VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, source, "title", "url", date, 1 if included else 0),
        )
        conn.commit()

    def test_removes_old_unseen(self, conn):
        self._insert(conn, "old_unseen", "src", days_ago=200, included=False)
        removed = prune_old_unseen(conn, days=180)
        assert removed == 1
        assert not is_seen(conn, "old_unseen")

    def test_keeps_old_included(self, conn):
        self._insert(conn, "old_included", "src", days_ago=200, included=True)
        removed = prune_old_unseen(conn, days=180)
        assert removed == 0
        assert is_seen(conn, "old_included")

    def test_keeps_recent_unseen(self, conn):
        self._insert(conn, "recent_unseen", "src", days_ago=10, included=False)
        removed = prune_old_unseen(conn, days=180)
        assert removed == 0
        assert is_seen(conn, "recent_unseen")

    def test_mixed_batch(self, conn):
        self._insert(conn, "old_unseen",   "src", days_ago=200, included=False)
        self._insert(conn, "old_included", "src", days_ago=200, included=True)
        self._insert(conn, "new_unseen",   "src", days_ago=10,  included=False)
        removed = prune_old_unseen(conn, days=180)
        assert removed == 1
        assert not is_seen(conn, "old_unseen")
        assert is_seen(conn, "old_included")
        assert is_seen(conn, "new_unseen")

    def test_empty_db_no_error(self, conn):
        assert prune_old_unseen(conn, days=180) == 0


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

class TestGetStats:
    def test_empty_db(self, conn):
        stats = get_stats(conn)
        assert stats["total_items_seen"] == 0
        assert stats["total_included"] == 0
        assert stats["by_source"] == {}

    def test_counts_by_source(self, conn):
        mark_seen_batch(conn, [_make_item(str(i), "ncsc") for i in range(3)], included=True)
        mark_seen_batch(conn, [_make_item(str(i + 10), "cisa") for i in range(2)], included=False)
        stats = get_stats(conn)
        assert stats["total_items_seen"] == 5
        assert stats["total_included"] == 3
        assert stats["by_source"]["ncsc"] == 3
        assert stats["by_source"]["cisa"] == 2

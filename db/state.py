"""SQLite state database for tracking seen items and scraper schedules."""

import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB_PATH = os.path.expanduser("~/.cyberbriefing/state.db")


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Get a database connection, creating the directory and tables if needed."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _ensure_tables(conn)
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS seen_items (
            item_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT,
            url TEXT,
            first_seen TEXT NOT NULL,
            included_in_briefing INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS scraper_runs (
            source TEXT PRIMARY KEY,
            last_checked TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_seen_source ON seen_items(source);
        CREATE INDEX IF NOT EXISTS idx_seen_date ON seen_items(first_seen);
    """)
    conn.commit()


def is_seen(conn: sqlite3.Connection, item_id: str) -> bool:
    """Check if an item has already been processed."""
    row = conn.execute(
        "SELECT 1 FROM seen_items WHERE item_id = ?", (item_id,)
    ).fetchone()
    return row is not None


def filter_unseen(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """Return only items not yet in the database — single query regardless of input size."""
    if not items:
        return []
    ids = [item["id"] for item in items]
    placeholders = ",".join("?" * len(ids))
    seen = {
        row[0]
        for row in conn.execute(
            f"SELECT item_id FROM seen_items WHERE item_id IN ({placeholders})", ids
        )
    }
    return [item for item in items if item["id"] not in seen]


def mark_seen(
    conn: sqlite3.Connection,
    item_id: str,
    source: str,
    title: str = "",
    url: str = "",
    included: bool = False,
) -> None:
    """Record an item as seen."""
    conn.execute(
        """INSERT OR IGNORE INTO seen_items
           (item_id, source, title, url, first_seen, included_in_briefing)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            item_id,
            source,
            title,
            url,
            datetime.now(timezone.utc).isoformat(),
            1 if included else 0,
        ),
    )
    conn.commit()


def mark_seen_batch(
    conn: sqlite3.Connection, items: list[dict], included: bool = False
) -> None:
    """Record multiple items as seen in a single transaction."""
    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        """INSERT OR IGNORE INTO seen_items
           (item_id, source, title, url, first_seen, included_in_briefing)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (
                item["id"],
                item.get("source", ""),
                item.get("title", ""),
                item.get("url", ""),
                now,
                1 if included else 0,
            )
            for item in items
        ],
    )
    conn.commit()


def should_check_scraper(
    conn: sqlite3.Connection, source: str, interval_hours: int
) -> bool:
    """Check whether a scraper is due to run based on its configured interval."""
    row = conn.execute(
        "SELECT last_checked FROM scraper_runs WHERE source = ?", (source,)
    ).fetchone()
    if row is None:
        return True
    last = datetime.fromisoformat(row["last_checked"])
    elapsed = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    return elapsed >= interval_hours


def update_scraper_run(conn: sqlite3.Connection, source: str) -> None:
    """Record that a scraper has just run."""
    conn.execute(
        """INSERT OR REPLACE INTO scraper_runs (source, last_checked)
           VALUES (?, ?)""",
        (source, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_stats(conn: sqlite3.Connection) -> dict:
    """Return basic statistics about the database."""
    total = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
    included = conn.execute(
        "SELECT COUNT(*) FROM seen_items WHERE included_in_briefing = 1"
    ).fetchone()[0]
    sources = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM seen_items GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    return {
        "total_items_seen": total,
        "total_included": included,
        "by_source": {row["source"]: row["cnt"] for row in sources},
    }

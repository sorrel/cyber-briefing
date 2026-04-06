"""Generic RSS/Atom feed collector.

Handles all feeds listed under sources.rss_feeds in config.yaml.
Supports optional keyword filtering (e.g. for Parliament news).
"""

import logging
import re
from datetime import datetime, timezone
from time import mktime
from typing import Optional

import feedparser

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.rss")


def collect(feed_config: dict) -> list[dict]:
    """Collect items from a single RSS/Atom feed.

    Args:
        feed_config: Dict with keys: url, source_name, category,
                     and optional keyword_filter (list of strings).

    Returns:
        List of standardised item dicts.
    """
    url = feed_config["url"]
    source_name = feed_config.get("source_name", url)
    category = feed_config.get("category", "advisory")
    keywords = feed_config.get("keyword_filter")
    max_entries = feed_config.get("max_entries")  # optional cap on number of entries

    logger.info("Fetching RSS: %s (%s)", source_name, url)

    try:
        feed = feedparser.parse(url)
    except Exception as e:
        logger.error("Failed to parse feed %s: %s", url, e)
        return []

    if feed.bozo and not feed.entries:
        logger.warning("Feed %s returned bozo with no entries: %s", url, feed.bozo_exception)
        return []

    entries = feed.entries
    if max_entries:
        entries = entries[:max_entries]

    items = []
    for entry in entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()

        if not title or not link:
            continue

        # Apply keyword filter if configured
        if keywords and not _matches_keywords(entry, keywords):
            continue

        published = _parse_date(entry)
        snippet = _extract_snippet(entry)

        items.append(
            make_item(
                source=_slugify(source_name),
                title=title,
                url=link,
                snippet=truncate(snippet),
                category=category,
                published=published,
            )
        )

    logger.info("Collected %d items from %s", len(items), source_name)
    return items


def _matches_keywords(entry: dict, keywords: list[str]) -> bool:
    """Check whether an entry's title or summary contains any of the keywords."""
    text = (
        entry.get("title", "") + " " + entry.get("summary", "")
    ).lower()
    return any(kw.lower() in text for kw in keywords)


def _parse_date(entry: dict) -> str:
    """Extract a published date from a feed entry as ISO-8601."""
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                dt = datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
                return dt.isoformat()
            except (ValueError, OverflowError):
                pass
    return datetime.now(timezone.utc).isoformat()


def _extract_snippet(entry: dict) -> str:
    """Pull a text snippet from an entry's summary or content."""
    # Prefer summary, fall back to content
    text = entry.get("summary", "")
    if not text and "content" in entry:
        for content_block in entry["content"]:
            if content_block.get("value"):
                text = content_block["value"]
                break

    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _slugify(name: str) -> str:
    """Convert a source name to a simple slug."""
    return name.lower().replace(" ", "_").replace("-", "_")

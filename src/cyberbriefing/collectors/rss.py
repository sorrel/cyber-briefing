"""Generic RSS/Atom feed collector.

Handles all feeds listed under sources.rss_feeds in config.yaml.
Supports optional keyword filtering (e.g. for Parliament news).
"""

import logging
import re

import feedparser
import requests

from .base import USER_AGENT_BOT, make_item, parse_feedparser_date, truncate

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
    max_entries = feed_config.get("max_entries")

    logger.info("Fetching RSS: %s (%s)", source_name, url)

    # Fetch with requests so we can enforce a timeout — feedparser.parse(url)
    # has no timeout and a hung feed would stall the whole pipeline.
    try:
        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": USER_AGENT_BOT},
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
    except requests.Timeout:
        logger.warning("Feed %s timed out after 30s — skipping", source_name)
        return []
    except Exception as e:
        logger.error("Failed to fetch/parse feed %s: %s", source_name, e)
        return []

    if feed.bozo and not feed.entries:
        logger.warning("Feed %s returned bozo with no entries: %s", source_name, feed.bozo_exception)
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

        if keywords and not _matches_keywords(entry, keywords):
            continue

        published = parse_feedparser_date(entry)
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
    text = (entry.get("title", "") + " " + entry.get("summary", "")).lower()
    return any(kw.lower() in text for kw in keywords)


def _extract_snippet(entry: dict) -> str:
    """Pull a text snippet from an entry's summary or content."""
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

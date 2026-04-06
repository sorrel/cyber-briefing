"""Base collector: common schema, ID generation, and shared utilities."""

import hashlib
import logging
from datetime import datetime, timezone
from time import mktime
from typing import Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger("cyberbriefing.collectors")

# ---------------------------------------------------------------------------
# User-Agent constants
# Some sites (e.g. CloudSecList, Aikido) return 403 to generic Python UAs,
# so those scrapers use USER_AGENT_BROWSER. Everything else uses USER_AGENT_BOT.
# ---------------------------------------------------------------------------

USER_AGENT_BOT = "CyberBriefingBot/1.0 (personal security briefing tool)"

USER_AGENT_BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def make_item(
    source: str,
    title: str,
    url: str,
    snippet: str = "",
    category: str = "advisory",
    published: Optional[str] = None,
    extra: Optional[dict] = None,
) -> dict:
    """Create a standardised item dict.

    Args:
        source: Short source identifier, e.g. 'ncsc', 'cisa_kev'.
        title: Human-readable headline.
        url: Canonical link to the original content.
        snippet: First ~500 chars of body text for context.
        category: One of advisory, breach, policy, research, bounty, vendor.
        published: ISO-8601 datetime string. Defaults to now if not provided.
        extra: Optional dict of source-specific metadata (CVE IDs, CVSS, etc.)
    """
    if published is None:
        published = datetime.now(timezone.utc).isoformat()

    item_id = hashlib.sha256(url.encode()).hexdigest()[:16]

    item = {
        "id": item_id,
        "source": source,
        "title": title.strip(),
        "url": url.strip(),
        "snippet": (snippet or "")[:500].strip(),
        "category": category,
        "published": published,
    }
    if extra:
        item["extra"] = extra

    return item


def truncate(text: str, max_len: int = 500) -> str:
    """Truncate text to max_len characters, adding ellipsis if needed."""
    if not text or len(text) <= max_len:
        return text or ""
    return text[: max_len - 1].rsplit(" ", 1)[0] + "…"


def parse_feedparser_date(entry) -> str:
    """Extract a published date from a feedparser entry as ISO-8601.

    Falls back to now() if no date field is present or parseable.
    """
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                dt = datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
                return dt.isoformat()
            except (ValueError, OverflowError):
                pass
    return datetime.now(timezone.utc).isoformat()


def strip_utm(url: str) -> str:
    """Remove UTM tracking parameters from a URL."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    clean = "&".join(p for p in parsed.query.split("&") if not p.startswith("utm_"))
    return urlunparse(parsed._replace(query=clean))

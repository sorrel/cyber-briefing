"""Base collector: common schema, ID generation, and shared utilities."""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("cyberbriefing.collectors")


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

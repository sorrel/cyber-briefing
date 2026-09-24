"""OWASP Foundation news collector.

owasp.org was rebuilt on Next.js in September 2026. The old Jekyll
``/feed.xml`` now returns 404 and no replacement feed exists: ``/news`` is
rendered client-side from ``/api/public/news/list``, the same JSON this
collector reads. It is the site's own API rather than a published feed, so it
can change without notice; every failure path below returns an empty list and
the zero-item warning is the only sign the endpoint has moved.

Posts span foundation news, community updates and project releases
(Dependency-Track, Juice Shop), with the category carried into the snippet so
the scorer can tell them apart. Published a few times a month, so checked
every 24 hours.
"""

import logging
import re
from datetime import datetime

import requests

from .base import USER_AGENT_BOT, make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.owasp_news")

OWASP_NEWS_API_URL = "https://owasp.org/api/public/news/list?limit=20"
OWASP_NEWS_BASE_URL = "https://owasp.org/news/"

# The slug is spliced into a URL, so accept only a single plain path segment.
# Anything else (a traversal, an absolute URL, a query) would let the API mint
# a link to another path or host under the OWASP name.
_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parse_date(raw) -> str | None:
    """Return the post's ISO-8601 timestamp, or None if it can't be read."""
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw).isoformat()
    except ValueError:
        logger.debug("Unparseable OWASP news date: %r", raw)
        return None


def collect(config: dict | None = None) -> list[dict]:
    """Collect recent posts from the OWASP news API."""
    logger.info("Collecting OWASP news")

    try:
        resp = requests.get(
            OWASP_NEWS_API_URL,
            timeout=30,
            headers={"User-Agent": USER_AGENT_BOT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        # An unknown path gets the site's 200 HTML shell, so a moved endpoint
        # surfaces here as a parse failure rather than an HTTP error.
        posts = resp.json()["posts"]
        if not isinstance(posts, list):
            raise TypeError(f"'posts' is {type(posts).__name__}, not a list")
    except Exception as e:
        logger.error("Failed to fetch OWASP news: %s", e)
        return []

    items = []
    seen_slugs: set[str] = set()

    for post in posts:
        if not isinstance(post, dict) or post.get("status") != "published":
            continue

        slug = post.get("slug")
        title = _text(post.get("title"))
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug) or not title:
            continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        category = _text(post.get("category"))
        excerpt = _text(post.get("excerpt"))
        snippet = f"{category}: {excerpt}" if category and excerpt else category or excerpt

        items.append(
            make_item(
                source="owasp_news",
                title=title,
                url=OWASP_NEWS_BASE_URL + slug,
                snippet=truncate(snippet),
                category="research",
                published=_parse_date(post.get("published_at")),
            )
        )

    if not items:
        logger.warning(
            "Collected 0 OWASP news posts: the news API has probably changed (%s)",
            OWASP_NEWS_API_URL,
        )
    else:
        logger.info("Collected %d OWASP news posts", len(items))
    return items

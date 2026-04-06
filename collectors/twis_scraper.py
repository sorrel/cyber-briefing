"""This Week in Security newsletter scraper.

Fetches the RSS feed and parses individual stories from the content:encoded
field of each edition. Each story becomes a separate item rather than
ingesting the whole weekly edition as a single blob.

Published weekly on Sundays. Run interval-gated at 168h.
"""

import hashlib
import logging
import re
from time import mktime
from datetime import datetime, timezone

import feedparser
from bs4 import BeautifulSoup

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.twis_scraper")

RSS_URL = "https://this.weekinsecurity.com/past-issues/rss/"

# Titles to skip — sponsorship, housekeeping, community fluff, social shoutouts
_SKIP_PATTERNS = re.compile(
    r"sponsor|advertisement|unsubscribe|subscribe|support this|from the editor|"
    r"this week in security|week in security|^edition|^issue|"
    r"cyber cats|send in your|keep sending|^@\w+",  # social shoutouts, cat posts
    re.IGNORECASE,
)


def collect(config: dict | None = None) -> list[dict]:
    """Fetch recent TWIS editions and return individual stories."""
    logger.info("Fetching This Week in Security RSS")

    try:
        feed = feedparser.parse(RSS_URL)
    except Exception as e:
        logger.error("Failed to fetch TWIS RSS: %s", e)
        return []

    if not feed.entries:
        logger.warning("TWIS RSS returned no entries")
        return []

    all_items: list[dict] = []

    # Parse up to 2 most recent editions (catches first-run and any missed weeks)
    for entry in feed.entries[:2]:
        edition_url = entry.get("link", "")
        published = _parse_date(entry)
        html = _get_content_html(entry)
        if not html:
            logger.warning("No content:encoded found for TWIS entry: %s", edition_url)
            continue

        stories = _parse_edition(html, edition_url, published)
        logger.info("Parsed %d stories from TWIS edition: %s", len(stories), edition_url)
        all_items.extend(stories)

    logger.info("Collected %d total stories from This Week in Security", len(all_items))
    return all_items


def _get_content_html(entry) -> str:
    """Extract full HTML body from a feedparser entry's content:encoded field."""
    # feedparser maps content:encoded → entry.content[0].value
    for block in entry.get("content", []):
        if block.get("value"):
            return block["value"]
    # Fall back to summary if content is absent
    return entry.get("summary", "")


def _parse_edition(html: str, edition_url: str, published: str) -> list[dict]:
    """Parse individual stories from one edition's HTML.

    Two paragraph types:

    Type A — single story with linked headline:
        <p>
          <a href="https://external.story/"><strong><u>Title</u></strong></a>
          <br/> <strong>Source:</strong> Body text…
          <br/> <strong>More:</strong> <a>…</a> | …
        </p>

    Type B — roundup item with bold title and multiple inline links:
        <p>
          <strong>Data breaches: Cisco, Mercor…</strong>
          <br/> Narrative text with inline <a href="…">links</a>…
        </p>
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen_urls: set[str] = set()

    for p in soup.find_all("p"):
        children = [c for c in p.children if getattr(c, "name", None) or str(c).strip()]

        # --- Type A: first meaningful child is an <a> with external href ---
        first_tag = next((c for c in p.children if getattr(c, "name", None)), None)
        if first_tag and first_tag.name == "a":
            story_url = (first_tag.get("href") or "").strip()
            # Skip newsletter self-links and social media posts (not proper stories)
            if not story_url.startswith("http") or _is_non_story_url(story_url):
                continue

            title = first_tag.get_text(strip=True)
            if not title or len(title) < 10 or _SKIP_PATTERNS.search(title):
                continue

            if story_url in seen_urls:
                continue
            seen_urls.add(story_url)

            body = _extract_body(p, title)
            items.append(make_item(
                source="this_week_in_security",
                title=title,
                url=story_url,
                snippet=truncate(body),
                category="news",
                published=published,
            ))
            continue

        # --- Type B: first meaningful child is a <strong> (roundup heading) ---
        if first_tag and first_tag.name == "strong":
            title = first_tag.get_text(strip=True)
            if not title or len(title) < 10 or _SKIP_PATTERNS.search(title):
                continue

            # Synthetic URL: edition URL + anchor slug — stable across runs
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            story_url = f"{edition_url.rstrip('/')}#{slug}"

            if story_url in seen_urls:
                continue
            seen_urls.add(story_url)

            body = _extract_body(p, title)
            items.append(make_item(
                source="this_week_in_security",
                title=title,
                url=story_url,
                snippet=truncate(body),
                category="news",
                published=published,
            ))

    return items


_SOCIAL_DOMAINS = re.compile(
    r"(^|\.)("
    r"twitter\.com|x\.com|bsky\.app|mastodon\.\w+|"
    r"infosec\.exchange|lgbtqia\.space|fosstodon\.org|"
    r"hachyderm\.io|social\.coop|kolektiva\.social"
    r")/",
    re.IGNORECASE,
)


def _is_non_story_url(url: str) -> bool:
    """Return True for URLs that are social posts or newsletter self-links."""
    if "this.weekinsecurity.com" in url:
        return True
    # Generic Mastodon/ActivityPub instances: /@user/ path pattern
    if re.search(r"/(@[\w.]+|users/\w+)/\d+", url):
        return True
    return bool(_SOCIAL_DOMAINS.search(url))


def _extract_body(p, title: str) -> str:
    """Extract body text from a story paragraph, stripping the title and More: lines."""
    raw = p.get_text(separator=" ", strip=True)
    # Remove title from the front (it may appear verbatim)
    if raw.startswith(title):
        raw = raw[len(title):].strip().lstrip(": ").strip()
    # Remove trailing "More: …" links section
    more_match = re.search(r"\bMore\s*:", raw, re.IGNORECASE)
    if more_match:
        raw = raw[:more_match.start()].strip()
    return raw


def _parse_date(entry) -> str:
    """Extract published date from a feedparser entry as ISO-8601."""
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                dt = datetime.fromtimestamp(mktime(parsed), tz=timezone.utc)
                return dt.isoformat()
            except (ValueError, OverflowError):
                pass
    return datetime.now(timezone.utc).isoformat()

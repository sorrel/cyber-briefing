"""ICO enforcement actions scraper.

Scrapes the ICO's enforcement actions page. Run weekly.
"""

import logging

import requests
from bs4 import BeautifulSoup

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.ico_scraper")

ICO_URL = "https://ico.org.uk/action-weve-taken/enforcement/"


def collect(config: dict | None = None) -> list[dict]:
    """Scrape the ICO enforcement actions page."""
    logger.info("Scraping ICO enforcement actions")

    try:
        resp = requests.get(
            ICO_URL,
            timeout=30,
            headers={
                "User-Agent": "CyberBriefingBot/1.0 (personal security briefing tool)"
            },
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch ICO enforcement: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []

    # ICO enforcement pages typically list actions as linked entries
    for link_tag in soup.select("a[href*='/action-weve-taken/enforcement/']"):
        title = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")

        if not title or not href or len(title) < 10:
            continue

        if href.startswith("/"):
            href = "https://ico.org.uk" + href

        # Skip self-referential links to the main enforcement page
        if href.rstrip("/") == ICO_URL.rstrip("/"):
            continue

        parent = link_tag.find_parent(["li", "div", "article", "tr"])
        snippet = ""
        if parent:
            snippet = parent.get_text(strip=True)[:300]

        items.append(
            make_item(
                source="ico",
                title=title,
                url=href,
                snippet=truncate(snippet),
                category="policy",
            )
        )

    # Deduplicate by URL
    seen_urls = set()
    unique_items = []
    for item in items:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique_items.append(item)

    logger.info("Scraped %d ICO enforcement actions", len(unique_items))
    return unique_items

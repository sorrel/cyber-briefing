"""ENISA publications scraper.

ENISA discontinued RSS feeds in 2025 so we scrape their publications
listing page instead. Run at configured intervals (default 24h).
"""

import logging

import requests
from bs4 import BeautifulSoup

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.enisa_scraper")

ENISA_URL = "https://www.enisa.europa.eu/publications"


def collect(config: dict | None = None) -> list[dict]:
    """Scrape the ENISA publications page for recent entries."""
    logger.info("Scraping ENISA publications")

    try:
        resp = requests.get(
            ENISA_URL,
            timeout=30,
            headers={
                "User-Agent": "CyberBriefingBot/1.0 (personal security briefing tool)"
            },
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch ENISA publications: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []

    # ENISA's publications page structure may change — this is best-effort.
    # Look for article/publication cards with links and titles.
    for link_tag in soup.select("a[href*='/publications/']"):
        title = link_tag.get_text(strip=True)
        href = link_tag.get("href", "")

        if not title or not href or len(title) < 10:
            continue

        # Build absolute URL
        if href.startswith("/"):
            href = "https://www.enisa.europa.eu" + href

        # Skip non-publication links (navigation, etc.)
        if "/publications/" not in href:
            continue

        # Try to extract a snippet from surrounding context
        parent = link_tag.find_parent(["article", "div", "li"])
        snippet = ""
        if parent:
            # Look for a description element
            desc = parent.find(["p", "span"], class_=lambda c: c and "desc" in str(c).lower())
            if desc:
                snippet = desc.get_text(strip=True)
            elif parent.get_text(strip=True) != title:
                snippet = parent.get_text(strip=True)[:300]

        items.append(
            make_item(
                source="enisa",
                title=title,
                url=href,
                snippet=truncate(snippet),
                category="policy",
            )
        )

    # Deduplicate by URL (page may have multiple links to same publication)
    seen_urls = set()
    unique_items = []
    for item in items:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique_items.append(item)

    logger.info("Scraped %d ENISA publications", len(unique_items))
    return unique_items

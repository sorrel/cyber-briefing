"""ICO enforcement actions scraper.

Uses the ICO's internal search JSON API rather than scraping HTML,
since the enforcement page loads results client-side via JavaScript.
Runs weekly.
"""

import logging

import requests

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.ico_scraper")

ICO_SEARCH_API = "https://ico.org.uk/api/search"
ICO_ROOT_PAGE_ID = 17222  # data-node-id from the enforcement page
ICO_BASE_URL = "https://ico.org.uk"


def collect(config: dict | None = None) -> list[dict]:
    """Fetch ICO enforcement actions via the search API."""
    logger.info("Scraping ICO enforcement actions")

    try:
        resp = requests.post(
            ICO_SEARCH_API,
            json={"rootPageId": ICO_ROOT_PAGE_ID, "pageNumber": 1, "order": "newest", "filters": []},
            timeout=30,
            headers={
                "User-Agent": "CyberBriefingBot/1.0 (personal security briefing tool)",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch ICO enforcement: %s", e)
        return []

    results = data.get("results", [])
    items = []

    for result in results:
        title = (result.get("title") or "").strip()
        url = (result.get("url") or "").strip()

        if not title or not url:
            continue

        if url.startswith("/"):
            url = ICO_BASE_URL + url

        snippet = (result.get("description") or "").strip()
        published = result.get("date") or None

        items.append(
            make_item(
                source="ico",
                title=title,
                url=url,
                snippet=truncate(snippet),
                category="policy",
                published=published,
            )
        )

    logger.info("Scraped %d ICO enforcement actions", len(items))
    return items

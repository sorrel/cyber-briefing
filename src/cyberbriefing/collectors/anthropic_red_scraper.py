"""Anthropic Frontier Red Team blog scraper.

Scrapes https://red.anthropic.com/ for research publications on AI
security, cyber capabilities, and national security implications.
Published infrequently — checked every 24 hours.
"""

import logging

import requests
from bs4 import BeautifulSoup

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.anthropic_red")

ANTHROPIC_RED_URL = "https://red.anthropic.com/"


def collect(config: dict | None = None) -> list[dict]:
    """Scrape the Anthropic Red Team blog for research posts."""
    logger.info("Scraping Anthropic Red Team blog")

    try:
        resp = requests.get(
            ANTHROPIC_RED_URL,
            timeout=30,
            headers={
                "User-Agent": "CyberBriefingBot/1.0 (personal security briefing tool)"
            },
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch Anthropic Red Team blog: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    seen_urls: set[str] = set()

    # Posts are <a> tags containing <h3> (title) and <p> (description).
    for link_tag in soup.find_all("a", href=True):
        h3 = link_tag.find("h3")
        if not h3:
            continue

        title = h3.get_text(strip=True)
        href = link_tag["href"]

        if not title or not href:
            continue

        # Build absolute URL
        if not href.startswith("http"):
            href = ANTHROPIC_RED_URL.rstrip("/") + "/" + href.lstrip("/")

        if href in seen_urls:
            continue
        seen_urls.add(href)

        # Extract snippet from the <p> sibling inside the link
        snippet = ""
        p_tag = link_tag.find("p")
        if p_tag:
            snippet = p_tag.get_text(strip=True)

        items.append(
            make_item(
                source="anthropic_red",
                title=title,
                url=href,
                snippet=truncate(snippet),
                category="research",
            )
        )

    logger.info("Scraped %d Anthropic Red Team posts", len(items))
    return items

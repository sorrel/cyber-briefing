"""TLDR Infosec newsletter scraper.

Fetches the latest issue URL from the RSS feed, then scrapes the full
story list from the issue page. Each story becomes an individual item
rather than ingesting the whole newsletter as a single blob.

Publishes Monday–Friday. Run daily — the state DB handles dedup.
"""

import logging
import re
from urllib.parse import urlparse, urlunparse

import feedparser
import requests
from bs4 import BeautifulSoup

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.tldr_scraper")

RSS_URL = "https://tldr.tech/api/rss/infosec"

# Map TLDR section names to the briefing's category taxonomy
SECTION_CATEGORIES = {
    "Attacks & Vulnerabilities": "advisory",
    "Strategies & Tactics": "research",
    "Launches & Tools": "research",
    "Miscellaneous": "breach",
    "Quick Links": "breach",
}


def collect(config: dict | None = None) -> list[dict]:
    """Fetch the latest TLDR Infosec issue and return individual stories."""
    issue_url = _get_latest_issue_url()
    if not issue_url:
        return []

    logger.info("Fetching TLDR Infosec issue: %s", issue_url)
    try:
        resp = requests.get(
            issue_url,
            timeout=30,
            headers={"User-Agent": "CyberBriefingBot/1.0 (personal security briefing tool)"},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch TLDR issue: %s", e)
        return []

    items = _parse_issue(resp.text)
    logger.info("Collected %d stories from TLDR Infosec", len(items))
    return items


def _get_latest_issue_url() -> str | None:
    """Poll the RSS feed for the most recent issue URL."""
    try:
        feed = feedparser.parse(RSS_URL)
    except Exception as e:
        logger.error("Failed to fetch TLDR RSS: %s", e)
        return None

    entries = feed.get("entries", [])
    if not entries:
        logger.warning("TLDR RSS returned no entries")
        return None

    return entries[0].get("link", "") or None


def _parse_issue(html: str) -> list[dict]:
    """Parse individual stories from a TLDR issue page.

    Structure:
        <section>
          <header> <h3 class="font-bold">Section Name</h3> </header>
          <article>
            <a class="font-bold" href="url"> <h3>Story Title (N minute read)</h3> </a>
            <div class="newsletter-html"> snippet text </div>
          </article>
          ...
        </section>
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen_urls: set[str] = set()

    # Content sections have a <header> direct child containing the section name
    for section in soup.find_all("section"):
        header = section.find("header", recursive=False)
        if not header:
            continue

        h3 = header.find("h3")
        section_name = h3.get_text(strip=True) if h3 else header.get_text(strip=True)
        category = SECTION_CATEGORIES.get(section_name, "research")

        for article in section.find_all("article", recursive=False):
            # The title link wraps the <h3>: <a class="font-bold"><h3>Title</h3></a>
            link = article.find("a", class_="font-bold", href=True)
            if not link:
                continue

            raw_url = link.get("href", "")
            if not raw_url.startswith("http") or "tldr.tech" in raw_url:
                continue

            raw_title = link.get_text(strip=True)
            if "(Sponsor)" in raw_title:
                continue

            url = _strip_utm(raw_url)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Strip read-time and repo type suffixes from the title
            title = re.sub(r"\s*\(\d+ minute read\)$", "", raw_title).strip()
            title = re.sub(r"\s*\(GitHub Repo\)$", "", title).strip()

            # Snippet lives in the newsletter-html div
            snippet_div = article.find("div", class_="newsletter-html")
            snippet = snippet_div.get_text(strip=True) if snippet_div else ""

            items.append(
                make_item(
                    source="tldr_infosec",
                    title=title,
                    url=url,
                    snippet=truncate(snippet),
                    category=category,
                )
            )

    return items


def _strip_utm(url: str) -> str:
    """Remove UTM tracking parameters from a URL."""
    parsed = urlparse(url)
    if parsed.query:
        clean = "&".join(p for p in parsed.query.split("&") if not p.startswith("utm_"))
        return urlunparse(parsed._replace(query=clean))
    return url

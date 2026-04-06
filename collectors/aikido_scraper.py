"""Aikido Security blog scraper.

Aikido's blog (Webflow CMS) has no RSS feed. This scraper fetches the
blog listing page, which is server-rendered with Finsweet CMS attributes,
and returns the most recent posts. The seen_items dedup in state.db ensures
only new posts are processed on each run.

Blog URL: https://www.aikido.dev/blog
"""

import logging

import requests
from bs4 import BeautifulSoup

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.aikido")

BLOG_URL = "https://www.aikido.dev/blog"
BASE_URL = "https://www.aikido.dev"

# Fetch the top N posts — the listing is sorted newest-first
MAX_POSTS = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}


def collect(config: dict | None = None) -> list[dict]:
    """Fetch recent Aikido blog posts and return them as items."""
    logger.info("Fetching Aikido blog listing")
    try:
        resp = requests.get(BLOG_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch Aikido blog: %s", e)
        return []

    items = _parse_listing(resp.text)
    logger.info("Collected %d posts from Aikido blog", len(items))
    return items


def _parse_listing(html: str) -> list[dict]:
    """Parse blog post items from the Aikido blog listing page.

    Structure (Webflow + Finsweet CMS list):
        <div role="listitem" class="blog_hero_resut_item w-dyn-item">
          <a href="/blog/SLUG" class="blog_hero_result_link w-inline-block">
            <div fs-list-field="title">Post Title</div>
            <div fs-list-field="description">Short description</div>
            <div fs-list-field="summary">Longer summary</div>
            <div fs-list-field="category">Category</div>
          </a>
        </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []

    list_items = soup.find_all("div", role="listitem", class_="blog_hero_resut_item")

    for item_div in list_items[:MAX_POSTS]:
        link = item_div.find("a", class_="blog_hero_result_link", href=True)
        if not link:
            continue

        href = link.get("href", "")
        if not href.startswith("/blog/"):
            continue

        url = BASE_URL + href

        title_el = link.find("div", attrs={"fs-list-field": "title"})
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            continue

        # Prefer description; fall back to summary
        desc_el = link.find("div", attrs={"fs-list-field": "description"})
        if not desc_el or not desc_el.get_text(strip=True):
            desc_el = link.find("div", attrs={"fs-list-field": "summary"})
        snippet = desc_el.get_text(strip=True) if desc_el else ""

        items.append(
            make_item(
                source="aikido",
                title=title,
                url=url,
                snippet=truncate(snippet),
                category="research",
            )
        )

    return items

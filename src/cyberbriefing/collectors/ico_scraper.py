"""ICO enforcement actions scraper.

Uses the ICO's internal search JSON API rather than scraping HTML,
since the enforcement page loads results client-side via JavaScript.
Runs weekly.

On 4 Nov 2026 the API answered some requests with zero results: roughly 1 in
6 at random, uncached, whatever the client, and for a spell every "newest"
request (the site's own default) while "oldest" still returned everything.
One empty answer cost the weekly scrape its whole week. So an empty answer
is retried a bounded number of times, and if "newest" stays empty the last
two pages of "oldest" are read instead, newest first. Results also lost
their ``date`` field; the date now comes from ``filterItemMetaData``
("27 October 2026, Reprimands, ...") or ``createdDateTime``.
"""

import logging
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

from .base import USER_AGENT_BOT, host_matches, make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.ico_scraper")

ICO_SEARCH_API = "https://ico.org.uk/api/search"
ICO_ROOT_PAGE_ID = 17222  # data-node-id from the enforcement page
ICO_BASE_URL = "https://ico.org.uk"

# The fallback reads this many pages from the end of the "oldest" listing.
# At 25 per page that is the newest 50 actions, far more than a week brings.
_FALLBACK_PAGES = 2

# An empty answer is retried, since the API returns one at random. Whole
# seconds between attempts; the worst case (four searches, each empty
# twice) adds 16s to a weekly scrape.
_ATTEMPTS = 3
_RETRY_GAP_SECONDS = 2


def _search(order: str, page: int) -> dict:
    """POST one search request; raises on HTTP or JSON failure."""
    resp = requests.post(
        ICO_SEARCH_API,
        json={"rootPageId": ICO_ROOT_PAGE_ID, "pageNumber": page,
              "order": order, "filters": []},
        timeout=30,
        headers={"User-Agent": USER_AGENT_BOT, "Content-Type": "application/json"},
    )
    resp.raise_for_status()
    return resp.json()


def _search_nonempty(order: str, page: int) -> dict:
    """``_search``, retrying an empty answer up to ``_ATTEMPTS`` times in all."""
    for attempt in range(1, _ATTEMPTS + 1):
        data = _search(order, page)
        if data.get("results"):
            return data
        if attempt < _ATTEMPTS:
            logger.info("ICO search (%s, page %d) came back empty; retrying", order, page)
            time.sleep(_RETRY_GAP_SECONDS)
    return data


def _newest_results() -> list[dict]:
    """Return raw results newest first, via "newest" or the "oldest" fallback."""
    newest = _search_nonempty("newest", 1)
    if newest.get("results"):
        return newest["results"]

    first = _search_nonempty("oldest", 1)
    total_pages = (first.get("pagination") or {}).get("totalPages") or 0
    if total_pages < 1:
        return []
    logger.info('ICO "newest" order returned nothing; reading the end of "oldest"')

    fetched = {1: first}
    results: list[dict] = []
    for page in range(total_pages, max(total_pages - _FALLBACK_PAGES, 0), -1):
        data = fetched.get(page) or _search_nonempty("oldest", page)
        results.extend(reversed(data.get("results") or []))
    return results


def _parse_date(result: dict) -> str | None:
    """ISO-8601 date from the metadata line, else from createdDateTime."""
    meta = (result.get("filterItemMetaData") or "").split(",", 1)[0].strip()
    try:
        return datetime.strptime(meta, "%d %B %Y").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    created = (result.get("createdDateTime") or "").strip()
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def collect(config: dict | None = None) -> list[dict]:
    """Fetch ICO enforcement actions via the search API."""
    logger.info("Scraping ICO enforcement actions")

    try:
        results = _newest_results()
    except Exception as e:
        logger.error("Failed to fetch ICO enforcement: %s", e)
        return []

    items = []
    for result in results:
        title = (result.get("title") or "").strip()
        url = (result.get("url") or "").strip()
        if not title or not url:
            continue

        url = urljoin(ICO_BASE_URL + "/", url)
        if not host_matches(url, "ico.org.uk"):
            continue

        snippet = (result.get("description") or "").strip()
        items.append(
            make_item(
                source="ico",
                title=title,
                url=url,
                snippet=truncate(snippet),
                category="policy",
                published=_parse_date(result),
            )
        )

    logger.info("Scraped %d ICO enforcement actions", len(items))
    return items

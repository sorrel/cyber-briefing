"""CloudSecList newsletter scraper.

Fetches the latest issue by incrementing from the last known issue number.
CloudSecList is a weekly cloud security newsletter. The issue pages require a
browser-like User-Agent — the site returns 403 to default Python requests.

URL pattern: https://cloudseclist.com/issues/issue-NNN/
State:       ~/.cyberbriefing/cloudseclist_state.json  {"last_issue": NNN}
"""

import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.cloudseclist")

BASE_URL = "https://cloudseclist.com/issues/issue-{n}/"
STATE_FILE = Path.home() / ".cyberbriefing" / "cloudseclist_state.json"

# Issue 332 was published 2026-04-05; start from 331 so 332 is picked up on
# the first run.
STARTING_ISSUE = 331

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

# Map CloudSecList section headings to the briefing's category taxonomy
SECTION_CATEGORIES = {
    "This week's articles": "research",
    "Tools": "research",
    "From the cloud providers": "vendor",
}


def collect(config: dict | None = None) -> list[dict]:
    """Fetch any new CloudSecList issues and return individual items."""
    last_issue = _load_last_issue()
    items = []
    highest_fetched = last_issue

    # Try up to 3 issue numbers ahead in case we missed a week
    for issue_n in range(last_issue + 1, last_issue + 4):
        url = BASE_URL.format(n=issue_n)
        logger.info("Trying CloudSecList issue %d", issue_n)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
        except Exception as e:
            logger.error("Failed to fetch CloudSecList issue %d: %s", issue_n, e)
            break

        if resp.status_code == 404:
            logger.info("CloudSecList issue %d not yet published", issue_n)
            break
        elif resp.status_code != 200:
            logger.warning(
                "CloudSecList issue %d returned HTTP %d", issue_n, resp.status_code
            )
            break

        parsed = _parse_issue(resp.text, issue_n)
        logger.info(
            "Collected %d items from CloudSecList issue %d", len(parsed), issue_n
        )
        items.extend(parsed)
        highest_fetched = issue_n

    if highest_fetched > last_issue:
        _save_last_issue(highest_fetched)

    return items


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def _load_last_issue() -> int:
    """Load the last successfully fetched issue number from state file."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return int(data.get("last_issue", STARTING_ISSUE))
        except Exception:
            pass
    return STARTING_ISSUE


def _save_last_issue(n: int) -> None:
    """Persist the last successfully fetched issue number."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"last_issue": n}))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_issue(html: str, issue_n: int) -> list[dict]:
    """Parse individual items from a CloudSecList issue page.

    Structure (repeated per section):
        <h1 class="title-container">Section Name</h1>
        <div>
          <span style="font-size:18px">
            <strong>
              <a href="URL" class="hyperlink">Title</a>
            </strong>
          </span>
          <div dir="ltr">Description snippet</div>
        </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen_urls: set[str] = set()
    current_category = "research"
    issue_date = _extract_date(html)

    for element in soup.find_all(["h1", "div"]):
        # Update current section category when we hit a section heading
        if element.name == "h1" and "title-container" in element.get("class", []):
            section = element.get_text(strip=True)
            current_category = SECTION_CATEGORIES.get(section, "research")
            continue

        if element.name != "div":
            continue

        link = element.find("a", class_="hyperlink", href=True)
        if not link:
            continue

        raw_url = link.get("href", "")
        if not raw_url.startswith("http"):
            continue

        # Skip the newsletter's own site links (logo, nav, footer, etc.)
        # Check only the netloc — every external link has utm_source=cloudseclist.com
        if "cloudseclist.com" in urlparse(raw_url).netloc:
            continue

        url = _strip_utm(raw_url)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = link.get_text(strip=True)
        if not title:
            continue

        snippet_div = element.find("div", dir="ltr")
        snippet = snippet_div.get_text(strip=True) if snippet_div else ""

        items.append(
            make_item(
                source="cloudseclist",
                title=title,
                url=url,
                snippet=truncate(snippet),
                category=current_category,
                published=issue_date,
                extra={"issue": issue_n},
            )
        )

    return items


def _extract_date(html: str) -> str | None:
    """Extract publication date from the <meta name="date"> tag."""
    match = re.search(r'<meta name="date" content="([^"]+)"', html)
    return match.group(1) if match else None


def _strip_utm(url: str) -> str:
    """Remove UTM tracking parameters from a URL."""
    parsed = urlparse(url)
    if parsed.query:
        clean = "&".join(
            p for p in parsed.query.split("&") if not p.startswith("utm_")
        )
        return urlunparse(parsed._replace(query=clean))
    return url

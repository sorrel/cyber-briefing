"""Anthropic Frontier Red Team blog scraper.

Scrapes the Frontier Red Team's publication list for research on AI security,
cyber capabilities and national security implications. Published infrequently,
so checked every 24 hours.

The team's old standalone site, https://red.anthropic.com/, now 301s onto
anthropic.com and its posts are rendered as a ``PublicationList``: one ``<a>``
per post wrapping a ``<time>``, a subject ``<span>`` and a title ``<span>``.
The previous ``<a><h3>`` markup is gone, which is why this collector returned
zero items on every run until 8 Sep 2026.
"""

import logging
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .base import USER_AGENT_BOT, host_matches, make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.anthropic_red")

ANTHROPIC_RED_URL = "https://www.anthropic.com/research/team/frontier-red-team"

# Anthropic build their CSS modules as
# ``PublicationList-module-scss-module__<hash>__title``. The hash is
# regenerated on every deploy; the local name after the final "__" is stable,
# so match on that suffix rather than the whole class.
_TITLE_SUFFIX = "__title"
_SUBJECT_SUFFIX = "__subject"


def _by_class_suffix(tag, suffix: str):
    """Find a descendant whose class list has an entry ending in ``suffix``."""

    def matches(value) -> bool:
        if not value:
            return False
        classes = value if isinstance(value, list) else str(value).split()
        return any(c.endswith(suffix) for c in classes)

    return tag.find(class_=matches)


def _title_by_subtraction(link_tag) -> str:
    """Fallback title: the row's text once the date and subject are removed.

    Used only if the ``__title`` class is ever renamed, so a cosmetic markup
    change degrades the title rather than silently dropping the post.
    """
    row = BeautifulSoup(str(link_tag), "html.parser")
    for stale in row.find_all("time"):
        stale.decompose()
    subject = _by_class_suffix(row, _SUBJECT_SUFFIX)
    if subject is not None:
        subject.decompose()
    return row.get_text(" ", strip=True)


def _parse_date(time_tag) -> str | None:
    """Turn the row's ``<time>`` into ISO-8601, or None if it can't be read."""
    raw = (time_tag.get("datetime") or time_tag.get_text(strip=True) or "").strip()
    if not raw:
        return None

    # "Aug 13, 2026" is what the page renders; a datetime attribute (if the
    # markup ever grows one) is already ISO and passes straight through.
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw).isoformat()
    except ValueError:
        logger.debug("Unparseable Anthropic Red Team date: %r", raw)
        return None


def collect(config: dict | None = None) -> list[dict]:
    """Scrape the Frontier Red Team publication list."""
    logger.info("Scraping Anthropic Red Team blog")

    try:
        resp = requests.get(
            ANTHROPIC_RED_URL,
            timeout=30,
            headers={"User-Agent": USER_AGENT_BOT},
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to fetch Anthropic Red Team blog: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    seen_urls: set[str] = set()

    # A publication row is the only anchor on the page carrying a <time>. That
    # single structural test drops the nav, the featured "Read more" card and
    # the whole footer without depending on any class name.
    for link_tag in soup.find_all("a", href=True):
        time_tag = link_tag.find("time")
        if time_tag is None:
            continue

        title_tag = _by_class_suffix(link_tag, _TITLE_SUFFIX)
        if title_tag is not None:
            title = title_tag.get_text(" ", strip=True)
        else:
            title = _title_by_subtraction(link_tag)

        # Resolve against the response URL so a redirect (red.anthropic.com →
        # anthropic.com) can never leave us minting links on a dead host.
        url = urljoin(getattr(resp, "url", None) or ANTHROPIC_RED_URL, link_tag["href"])

        if not title or not host_matches(url, "anthropic.com"):
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        subject_tag = _by_class_suffix(link_tag, _SUBJECT_SUFFIX)
        subject = subject_tag.get_text(" ", strip=True) if subject_tag else ""
        date_text = time_tag.get_text(" ", strip=True)
        snippet = ", ".join(p for p in (subject, date_text) if p)

        items.append(
            make_item(
                source="anthropic_red",
                title=title,
                url=url,
                snippet=truncate(snippet),
                category="research",
                published=_parse_date(time_tag),
            )
        )

    if not items:
        logger.warning(
            "Scraped 0 Anthropic Red Team posts: the publication list markup "
            "has probably changed again (%s)",
            ANTHROPIC_RED_URL,
        )
    else:
        logger.info("Scraped %d Anthropic Red Team posts", len(items))
    return items

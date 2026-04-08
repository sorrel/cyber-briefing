"""Markdown formatter for the daily briefing.

Converts scored and clustered items into a clean markdown document
suitable for Bear Notes.
"""

import logging
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger("cyberbriefing.delivery.formatter")

TIER_HEADERS = {
    "critical": "## 🔴 Critical — act on these",
    "notable": "## 🟡 Notable — worth reading",
    "radar": "## 📋 On your radar",
    "britain": "## 🇬🇧 Britain",
}

TIER_ORDER = ["critical", "notable", "radar", "britain"]


def format_briefing(
    scored_items: list[dict],
    all_items: list[dict],
    briefing_date: str | None = None,
) -> tuple[str, str, list[str]]:
    """Format scored items into a markdown briefing.

    Args:
        scored_items: Clustered/scored items from the prioritiser.
        all_items: Original items list (for URL lookups).
        briefing_date: Optional date string; defaults to today.

    Returns:
        Tuple of (title, body_markdown, tags).
    """
    if not briefing_date:
        briefing_date = datetime.now(timezone.utc).strftime("%d %B %Y")

    title = f"Cyber Briefing — {briefing_date}"

    # Build a lookup for original items
    item_lookup = {item["id"]: item for item in all_items}

    # Count items and unique sources
    sources = set()
    for item in scored_items:
        original = item_lookup.get(item.get("id", ""), {})
        sources.add(original.get("source", item.get("source", "unknown")))
    source_list = ", ".join(sorted(_pretty_source(s) for s in sources))

    lines = [
        f"*{len(scored_items)} items · Sources: {source_list}*",
        "",
    ]

    # Group items by tier
    tiers: dict[str, list[dict]] = {t: [] for t in TIER_ORDER}
    for item in scored_items:
        tier = item.get("tier", "radar")
        if tier in tiers:
            tiers[tier].append(item)

    # Collect all tags
    all_tags = set()

    for tier_key in TIER_ORDER:
        tier_items = tiers[tier_key]
        if not tier_items:
            continue

        lines.append("---")
        lines.append("")
        lines.append(TIER_HEADERS[tier_key])
        lines.append("")

        for item in tier_items:
            original = item_lookup.get(item.get("id", ""), {})
            item_url = original.get("url", item.get("url", ""))
            item_source = _pretty_source(
                original.get("source", item.get("source", ""))
            )

            summary = item.get("summary", item.get("title", ""))
            annotation = item.get("annotation", "")
            also_covered = item.get("also_covered_by", [])
            item_tags = item.get("tags", [])
            composite = item.get("composite", 0)

            if tier_key == "britain":
                # Headlines-only: headline linked, source name in italics
                if item_url:
                    lines.append(f"- [{summary}]({item_url}) · *{item_source}*")
                else:
                    lines.append(f"- {summary} · *{item_source}*")
                lines.append("")
                all_tags.update(item_tags)
                continue

            # Heading with primary link
            lines.append(f"### {summary}")

            # Source links
            source_links = [f"[{item_source}]({item_url})"]
            for other in also_covered:
                other_name = _pretty_source(other.get("source", ""))
                other_url = other.get("url", "")
                if other_url:
                    source_links.append(f"[{other_name}]({other_url})")
            lines.append(" · ".join(source_links))

            # Annotation
            if annotation:
                lines.append(annotation)

            # Score (subtle, for tuning visibility)
            lines.append(f"*Score: {composite:.1f}*")
            lines.append("")

            all_tags.update(item_tags)

    # If no items at all
    if not any(tiers.values()):
        lines.append("---")
        lines.append("")
        lines.append("*No items above threshold today. Quiet day.*")
        lines.append("")

    # Compose final tags list — cap at 10 topic tags, keeping most common across items
    tag_counts = Counter(all_tags)
    top_tags = [tag for tag, _ in tag_counts.most_common(10)]
    bear_tags = ["security/briefing/daily"]
    for tag in sorted(top_tags):
        bear_tags.append(f"security/briefing/{tag}")

    body = "\n".join(lines)
    return title, body, bear_tags


def _pretty_source(slug: str) -> str:
    """Convert a source slug to a human-readable name."""
    names = {
        "ncsc": "NCSC",
        "ncsc_reports": "NCSC Reports",
        "cisa_kev": "CISA KEV",
        "nvd": "NVD",
        "hackerone": "HackerOne",
        "github_advisories": "GitHub Advisory",
        "the_hacker_news": "The Hacker News",
        "thehackernews": "The Hacker News",
        "portswigger_research": "PortSwigger Research",
        "portswigger_daily_swig": "PortSwigger Daily Swig",
        "krebs_on_security": "Krebs on Security",
        "bleepingcomputer": "BleepingComputer",
        "risky_business": "Risky Business",
        "owasp": "OWASP",
        "enisa": "ENISA",
        "ico": "ICO",
        "commons_library": "Commons Library",
        "uk_parliament": "UK Parliament",
        "aws_security": "AWS Security",
        "azure_updates": "Azure Updates",
        "the_register": "The Register",
        "tldr_infosec": "TLDR Infosec",
        "cloudseclist": "CloudSecList",
        "wiz_blog": "Wiz Blog",
        "snyk_blog": "Snyk Blog",
        "tldrsec": "tl;dr sec",
        "trail_of_bits": "Trail of Bits",
        "ncc_group_research": "NCC Group Research",
        "google_project_zero": "Google Project Zero",
        "aikido": "Aikido Security",
        "this_week_in_security": "This Week in Security",
    }
    return names.get(slug, slug.replace("_", " ").title())

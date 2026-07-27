"""HackerOne Hacktivity collector.

Fetches recently disclosed reports from the HackerOne Hacker API,
filtered to high/critical severity.
"""

import logging
import os

import requests

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.hackerone")

HACKTIVITY_URL = "https://api.hackerone.com/v1/hackers/hacktivity"


def collect(config: dict | None = None) -> list[dict]:
    """Fetch recent hacktivity items from HackerOne."""
    username = os.environ.get("HACKERONE_API_USER", "")
    token = os.environ.get("HACKERONE_API_TOKEN", "")

    if not username or not token:
        logger.warning("HackerOne credentials not configured — skipping")
        return []

    logger.info("Fetching HackerOne hacktivity")

    params = {
        "filter[severity][]": ["critical", "high"],
        "page[size]": 50,
        "sort": "-latest_disclosable_activity_at",
    }

    try:
        resp = requests.get(
            HACKTIVITY_URL,
            params=params,
            auth=(username, token),
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch HackerOne: %s", e)
        return []

    items = []
    for entry in data.get("data", []):
        attrs = entry.get("attributes", {})
        rels = entry.get("relationships", {})

        title_text = attrs.get("title", "Undisclosed report")
        severity = attrs.get("severity_rating", "unknown")
        disclosed = attrs.get("disclosed", False)
        url = attrs.get("url", "")
        bounty = attrs.get("total_awarded_amount")
        submitted = attrs.get("submitted_at", "")
        cwe = attrs.get("cwe", "")

        # Programme info
        program_data = rels.get("program", {}).get("data", {})
        program_attrs = program_data.get("attributes", {})
        program_name = program_attrs.get("name", "Unknown programme")
        program_handle = program_attrs.get("handle", "")

        if not url:
            report_id = entry.get("id", "")
            url = f"https://hackerone.com/reports/{report_id}" if report_id else ""

        title = f"[{severity.upper()}] {title_text} — {program_name}"

        snippet_parts = []
        if cwe:
            snippet_parts.append(f"CWE: {cwe}.")
        if bounty:
            snippet_parts.append(f"Bounty: ${bounty:,.0f}.")
        if disclosed:
            snippet_parts.append("Publicly disclosed.")
        snippet = " ".join(snippet_parts)

        items.append(
            make_item(
                source="hackerone",
                title=title,
                url=url,
                snippet=truncate(snippet),
                category="bounty",
                published=submitted if submitted else None,
                extra={
                    "severity": severity,
                    "program": program_name,
                    "program_handle": program_handle,
                    "bounty": bounty,
                    "cwe": cwe,
                    "disclosed": disclosed,
                },
            )
        )

    logger.info("Collected %d HackerOne items", len(items))
    return items

"""CISA Known Exploited Vulnerabilities (KEV) collector.

Fetches the KEV JSON catalogue and returns entries added in the last 7 days.
On first run the orchestrator's state DB dedup handles the full backlog.
"""

import logging
from datetime import datetime, timedelta, timezone

import requests

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.cisa_kev")

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


def collect(config: dict | None = None) -> list[dict]:
    """Fetch the KEV catalogue and return entries added in the last 7 days.

    On first run (empty state DB) the orchestrator's dedup will filter the
    bulk, so we still return everything to ensure nothing is missed.
    Subsequent runs only process recently added entries, saving time and
    avoiding a 1,500-placeholder SQL query on every daily run.
    """
    logger.info("Fetching CISA KEV catalogue")

    try:
        resp = requests.get(KEV_URL, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch KEV: %s", e)
        return []

    vulnerabilities = data.get("vulnerabilities", [])

    # Filter to entries added in the last 7 days so we don't re-process the
    # full catalogue daily. The state DB dedup handles any edge cases.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).date()
    recent = [
        v for v in vulnerabilities
        if _parse_date(v.get("dateAdded", "")) >= cutoff
    ]

    # If nothing is recent (e.g. first run or a quiet week from CISA)
    # fall back to the full catalogue so nothing is missed.
    if not recent:
        logger.info("No KEV entries in last 7 days — returning full catalogue for dedup")
        recent = vulnerabilities

    items = []
    for vuln in recent:
        item = _parse_vuln(vuln)
        if item:
            items.append(item)

    logger.info("Collected %d KEV entries (dedup handled by orchestrator)", len(items))
    return items


def _parse_date(date_str: str):
    """Parse a YYYY-MM-DD date string to a date object, returning epoch on failure."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return datetime(1970, 1, 1).date()


def _parse_vuln(vuln: dict) -> dict | None:
    """Convert a KEV vulnerability dict to a standard item."""
    cve_id = vuln.get("cveID", "")
    if not cve_id:
        return None

    vendor = vuln.get("vendorProject", "")
    product = vuln.get("product", "")
    name = vuln.get("vulnerabilityName", "")
    description = vuln.get("shortDescription", "")
    date_added = vuln.get("dateAdded", "")
    due_date = vuln.get("dueDate", "")
    action = vuln.get("requiredAction", "")
    ransomware = vuln.get("knownRansomwareCampaignUse", "Unknown")
    notes = vuln.get("notes", "")

    title = f"{cve_id}: {vendor} {product} — {name}"
    url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"

    snippet = description
    if due_date:
        snippet += f" Remediation due: {due_date}."
    if ransomware and ransomware.lower() == "known":
        snippet += " Known ransomware use."

    return make_item(
        source="cisa_kev",
        title=title,
        url=url,
        snippet=truncate(snippet),
        category="advisory",
        published=f"{date_added}T00:00:00Z" if date_added else None,
        extra={
            "cve_id": cve_id,
            "vendor": vendor,
            "product": product,
            "due_date": due_date,
            "ransomware": ransomware,
            "action": action,
            "notes": notes,
        },
    )

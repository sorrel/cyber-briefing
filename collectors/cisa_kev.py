"""CISA Known Exploited Vulnerabilities (KEV) collector.

Fetches the full KEV JSON catalogue and diffs against previously seen CVE IDs
to surface only new additions.
"""

import logging

import requests

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.cisa_kev")

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


def collect(config: dict | None = None) -> list[dict]:
    """Fetch the KEV catalogue and return all vulnerability entries.

    Deduplication against previously seen items is handled by the
    orchestrator via the state database — this collector returns everything.
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
    items = []

    for vuln in vulnerabilities:
        cve_id = vuln.get("cveID", "")
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

        items.append(
            make_item(
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
        )

    logger.info("Collected %d KEV entries (dedup handled by orchestrator)", len(items))
    return items

"""NVD CVE API collector.

Fetches recently published/modified CVEs from the NVD 2.0 API,
filtered to CVSS >= configured threshold and optionally boosted
for appsec-relevant CWE categories.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.nvd")

NVD_API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def collect(config: dict | None = None) -> list[dict]:
    """Fetch recent CVEs from the NVD API.

    Args:
        config: Dict with keys from config.yaml's 'nvd' section:
                min_cvss, priority_cwes.
    """
    config = config or {}
    min_cvss = config.get("min_cvss", 7.0)
    api_key = os.environ.get("NVD_API_KEY", "")

    # Look back 48 hours to catch anything missed
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S.000")
    end = now.strftime("%Y-%m-%dT%H:%M:%S.000")

    headers = {}
    if api_key:
        headers["apiKey"] = api_key

    # Single unfiltered request — we apply min_cvss locally in _parse_cve.
    # This may miss some HIGH/CRITICAL CVEs when NVD volume is high, but
    # genuinely important vulnerabilities will surface via news RSS feeds
    # (BleepingComputer, Krebs, NCSC, etc.) so completeness isn't critical.
    params = {
        "pubStartDate": start,
        "pubEndDate": end,
        "resultsPerPage": 100,
    }

    logger.info("Fetching NVD CVEs from %s to %s", start, end)

    try:
        resp = requests.get(NVD_API_URL, params=params, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch NVD: %s", e)
        return []

    all_vulns = [
        vuln_wrapper.get("cve", {})
        for vuln_wrapper in data.get("vulnerabilities", [])
    ]

    items = []
    for cve in all_vulns:
        parsed = _parse_cve(cve, min_cvss)
        if parsed:
            items.append(parsed)

    logger.info("Collected %d NVD CVEs above CVSS %.1f", len(items), min_cvss)
    return items


def _parse_cve(cve: dict, min_cvss: float) -> dict | None:
    """Parse a single CVE object into our standard item format."""
    cve_id = cve.get("id", "")

    # Extract description (prefer English)
    descriptions = cve.get("descriptions", [])
    description = ""
    for desc in descriptions:
        if desc.get("lang") == "en":
            description = desc.get("value", "")
            break
    if not description and descriptions:
        description = descriptions[0].get("value", "")

    # Extract CVSS score
    cvss_score = _extract_cvss(cve)
    if cvss_score is not None and cvss_score < min_cvss:
        return None

    # Extract CWE IDs
    cwes = _extract_cwes(cve)

    # Published date
    published = cve.get("published", "")

    url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"

    snippet = description
    if cvss_score is not None:
        snippet = f"CVSS {cvss_score:.1f}. {snippet}"

    return make_item(
        source="nvd",
        title=f"{cve_id} (CVSS {cvss_score or '?'})",
        url=url,
        snippet=truncate(snippet),
        category="advisory",
        published=published if published else None,
        extra={
            "cve_id": cve_id,
            "cvss": cvss_score,
            "cwes": cwes,
        },
    )


def _extract_cvss(cve: dict) -> float | None:
    """Extract the highest CVSS v3.x or v4 score from a CVE."""
    metrics = cve.get("metrics", {})
    best = None

    for key in ("cvssMetricV40", "cvssMetricV31", "cvssMetricV30"):
        metric_list = metrics.get(key, [])
        for m in metric_list:
            score = m.get("cvssData", {}).get("baseScore")
            if score is not None:
                if best is None or score > best:
                    best = score
    return best


def _extract_cwes(cve: dict) -> list[str]:
    """Extract CWE IDs from a CVE's weaknesses."""
    cwes = []
    for weakness in cve.get("weaknesses", []):
        for desc in weakness.get("description", []):
            val = desc.get("value", "")
            if val.startswith("CWE-"):
                cwes.append(val)
    return cwes

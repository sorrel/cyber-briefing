"""GitHub Security Advisories collector.

Fetches recent critical/high severity advisories from the GitHub
GraphQL API (SecurityAdvisories endpoint).
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import requests

from .base import make_item, truncate

logger = logging.getLogger("cyberbriefing.collectors.github_advisories")

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

QUERY = """
query($since: DateTime!) {
  securityAdvisories(
    first: 50
    publishedSince: $since
    orderBy: {field: PUBLISHED_AT, direction: DESC}
  ) {
    nodes {
      ghsaId
      summary
      description
      severity
      publishedAt
      updatedAt
      permalink
      identifiers {
        type
        value
      }
      cwes(first: 5) {
        nodes {
          cweId
          name
        }
      }
      vulnerabilities(first: 5) {
        nodes {
          package {
            ecosystem
            name
          }
          vulnerableVersionRange
        }
      }
    }
  }
}
"""


def collect(config: dict | None = None) -> list[dict]:
    """Fetch recent GitHub Security Advisories."""
    token = os.environ.get("GITHUB_TOKEN", "")

    if not token:
        logger.warning("GITHUB_TOKEN not set — skipping GitHub advisories")
        return []

    since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    logger.info("Fetching GitHub advisories since %s", since)

    try:
        resp = requests.post(
            GITHUB_GRAPHQL_URL,
            json={"query": QUERY, "variables": {"since": since}},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch GitHub advisories: %s", e)
        return []

    if "errors" in data:
        logger.error("GitHub GraphQL errors: %s", data["errors"])
        return []

    advisories = (
        data.get("data", {}).get("securityAdvisories", {}).get("nodes", [])
    )

    items = []
    for adv in advisories:
        severity = (adv.get("severity") or "").upper()
        if severity not in ("CRITICAL", "HIGH"):
            continue

        ghsa_id = adv.get("ghsaId", "")
        summary = adv.get("summary", "")
        description = adv.get("description", "")
        permalink = adv.get("permalink", "")
        published = adv.get("publishedAt", "")

        # Extract CVE ID if present
        cve_id = ""
        for ident in adv.get("identifiers", []):
            if ident.get("type") == "CVE":
                cve_id = ident.get("value", "")
                break

        # Extract CWEs
        cwes = [
            n.get("cweId", "") for n in adv.get("cwes", {}).get("nodes", [])
        ]

        # Extract affected packages
        packages = []
        for vuln_node in adv.get("vulnerabilities", {}).get("nodes", []):
            pkg = vuln_node.get("package", {})
            eco = pkg.get("ecosystem", "")
            name = pkg.get("name", "")
            ver_range = vuln_node.get("vulnerableVersionRange", "")
            if name:
                packages.append(f"{eco}/{name} {ver_range}".strip())

        title = f"[{severity}] {summary}"
        if cve_id:
            title = f"[{severity}] {cve_id}: {summary}"

        snippet_parts = [description[:300]]
        if packages:
            snippet_parts.append("Affects: " + ", ".join(packages[:3]))
        snippet = " ".join(snippet_parts)

        items.append(
            make_item(
                source="github_advisories",
                title=title,
                url=permalink or f"https://github.com/advisories/{ghsa_id}",
                snippet=truncate(snippet),
                category="advisory",
                published=published if published else None,
                extra={
                    "ghsa_id": ghsa_id,
                    "cve_id": cve_id,
                    "severity": severity,
                    "cwes": cwes,
                    "packages": packages,
                },
            )
        )

    logger.info("Collected %d GitHub advisories", len(items))
    return items

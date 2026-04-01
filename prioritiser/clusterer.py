"""Story clusterer.

Groups scored items that share a cluster_id and collapses them
into a single entry with "also covered by" links.
"""

import logging

logger = logging.getLogger("cyberbriefing.prioritiser.clusterer")


def cluster_items(scored_items: list[dict], all_items: list[dict]) -> list[dict]:
    """Cluster scored items and collapse duplicates.

    Args:
        scored_items: Items from Claude's scoring response (with cluster_id).
        all_items: Original full items list (for URL lookups).

    Returns:
        Deduplicated list with the top item per cluster and 'also_covered_by'
        links from lower-scoring items in the same cluster.
    """
    # Build a lookup from item ID to original item (for URLs)
    item_lookup = {item["id"]: item for item in all_items}

    # Group by cluster_id
    clusters: dict[str, list[dict]] = {}
    unclustered = []

    for item in scored_items:
        cluster_id = item.get("cluster_id")
        if cluster_id:
            clusters.setdefault(cluster_id, []).append(item)
        else:
            unclustered.append(item)

    # For each cluster, keep the highest-scored item and merge URLs
    result = []
    for cluster_id, cluster_items_list in clusters.items():
        # Sort by composite score descending
        cluster_items_list.sort(
            key=lambda x: x.get("composite", 0), reverse=True
        )
        also_covered = []
        for other in cluster_items_list[1:]:
            other_id = other.get("id", "")
            original = item_lookup.get(other_id, {})
            source_name = original.get("source", other.get("source", ""))
            url = original.get("url", "")
            if url:
                also_covered.append({"source": source_name, "url": url})

        result.append({**cluster_items_list[0], "also_covered_by": also_covered})

    # Add unclustered items
    for item in unclustered:
        result.append({**item, "also_covered_by": []})

    # Sort final list by composite score descending
    result.sort(key=lambda x: x.get("composite", 0), reverse=True)

    logger.info(
        "Clustering: %d items → %d after merging %d clusters",
        len(scored_items),
        len(result),
        len(clusters),
    )

    return result
